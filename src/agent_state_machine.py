"""Typed state-machine objects for the agent loop.

Foundation for the design described in ``~/.latti/STATE_MACHINE.md``: the agent
IS the state machine, the LLM is one transition operator. This module defines
the interfaces; existing modules in ``src/`` (agent_runtime, agent_session,
agent_tools) will be migrated to operate over these typed objects in later
passes. For now this is purely additive — no existing import path changes.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

JSONDict = dict[str, Any]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


TaskStatus = Literal['pending', 'in_progress', 'blocked', 'done', 'abandoned']
GoalStatus = Literal['active', 'done', 'abandoned']
ActionKind = Literal['tool_call', 'llm_call', 'validation', 'wait', 'ask_user']
ObservationKind = Literal['success', 'error', 'partial', 'noop']
Severity = Literal['info', 'warn', 'block']
Verdict = Literal['continue', 'replan', 'escalate', 'done', 'timeout']
DecidedBy = Literal['rule', 'llm', 'human']
MemoryKind = Literal['scar', 'sop', 'lesson', 'decision', 'reference']
FactSource = Literal['user', 'observation', 'memory', 'inferred']


@dataclass(frozen=True)
class Goal:
    """What the user wants achieved. Long-lived. Stable across sessions."""
    id: str
    title: str
    success_criteria: tuple[str, ...] = ()
    created_at: float = field(default_factory=_now)
    owner: str = 'user'
    parent_goal: str | None = None
    status: GoalStatus = 'active'
    completed_at: float | None = None

    @classmethod
    def new(cls, title: str, success_criteria: tuple[str, ...] = (), owner: str = 'user', parent_goal: str | None = None) -> Goal:
        return cls(id=_new_id('goal'), title=title, success_criteria=success_criteria, owner=owner, parent_goal=parent_goal)

    def to_dict(self) -> JSONDict:
        return {'id': self.id, 'title': self.title, 'success_criteria': list(self.success_criteria),
                'created_at': self.created_at, 'owner': self.owner, 'parent_goal': self.parent_goal,
                'status': self.status, 'completed_at': self.completed_at}


@dataclass(frozen=True)
class Task:
    """A unit of work toward a Goal. Decomposable."""
    id: str
    goal_id: str
    description: str
    parent_task: str | None = None
    status: TaskStatus = 'pending'
    created_at: float = field(default_factory=_now)
    completed_at: float | None = None

    @classmethod
    def new(cls, goal_id: str, description: str, parent_task: str | None = None) -> Task:
        return cls(id=_new_id('task'), goal_id=goal_id, description=description, parent_task=parent_task)

    def to_dict(self) -> JSONDict:
        return {'id': self.id, 'goal_id': self.goal_id, 'description': self.description,
                'parent_task': self.parent_task, 'status': self.status,
                'created_at': self.created_at, 'completed_at': self.completed_at}


@dataclass(frozen=True)
class Fact:
    claim: str
    confidence: float
    source: FactSource
    evidence_ref: str | None = None

    def to_dict(self) -> JSONDict:
        return {'claim': self.claim, 'confidence': self.confidence,
                'source': self.source, 'evidence_ref': self.evidence_ref}


@dataclass(frozen=True)
class BeliefState:
    """What the system thinks is true right now."""
    facts: tuple[Fact, ...] = ()
    unresolved_questions: tuple[str, ...] = ()

    def with_fact(self, fact: Fact) -> BeliefState:
        return BeliefState(facts=self.facts + (fact,), unresolved_questions=self.unresolved_questions)

    def with_question(self, q: str) -> BeliefState:
        return BeliefState(facts=self.facts, unresolved_questions=self.unresolved_questions + (q,))

    def to_dict(self) -> JSONDict:
        return {'facts': [f.to_dict() for f in self.facts],
                'unresolved_questions': list(self.unresolved_questions)}


@dataclass(frozen=True)
class Action:
    """What the system intends to do. Declarative."""
    kind: ActionKind
    payload: JSONDict = field(default_factory=dict)
    required_capability: str | None = None
    id: str = field(default_factory=lambda: _new_id('act'))

    def to_dict(self) -> JSONDict:
        return {'id': self.id, 'kind': self.kind, 'payload': dict(self.payload),
                'required_capability': self.required_capability}


@dataclass(frozen=True)
class ToolCall:
    """A concrete invocation of a tool with arguments."""
    tool_name: str
    args: JSONDict
    started_at: float
    finished_at: float | None = None
    raw_result: Any = None
    error: str | None = None

    def to_dict(self) -> JSONDict:
        return {'tool_name': self.tool_name, 'args': dict(self.args),
                'started_at': self.started_at, 'finished_at': self.finished_at,
                'raw_result': self.raw_result, 'error': self.error}


@dataclass(frozen=True)
class Observation:
    """What the system learned from executing an Action."""
    action_id: str
    kind: ObservationKind
    payload: JSONDict = field(default_factory=dict)
    observed_at: float = field(default_factory=_now)
    cost_usd: float = 0.0
    tokens: int | None = None

    def to_dict(self) -> JSONDict:
        return {'action_id': self.action_id, 'kind': self.kind, 'payload': dict(self.payload),
                'observed_at': self.observed_at, 'cost_usd': self.cost_usd, 'tokens': self.tokens}


@dataclass(frozen=True)
class Step:
    """One node of a Plan."""
    id: str
    plan_id: str
    action: Action
    depends_on: tuple[str, ...] = ()
    status: TaskStatus = 'pending'
    expected_observation_shape: str | None = None

    def to_dict(self) -> JSONDict:
        return {'id': self.id, 'plan_id': self.plan_id, 'action': self.action.to_dict(),
                'depends_on': list(self.depends_on), 'status': self.status,
                'expected_observation_shape': self.expected_observation_shape}


@dataclass(frozen=True)
class Plan:
    """An ordered DAG of Steps proposed for a Task. May be revised."""
    id: str
    task_id: str
    steps: tuple[Step, ...] = ()
    created_at: float = field(default_factory=_now)
    revised_from: str | None = None

    @classmethod
    def new(cls, task_id: str, steps: tuple[Step, ...] = (), revised_from: str | None = None) -> Plan:
        return cls(id=_new_id('plan'), task_id=task_id, steps=steps, revised_from=revised_from)

    def to_dict(self) -> JSONDict:
        return {'id': self.id, 'task_id': self.task_id, 'steps': [s.to_dict() for s in self.steps],
                'created_at': self.created_at, 'revised_from': self.revised_from}


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    evidence: str = ''

    def to_dict(self) -> JSONDict:
        return {'name': self.name, 'passed': self.passed, 'evidence': self.evidence}


@dataclass(frozen=True)
class ValidationResult:
    """Did the Observation satisfy the Action's pre/postconditions?"""
    action_id: str
    passed: bool
    checks: tuple[ValidationCheck, ...] = ()
    severity: Severity = 'info'

    def to_dict(self) -> JSONDict:
        return {'action_id': self.action_id, 'passed': self.passed,
                'checks': [c.to_dict() for c in self.checks], 'severity': self.severity}


