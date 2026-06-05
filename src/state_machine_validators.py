"""Concrete Validator implementations for the state machine.

Step 3 of the runway in ``~/.latti/STATE_MACHINE.md``: validators run AFTER
each Operator produces an Observation, returning a ValidationResult that the
Runner can use to block, replan, or pass through.

Validators are NOT Operators. Operators execute actions. Validators grade
the resulting Observations.
"""
from __future__ import annotations

import re
from typing import Callable

from src.agent_state_machine import (
    Action,
    Observation,
    ValidationCheck,
    ValidationResult,
)


class ObservationShapeValidator:
    """Checks the Observation has expected payload keys for known action kinds.

    A minimal post-execution check: did the Operator return an Observation
    whose payload structure matches what downstream code expects? Catches
    silent contract drift between Operators.
    """

    @property
    def name(self) -> str:
        return 'observation_shape'

    def applies_to(self, action: Action) -> bool:
        return action.kind in {'tool_call', 'llm_call', 'validation'}

    def validate(self, action: Action, observation: Observation) -> ValidationResult:
        checks: list[ValidationCheck] = []
        all_passed = True

        # Action-id continuity: the Observation must reference the Action it came from.
        id_match = observation.action_id == action.id
        checks.append(ValidationCheck(
            name='action_id_continuity', passed=id_match,
            evidence=f'obs.action_id={observation.action_id!r} action.id={action.id!r}',
        ))
        if not id_match:
            all_passed = False

        # Per-kind contract: success Observations must have a payload shape we recognize.
        if observation.kind == 'success':
            if action.kind == 'tool_call':
                # tool_call Observations should expose at least one of these keys
                expected_any = {'content', 'ok', 'tool_name'}
                has_one = bool(set(observation.payload.keys()) & expected_any)
                checks.append(ValidationCheck(
                    name='tool_call_payload_shape', passed=has_one,
                    evidence=f'expected any of {sorted(expected_any)}; got keys={sorted(observation.payload.keys())}',
                ))
                if not has_one:
                    all_passed = False
            elif action.kind == 'llm_call':
                expected_any = {'completion', 'content', 'tool_calls', 'finish_reason'}
                has_completion = bool(set(observation.payload.keys()) & expected_any)
                checks.append(ValidationCheck(
                    name='llm_call_has_completion', passed=has_completion,
                    evidence=(
                        f'expected any of {sorted(expected_any)}; '
                        f'got keys={sorted(observation.payload.keys())}'
                    ),
                ))
                if not has_completion:
                    all_passed = False

        # Severity: 'block' if the contract drift is severe enough that the loop
        # should NOT proceed (action_id mismatch is always block). 'warn' for
        # softer issues. 'info' if everything passed.
        if not id_match:
            severity = 'block'
        elif not all_passed:
            severity = 'warn'
        else:
            severity = 'info'

        return ValidationResult(
            action_id=action.id, passed=all_passed,
            checks=tuple(checks), severity=severity,
        )


class BudgetValidator:
    """Blocks the loop when an Observation's cost would exceed remaining budget.

    Reads ``state.budget_remaining_usd`` (passed via the Runner's validate_with
    helper). The Runner is responsible for invoking this with the pre-step
    state so the comparison is correct.
    """

    def __init__(self, max_cost_per_step_usd: float = 1.0) -> None:
        self._max_per_step = max_cost_per_step_usd

    @property
    def name(self) -> str:
        return 'budget'

    def applies_to(self, action: Action) -> bool:
        return True

    def validate(self, action: Action, observation: Observation) -> ValidationResult:
        within = observation.cost_usd <= self._max_per_step
        check = ValidationCheck(
            name='cost_per_step',
            passed=within,
            evidence=f'cost_usd={observation.cost_usd:.4f} max_per_step={self._max_per_step:.4f}',
        )
        return ValidationResult(
            action_id=action.id,
            passed=within,
            checks=(check,),
            severity='block' if not within else 'info',
        )