@dataclass(frozen=True)
class EvaluationResult:
    """After a Step or Plan completes, did it move us toward the Goal?"""
    task_id: str
    score: float
    dimensions: JSONDict = field(default_factory=dict)
    verdict: Verdict = 'continue'
    note: str | None = None

    def to_dict(self) -> JSONDict:
        return {'task_id': self.task_id, 'score': self.score,
                'dimensions': dict(self.dimensions), 'verdict': self.verdict, 'note': self.note}


@dataclass(frozen=True)
class PolicyDecision:
    """The Controller's choice of what to do next, with rationale."""
    at_state_turn_id: str
    chose: Action
    rejected_alternatives: tuple[Action, ...] = ()
    rationale: str = ''
    confidence: float = 0.0
    decided_by: DecidedBy = 'rule'
    decided_at: float = field(default_factory=_now)

    def to_dict(self) -> JSONDict:
        return {'at_state_turn_id': self.at_state_turn_id, 'chose': self.chose.to_dict(),
                'rejected_alternatives': [a.to_dict() for a in self.rejected_alternatives],
                'rationale': self.rationale, 'confidence': self.confidence,
                'decided_by': self.decided_by, 'decided_at': self.decided_at}


@dataclass(frozen=True)
class MemoryRecord:
    """A persisted fact, scar, correction, decision, or session note."""
    id: str
    kind: MemoryKind
    body: str
    last_used: float = field(default_factory=_now)
    source_session_id: str | None = None
    source_turn_id: str | None = None

    @classmethod
    def new(cls, kind: MemoryKind, body: str, source_session_id: str | None = None,
            source_turn_id: str | None = None) -> MemoryRecord:
        return cls(id=_new_id('mem'), kind=kind, body=body,
                   source_session_id=source_session_id, source_turn_id=source_turn_id)

    def to_dict(self) -> JSONDict:
        return {'id': self.id, 'kind': self.kind, 'body': self.body,
                'last_used': self.last_used, 'source_session_id': self.source_session_id,
                'source_turn_id': self.source_turn_id}