# High-risk command patterns. A bash command matching one of these AND
# overlapping a NEVER anchor's tokens triggers PRE-DISPATCH BLOCK
# (severity='block') in AnchorViolationValidator.pre_validate. Soft
# overlaps without a high-risk pattern fall through to post-execute
# warn. Static-only patterns (no anchor required) live in
# violates_constitutional_wall — that surface is anchor-agnostic.
_HIGH_RISK_BASH_PATTERNS = (
    # rm -rf rooted at production-style paths (anything outside /tmp,
    # /var/folders, /private/var/folders, ~/scratch, etc.). We match
    # paths starting with /var/lib, /var/log, /etc, /home, /Users,
    # /opt, /System, /Library — common live-data roots.
    re.compile(r'\brm\s+(?:-[a-zA-Z]+\s+)*-?[a-zA-Z]*r[a-zA-Z]*[fF][a-zA-Z]*\s+/(?:var/lib|var/log|etc|home|Users|opt|System|Library)\b'),
    # git push --force / -f targeting main or master.
    re.compile(r'\bgit\s+push\s+(?:--force|-f|-+force-with-lease)\b[^|;&]*\b(?:main|master)\b'),
    # chmod 777 / chmod a+rwx (universal write+exec is rarely intended)
    re.compile(r'\bchmod\s+(?:777|a\+rwx)\b'),
    # dd writing to a raw device path (overwrites disks)
    re.compile(r'\bdd\s+[^|;&]*\bof=/dev/(?!null|stdout|stderr|tty\b)'),
)


class AnchorViolationValidator:
    """Surfaces violations of NEVER: anchored constraints on bash tool calls.

    Anchored messages (mission/correction/never/always prefixes; see
    src/agent_session.py:_should_auto_anchor) survive compaction and stay
    visible to the LLM as context. This validator turns one slice of that
    passive history into ACTIVE governance: when a bash command is
    dispatched, every NEVER: constraint in the session's anchors is
    word-set-overlapped against the command. Above-threshold overlap
    yields severity='warn' with the matched constraint named in the
    evidence — surfacing the violation to the decision log without
    blocking the loop.

    Provider injection: an ``anchors_provider`` callable is supplied at
    construction time (typically a closure over the live session). On
    every validate() call the provider is invoked fresh, so anchors
    added mid-session are picked up without re-instantiating the
    validator. Provider failures are swallowed (validator must never
    crash the runner).

    Smallest meaningful first cut at the user's framing
    "summary as active constraint, not passive history." Future
    expansion: 'block' severity for hard walls (rm -rf /, force-push
    main); LLM-judge for fuzzy matching beyond word overlap; coverage
    of MISSION/CORRECTION/IMPORTANT prefixes (today: only NEVER).
    """

    _NEVER_PREFIX_RE = re.compile(r'(?im)^NEVER:\s*(.+)$')
    # Tokens shorter than this are dropped (`a`, `an`, `is`, `to`...) —
    # they create noise in word-overlap matching.
    _MIN_TOKEN_LEN = 3
    # Minimum overlap to flag. 2 = require at least 2 substantive
    # tokens shared between the anchor's NEVER body and the command.
    _MIN_OVERLAP = 2

    def __init__(self, anchors_provider: Callable[[], list[str]]) -> None:
        self._anchors_provider = anchors_provider

    @property
    def name(self) -> str:
        return 'anchor_violation'

    def applies_to(self, action: Action) -> bool:
        if action.kind != 'tool_call':
            return False
        return action.payload.get('tool_name') == 'bash'

    def pre_validate(self, action: Action) -> ValidationResult | None:
        """Pre-dispatch block check for constitution-grade violations.

        Returns:
          - ValidationResult(severity='block') when the bash command
            matches BOTH a HIGH_RISK_BASH_PATTERN and a NEVER anchor
            whose tokens overlap the command (>=_MIN_OVERLAP).
          - None for everything else — including high-risk-no-anchor
            (violates_constitutional_wall handles that surface) and
            soft-anchor-no-high-risk (post-execute validate emits warn).

        The runner calls this before op.execute. Block-severity result
        causes run_one_step to return an error Observation without
        running the operator — the bash command never executes.
        """
        if not self.applies_to(action):
            return None

        try:
            anchors = self._anchors_provider() or []
        except Exception:
            return None  # provider failure → no block

        command = ''
        args = action.payload.get('arguments')
        if isinstance(args, dict):
            cmd = args.get('command')
            if isinstance(cmd, str):
                command = cmd
        if not command:
            return None

        # Step 1: command must match a high-risk pattern.
        high_risk_hit: re.Pattern | None = None
        for pat in _HIGH_RISK_BASH_PATTERNS:
            if pat.search(command):
                high_risk_hit = pat
                break
        if high_risk_hit is None:
            return None

        # Step 2: at least one NEVER anchor must overlap the command.
        cmd_tokens = self._tokens(command)
        for anchor_text in anchors:
            if not isinstance(anchor_text, str):
                continue
            for match in self._NEVER_PREFIX_RE.finditer(anchor_text):
                constraint = match.group(1).strip()
                if not constraint:
                    continue
                anchor_tokens = self._tokens(constraint)
                overlap = anchor_tokens & cmd_tokens
                if len(overlap) >= self._MIN_OVERLAP:
                    check = ValidationCheck(
                        name='anchor_pre_dispatch_block',
                        passed=False,
                        evidence=(
                            f'high-risk pattern matched ({high_risk_hit.pattern!r}); '
                            f'NEVER: {constraint!r} overlap={sorted(overlap)}'
                        ),
                    )
                    return ValidationResult(
                        action_id=action.id,
                        passed=False,
                        checks=(check,),
                        severity='block',
                    )

        return None

    def validate(self, action: Action, observation: Observation) -> ValidationResult:
        try:
            anchors = self._anchors_provider() or []
        except Exception:
            # Provider failure must not crash the runner. Degrade to pass.
            return self._pass(action, 'anchors_provider raised; skipped')

        command = ''
        args = action.payload.get('arguments')
        if isinstance(args, dict):
            cmd = args.get('command')
            if isinstance(cmd, str):
                command = cmd
        if not command:
            return self._pass(action, 'no command to inspect')

        cmd_tokens = self._tokens(command)
        violations: list[tuple[str, set[str]]] = []
        for anchor_text in anchors:
            if not isinstance(anchor_text, str):
                continue
            for match in self._NEVER_PREFIX_RE.finditer(anchor_text):
                constraint = match.group(1).strip()
                if not constraint:
                    continue
                anchor_tokens = self._tokens(constraint)
                overlap = anchor_tokens & cmd_tokens
                if len(overlap) >= self._MIN_OVERLAP:
                    violations.append((constraint, overlap))

        if not violations:
            return self._pass(action, 'no anchor violations detected')

        evidence_parts: list[str] = []
        for constraint, overlap in violations:
            evidence_parts.append(
                f'NEVER: {constraint!r} overlap={sorted(overlap)}'
            )
        check = ValidationCheck(
            name='anchor_violation',
            passed=False,
            evidence=' | '.join(evidence_parts),
        )
        return ValidationResult(
            action_id=action.id,
            passed=False,
            checks=(check,),
            severity='warn',
        )

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        # Lowercase word tokenization, drop short tokens, drop common
        # filler words. Non-empty intersection is the warning surface.
        words = re.findall(r"[A-Za-z]+", text.lower())
        return {w for w in words if len(w) >= cls._MIN_TOKEN_LEN}

    @staticmethod
    def _pass(action: Action, evidence: str) -> ValidationResult:
        return ValidationResult(
            action_id=action.id, passed=True,
            checks=(ValidationCheck(
                name='anchor_violation', passed=True, evidence=evidence,
            ),),
            severity='info',
        )