@dataclass(frozen=True)
class State:
    """The current world snapshot the controller is reasoning about."""
    turn_id: str
    session_id: str
    beliefs: BeliefState = field(default_factory=BeliefState)
    open_tasks: tuple[Task, ...] = ()
    available_tools: tuple[str, ...] = ()
    runtime: JSONDict = field(default_factory=dict)
    budget_remaining_usd: float = 0.0
    last_observation: Observation | None = None

    @classmethod
    def fresh(cls, session_id: str, available_tools: tuple[str, ...] = (), budget_usd: float = 0.0) -> State:
        return cls(turn_id=_new_id('turn'), session_id=session_id,
                   available_tools=available_tools, budget_remaining_usd=budget_usd)

    def with_runtime(self, runtime: JSONDict) -> State:
        return State(
            turn_id=self.turn_id,
            session_id=self.session_id,
            beliefs=self.beliefs,
            open_tasks=self.open_tasks,
            available_tools=self.available_tools,
            runtime=dict(runtime),
            budget_remaining_usd=self.budget_remaining_usd,
            last_observation=self.last_observation,
        )

    def next_turn(self, observation: Observation, budget_decrement_usd: float = 0.0) -> State:
        return State(
            turn_id=_new_id('turn'),
            session_id=self.session_id,
            beliefs=self.beliefs,
            open_tasks=self.open_tasks,
            available_tools=self.available_tools,
            runtime=dict(self.runtime),
            budget_remaining_usd=max(0.0, self.budget_remaining_usd - budget_decrement_usd),
            last_observation=observation,
        )

    def to_dict(self) -> JSONDict:
        return {'turn_id': self.turn_id, 'session_id': self.session_id,
                'beliefs': self.beliefs.to_dict(),
                'open_tasks': [t.to_dict() for t in self.open_tasks],
                'available_tools': list(self.available_tools),
                'runtime': dict(self.runtime),
                'budget_remaining_usd': self.budget_remaining_usd,
                'last_observation': self.last_observation.to_dict() if self.last_observation else None}


def _fact_from_dict(payload: Any) -> Fact | None:
    if not isinstance(payload, dict):
        return None
    claim = payload.get('claim')
    confidence = payload.get('confidence')
    source = payload.get('source')
    if not isinstance(claim, str) or not isinstance(source, str):
        return None
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    evidence_ref = payload.get('evidence_ref')
    return Fact(
        claim=claim,
        confidence=confidence_value,
        source=source,  # type: ignore[arg-type]
        evidence_ref=evidence_ref if isinstance(evidence_ref, str) else None,
    )


def _belief_state_from_dict(payload: Any) -> BeliefState:
    if not isinstance(payload, dict):
        return BeliefState()
    facts = tuple(
        fact
        for item in payload.get('facts', [])
        if (fact := _fact_from_dict(item)) is not None
    )
    unresolved = tuple(
        item for item in payload.get('unresolved_questions', [])
        if isinstance(item, str)
    )
    return BeliefState(facts=facts, unresolved_questions=unresolved)


def _task_from_dict(payload: Any) -> Task | None:
    if not isinstance(payload, dict):
        return None
    task_id = payload.get('id')
    goal_id = payload.get('goal_id')
    description = payload.get('description')
    if not isinstance(task_id, str) or not isinstance(goal_id, str) or not isinstance(description, str):
        return None
    parent_task = payload.get('parent_task')
    status = payload.get('status', 'pending')
    created_at = payload.get('created_at', _now())
    completed_at = payload.get('completed_at')
    try:
        created_at_value = float(created_at)
    except (TypeError, ValueError):
        created_at_value = _now()
    completed_at_value: float | None
    try:
        completed_at_value = float(completed_at) if completed_at is not None else None
    except (TypeError, ValueError):
        completed_at_value = None
    return Task(
        id=task_id,
        goal_id=goal_id,
        description=description,
        parent_task=parent_task if isinstance(parent_task, str) else None,
        status=status,  # type: ignore[arg-type]
        created_at=created_at_value,
        completed_at=completed_at_value,
    )


def observation_from_dict(payload: Any) -> Observation | None:
    if not isinstance(payload, dict):
        return None
    action_id = payload.get('action_id')
    kind = payload.get('kind')
    if not isinstance(action_id, str) or not isinstance(kind, str):
        return None
    raw_payload = payload.get('payload')
    observed_at = payload.get('observed_at', _now())
    cost_usd = payload.get('cost_usd', 0.0)
    tokens = payload.get('tokens')
    try:
        observed_at_value = float(observed_at)
    except (TypeError, ValueError):
        observed_at_value = _now()
    try:
        cost_usd_value = float(cost_usd)
    except (TypeError, ValueError):
        cost_usd_value = 0.0
    token_value: int | None
    try:
        token_value = int(tokens) if tokens is not None else None
    except (TypeError, ValueError):
        token_value = None
    return Observation(
        action_id=action_id,
        kind=kind,  # type: ignore[arg-type]
        payload=dict(raw_payload) if isinstance(raw_payload, dict) else {},
        observed_at=observed_at_value,
        cost_usd=cost_usd_value,
        tokens=token_value,
    )


def state_from_dict(payload: Any) -> State | None:
    if not isinstance(payload, dict):
        return None
    turn_id = payload.get('turn_id')
    session_id = payload.get('session_id')
    if not isinstance(turn_id, str) or not isinstance(session_id, str):
        return None
    budget_remaining_usd = payload.get('budget_remaining_usd', 0.0)
    try:
        budget_value = float(budget_remaining_usd)
    except (TypeError, ValueError):
        budget_value = 0.0
    available_tools = tuple(
        item for item in payload.get('available_tools', [])
        if isinstance(item, str)
    )
    runtime = dict(payload.get('runtime', {})) if isinstance(payload.get('runtime'), dict) else {}
    open_tasks = tuple(
        task
        for item in payload.get('open_tasks', [])
        if (task := _task_from_dict(item)) is not None
    )
    return State(
        turn_id=turn_id,
        session_id=session_id,
        beliefs=_belief_state_from_dict(payload.get('beliefs')),
        open_tasks=open_tasks,
        available_tools=available_tools,
        runtime=runtime,
        budget_remaining_usd=budget_value,
        last_observation=observation_from_dict(payload.get('last_observation')),
    )


# ---- Operator protocol -----------------------------------------------------
# The Operator is the unified interface for anything that executes an Action
# and returns an Observation. Tool calls, LLM calls, validators, and ask-user
# all become Operator subtypes. The Controller dispatches over them.

@runtime_checkable
class Operator(Protocol):
    """Anything that can execute an Action and return an Observation."""

    @property
    def kind(self) -> ActionKind: ...

    def can_handle(self, action: Action) -> bool: ...

    def execute(self, action: Action, state: State) -> Observation: ...


# ---- Validator protocol ----------------------------------------------------
# A Validator runs AFTER an Operator produces an Observation. It checks that
# the Observation satisfies the Action's preconditions and postconditions.
# Validators are NOT Operators — they don't execute Actions, they grade them.

@runtime_checkable
class Validator(Protocol):
    """Post-Observation check returning a ValidationResult."""

    @property
    def name(self) -> str: ...

    def applies_to(self, action: Action) -> bool: ...

    def validate(self, action: Action, observation: Observation) -> ValidationResult: ...


# ---- Evaluator protocol ----------------------------------------------------
# An Evaluator scores progress toward the goal and returns an EvaluationResult
# with a verdict. The runner uses the verdict to decide whether to continue,
# replan, escalate, or terminate. Verdict precedence (most-severe wins) is:
# timeout > escalate > done > replan > continue.

@runtime_checkable
class Evaluator(Protocol):
    """Post-step check returning an EvaluationResult with a verdict."""

    @property
    def name(self) -> str: ...

    def evaluate(self, state: State, goal: Goal | None = None) -> EvaluationResult: ...