class NonEmptyContentValidator:
    """For tool_call Observations, asserts content is non-empty when ok=True.

    Catches a subtle Operator bug: success returned but no content payload.
    """

    @property
    def name(self) -> str:
        return 'non_empty_content'

    def applies_to(self, action: Action) -> bool:
        return action.kind == 'tool_call'

    def validate(self, action: Action, observation: Observation) -> ValidationResult:
        if observation.kind != 'success':
            # Only check success observations
            return ValidationResult(
                action_id=action.id, passed=True,
                checks=(ValidationCheck(name='non_empty_content', passed=True,
                                        evidence='not applicable: observation not success'),),
                severity='info',
            )
        content = observation.payload.get('content')
        ok_flag = observation.payload.get('ok', True)
        if ok_flag is False:
            # ok=False means the tool itself reported failure; not our concern
            return ValidationResult(
                action_id=action.id, passed=True,
                checks=(ValidationCheck(name='non_empty_content', passed=True,
                                        evidence='not applicable: tool reported ok=False'),),
                severity='info',
            )
        non_empty = bool(content and isinstance(content, str) and content.strip())
        return ValidationResult(
            action_id=action.id, passed=non_empty,
            checks=(ValidationCheck(
                name='non_empty_content', passed=non_empty,
                evidence=f'len(content)={len(content) if isinstance(content, str) else 0}',
            ),),
            severity='warn' if not non_empty else 'info',
        )