# ---- Controller protocol ---------------------------------------------------
# A Controller picks the next Action given the current State. It returns a
# typed PolicyDecision (not a bare Action) so the rationale + decided_by
# metadata are recorded with the choice. Rule-based controllers fire on
# known-shape transitions; LLM controllers handle ambiguity. Compose via
# FallbackController(primary, fallback).
#
# Returning ``None`` from pick() signals "no Action — halt the loop."

@runtime_checkable
class Controller(Protocol):
    """Picks the next Action given a State. Returns PolicyDecision or None."""

    @property
    def name(self) -> str: ...

    def pick(self, state: State, goal: Goal | None = None) -> PolicyDecision | None: ...


# Verdict precedence — most-severe-wins. The runner combines verdicts from
# multiple evaluators by picking the highest-precedence one.
_VERDICT_PRECEDENCE: dict[Verdict, int] = {
    'continue': 0,
    'replan':   1,
    'done':     2,
    'escalate': 3,
    'timeout':  4,
}


def combine_verdicts(verdicts: tuple[Verdict, ...]) -> Verdict:
    """Pick the most-severe verdict. Empty tuple → 'continue'."""
    if not verdicts:
        return 'continue'
    return max(verdicts, key=lambda v: _VERDICT_PRECEDENCE.get(v, 0))


# ---- Constitutional walls --------------------------------------------------
# These are NEVER decided by the LLM. Hard-coded operators only.

CONSTITUTIONAL_WALLS: tuple[str, ...] = (
    'never_delete_production_data',
    'never_commit_secrets',
    'never_force_push_main',
    'never_silently_swallow_errors',
    'never_let_performance_replace_function',
    'never_let_live_subsystem_die_silently',
)


import re as _re

# Concrete wall-check regexes. Compiled at module load.
_FORCE_PUSH_MAIN = _re.compile(
    r'git\s+push\s+(--force|-f)\b.*\b(main|master)\b'
    r'|git\s+push\s+.*\b(main|master)\b\s+(--force|-f)\b',
    _re.IGNORECASE,
)
_SECRET_PATTERNS = (
    _re.compile(r'\bsk-(ant|proj|or|live|test)-[A-Za-z0-9_\-]{8,}'),
    _re.compile(r'\bghp_[A-Za-z0-9]{20,}'),
    _re.compile(r'\bAKIA[0-9A-Z]{16,}'),
    _re.compile(r'\bxoxb-[A-Za-z0-9\-]{20,}'),
    _re.compile(r'-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE) (PRIVATE )?KEY-----'),
)
# rm -rf with a path that's clearly system or production root.
_DESTROY_ROOT = _re.compile(
    r'\brm\s+(-r[fF]?|-fr|-rf)\s+/(?!tmp\b|var/tmp\b|home/[^/\s]+/(?:Downloads|Desktop|tmp))',
)
# git config / cred manipulation in bash.
_GIT_CONFIG_MUT = _re.compile(
    r'git\s+config\s+(--global|--system)\s+(user\.|credential\.|core\.askPass|http\..*\.helper)',
    _re.IGNORECASE,
)


def _payload_text(payload: dict) -> str:
    """Flatten payload dict into a single searchable string for regex checks.

    Conservatively concatenates string values at any nesting depth. Non-strings
    are coerced via str() so numeric/JSON serialization edges are caught too.
    """
    parts: list[str] = []

    def walk(obj):
        if isinstance(obj, str):
            parts.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)
        else:
            parts.append(str(obj))

    walk(payload)
    return '\n'.join(parts)


def violates_constitutional_wall(action: Action) -> str | None:
    """Return the wall name violated by this action, or None.

    Implemented checks (extend by adding more regex patterns above):
      - never_force_push_main: ``git push --force ... main`` (or master)
      - never_commit_secrets: known secret-token formats in any payload value
      - never_delete_production_data: ``rm -rf /...`` rooted at system paths
      - never_silently_swallow_errors: git config of credential helpers, etc.

    Returns the FIRST wall hit (deterministic order). Other walls
    (performance-replaces-function, dead-subsystem) are context-dependent
    and remain unenforced here — they belong upstream of the action.
    """
    text = _payload_text(action.payload)

    if _FORCE_PUSH_MAIN.search(text):
        return 'never_force_push_main'

    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return 'never_commit_secrets'

    if _DESTROY_ROOT.search(text):
        return 'never_delete_production_data'

    if _GIT_CONFIG_MUT.search(text):
        return 'never_silently_swallow_errors'

    return None
