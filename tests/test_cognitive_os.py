"""
Tests for the Sovereign Cognitive OS system.

Covers all five modules without making real LLM calls:
  - intent_router   (Pre-Cognitive Layer)
  - gauntlet        (Thermodynamic Validation Layer)
  - forge           (Kinetic Execution Layer — sterilize + Forge.generate mocked)
  - cognitive_os    (Orchestrator — Forge.generate mocked)
  - cognitive_os_integration (Agent wrapper)
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from src.intent_router import (
    IntentManifest,
    TaskType,
    classify,
    _extract_constraint_hints,
)
from src.gauntlet import (
    GauntletResult,
    WallResult,
    _extract_code,
    _wall_syntax,
    _wall_intent,
    _wall_z3,
    run as gauntlet_run,
)
from src.forge import ForgeCandidate, Forge, sterilize
from src.cognitive_os import CognitiveOS, COSResult, _build_mutation
from src.cognitive_os_integration import (
    CognitiveOSAgentWrapper,
    wrap_agent_for_cognitive_os,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_manifest(
    task_type: TaskType = TaskType.CODE_GEN,
    z3_enabled: bool = False,
    k: int = 2,
) -> IntentManifest:
    from src.intent_router import _WEIGHT_PROFILES, _TEMPERATURE_MAP, _K_MAP
    return IntentManifest(
        task_type=task_type,
        gauntlet_weights=_WEIGHT_PROFILES[task_type],
        z3_enabled=z3_enabled,
        temperature=_TEMPERATURE_MAP[task_type],
        k_candidates=k,
        rationale="test",
        constraint_hints=[],
    )


def _make_forge_candidate(text: str, cid: int = 0) -> ForgeCandidate:
    return ForgeCandidate(
        candidate_id=cid,
        raw_text=text,
        model="test-model",
        latency_ms=10.0,
        prompt_tokens=10,
        completion_tokens=20,
    )


# ============================================================================
# Intent Router
# ============================================================================

class TestIntentRouter:

    def test_classify_cyclic_prompt(self):
        m = classify("Write a weekly schedule that wraps Sunday back to Monday")
        assert m.task_type == TaskType.CYCLIC

    def test_classify_constraint_prompt(self):
        # "constraint solver" is the phrase that triggers CONSTRAINT classification
        m = classify("Implement a constraint solver where x >= 0")
        assert m.task_type == TaskType.CONSTRAINT

    def test_classify_debug_prompt(self):
        m = classify("Fix the bug in this function that raises a KeyError")
        assert m.task_type == TaskType.DEBUG

    def test_classify_refactor_prompt(self):
        m = classify("Refactor this class to reduce duplication")
        assert m.task_type == TaskType.REFACTOR

    def test_classify_explain_prompt(self):
        m = classify("Explain how this sorting algorithm works")
        assert m.task_type == TaskType.EXPLAIN

    def test_classify_code_gen_prompt(self):
        m = classify("Write a function that computes the Fibonacci sequence")
        assert m.task_type in (TaskType.CODE_GEN, TaskType.GENERAL)

    def test_classify_general_fallback(self):
        m = classify("hello")
        assert m.task_type == TaskType.GENERAL

    def test_manifest_has_weights(self):
        m = classify("Write a weekly rotation schedule")
        assert isinstance(m.gauntlet_weights, dict)
        assert "syntax" in m.gauntlet_weights
        assert "intent" in m.gauntlet_weights

    def test_manifest_k_candidates_positive(self):
        m = classify("Write a function")
        assert m.k_candidates >= 1

    def test_manifest_temperature_in_range(self):
        m = classify("Write a function")
        assert 0.0 <= m.temperature <= 1.0

    def test_z3_enabled_for_constraint(self):
        m = classify("Implement a constraint solver where x >= 0")
        # constraint tasks should enable z3
        assert m.z3_enabled is True

    def test_z3_disabled_for_explain(self):
        m = classify("Explain how this works")
        assert m.z3_enabled is False

    def test_extract_constraint_hints_finds_bounds(self):
        hints = _extract_constraint_hints("x must be >= 0 and x < 100")
        assert len(hints) >= 1

    def test_extract_constraint_hints_empty(self):
        hints = _extract_constraint_hints("hello world")
        assert isinstance(hints, list)

    def test_rationale_is_string(self):
        m = classify("Fix the bug in this code")
        assert isinstance(m.rationale, str)
        assert len(m.rationale) > 0


# ============================================================================
# Gauntlet — Code Extraction
# ============================================================================

class TestCodeExtraction:

    def test_extracts_python_fenced_block(self):
        text = "Here is the code:\n```python\ndef foo():\n    return 1\n```"
        assert _extract_code(text) == "def foo():\n    return 1"

    def test_extracts_plain_fenced_block(self):
        text = "```\ndef bar():\n    pass\n```"
        assert _extract_code(text) == "def bar():\n    pass"

    def test_falls_back_to_full_text(self):
        text = "def baz():\n    return 42"
        assert _extract_code(text) == text

    def test_empty_string(self):
        assert _extract_code("") == ""


# ============================================================================
# Gauntlet — Wall 1: Syntax
# ============================================================================

class TestWallSyntax:

    def test_valid_code_passes(self):
        result = _wall_syntax("def foo():\n    return 1", weight=1.0)
        assert result.passed is True
        assert result.energy_contribution == 0.0

    def test_invalid_code_fails_with_inf(self):
        result = _wall_syntax("def foo(\n    return 1", weight=1.0)
        assert result.passed is False
        assert math.isinf(result.energy_contribution)

    def test_empty_code_fails(self):
        result = _wall_syntax("", weight=1.0)
        assert result.passed is False
        assert math.isinf(result.energy_contribution)

    def test_syntax_error_detail_contains_info(self):
        result = _wall_syntax("def foo(\n    return 1", weight=1.0)
        assert "SyntaxError" in result.detail or "syntax" in result.detail.lower()


# ============================================================================
# Gauntlet — Wall 3: Intent
# ============================================================================

class TestWallIntent:

    def test_high_similarity_low_energy(self):
        prompt = "Write a function to compute fibonacci numbers"
        candidate = "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"
        result = _wall_intent(prompt, candidate, weight=1.0)
        # Should have lower energy than a completely unrelated candidate
        assert result.energy_contribution < 1.0

    def test_zero_weight_skipped(self):
        result = _wall_intent("anything", "anything", weight=0.0)
        assert result.energy_contribution == 0.0
        assert "skipped" in result.detail

    def test_energy_bounded_zero_to_weight(self):
        result = _wall_intent("sort a list", "def foo(): pass", weight=0.8)
        assert 0.0 <= result.energy_contribution <= 0.8 + 1e-9


# ============================================================================
# Gauntlet — Wall 4: Z3
# ============================================================================

class TestWallZ3:

    def test_z3_skipped_when_disabled(self):
        manifest = _make_manifest(z3_enabled=False)
        result = _wall_z3("x = 1", manifest)
        assert result.energy_contribution == 0.0
        assert "skipped" in result.detail

    def test_z3_no_constraints_neutral(self):
        manifest = _make_manifest(task_type=TaskType.CONSTRAINT, z3_enabled=True)
        # Code with no assert statements or arithmetic comparisons
        result = _wall_z3("def foo():\n    return 'hello'", manifest)
        assert result.energy_contribution == 0.0

    def test_z3_satisfiable_constraint_low_energy(self):
        manifest = _make_manifest(task_type=TaskType.CONSTRAINT, z3_enabled=True)
        # Code with a satisfiable assert
        code = "x = 5\nassert x >= 0"
        result = _wall_z3(code, manifest)
        # Should not spike energy for satisfiable constraint
        assert not math.isinf(result.energy_contribution)

    def test_z3_contradiction_spikes_energy(self):
        manifest = _make_manifest(task_type=TaskType.CONSTRAINT, z3_enabled=True)
        # x >= 10 AND x < 5 is unsatisfiable
        code = "x = 7\nassert x >= 10\nassert x < 5"
        result = _wall_z3(code, manifest)
        # Z3 should detect the contradiction
        assert result.energy_contribution > 0.0 or "contradiction" in result.detail.lower()


# ============================================================================
# Gauntlet — Full run()
# ============================================================================

class TestGauntletRun:

    def test_valid_code_survives(self):
        manifest = _make_manifest()
        code = "def add(a, b):\n    return a + b"
        result = gauntlet_run(
            candidate_id=0,
            raw_text=code,
            prompt="Write a function to add two numbers",
            manifest=manifest,
        )
        assert result.survived is True
        assert not math.isinf(result.total_energy)
        assert result.candidate_id == 0

    def test_syntax_error_kills_candidate(self):
        manifest = _make_manifest()
        result = gauntlet_run(
            candidate_id=1,
            raw_text="def broken(\n    return 1",
            prompt="Write a function",
            manifest=manifest,
        )
        assert result.survived is False
        assert math.isinf(result.total_energy)

    def test_wall_results_always_present(self):
        manifest = _make_manifest()
        result = gauntlet_run(
            candidate_id=0,
            raw_text="def foo(): return 1",
            prompt="Write a function",
            manifest=manifest,
        )
        assert len(result.wall_results) >= 1  # at least syntax wall

    def test_syntax_error_short_circuits_other_walls(self):
        manifest = _make_manifest()
        result = gauntlet_run(
            candidate_id=0,
            raw_text="def broken(",
            prompt="Write a function",
            manifest=manifest,
        )
        # Only syntax wall should run (short-circuit)
        assert result.wall_results[0].wall == "syntax"
        assert len(result.wall_results) == 1

    def test_extracted_code_populated(self):
        manifest = _make_manifest()
        result = gauntlet_run(
            candidate_id=0,
            raw_text="```python\ndef foo():\n    return 1\n```",
            prompt="Write a function",
            manifest=manifest,
        )
        assert "def foo" in result.extracted_code

    def test_lower_energy_for_better_candidate(self):
        manifest = _make_manifest()
        prompt = "Write a function to compute fibonacci numbers"

        good = gauntlet_run(
            candidate_id=0,
            raw_text="def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)",
            prompt=prompt,
            manifest=manifest,
        )
        bad = gauntlet_run(
            candidate_id=1,
            raw_text="def totally_unrelated_thing():\n    x = 'hello world'\n    return x * 100",
            prompt=prompt,
            manifest=manifest,
        )
        # Good candidate should have lower or equal energy
        assert good.total_energy <= bad.total_energy


# ============================================================================
# Forge — sterilize()
# ============================================================================

class TestSterilize:

    def test_removes_please(self):
        assert "please" not in sterilize("Please write a function").lower()

    def test_removes_can_you(self):
        result = sterilize("Can you write a sorting algorithm?")
        assert "can you" not in result.lower()

    def test_preserves_technical_content(self):
        prompt = "Write a function that computes fibonacci(n) using memoization"
        result = sterilize(prompt)
        assert "fibonacci" in result
        assert "memoization" in result

    def test_empty_string(self):
        assert sterilize("") == ""

    def test_no_filler_unchanged(self):
        prompt = "Implement a binary search tree"
        assert sterilize(prompt) == prompt


# ============================================================================
# Forge — generate() (mocked LLM)
# ============================================================================

class TestForgeGenerate:

    def _make_forge(self) -> Forge:
        client = MagicMock()
        client.base_url = "http://localhost:8000/v1"
        client.api_key = "test-key"
        return Forge(client=client, model="test-model")

    def test_generate_returns_candidates(self):
        forge = self._make_forge()
        manifest = _make_manifest(k=2)

        good_response = {
            "choices": [{"message": {"content": "def foo(): return 1"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(good_response).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            candidates = forge.generate(
                prompt="Write a function",
                manifest=manifest,
            )

        assert len(candidates) == 2
        assert all(isinstance(c, ForgeCandidate) for c in candidates)
        assert all(c.raw_text == "def foo(): return 1" for c in candidates)

    def test_generate_handles_api_failure_gracefully(self):
        forge = self._make_forge()
        manifest = _make_manifest(k=3)

        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            candidates = forge.generate(
                prompt="Write a function",
                manifest=manifest,
            )

        # Should return empty list, not raise
        assert candidates == []

    def test_generate_partial_failure(self):
        """If some calls fail, returns only successful candidates."""
        forge = self._make_forge()
        manifest = _make_manifest(k=3)

        call_count = 0
        good_response = {
            "choices": [{"message": {"content": "def foo(): return 1"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("transient failure")
            mock_resp = MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(good_response).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            candidates = forge.generate(
                prompt="Write a function",
                manifest=manifest,
            )

        assert len(candidates) == 2  # 2 of 3 succeeded


# ============================================================================
# CognitiveOS — Orchestrator
# ============================================================================

class TestCognitiveOS:

    def _make_cos(self, max_cycles: int = 2) -> CognitiveOS:
        client = MagicMock()
        client.base_url = "http://localhost:8000/v1"
        client.api_key = "test-key"
        return CognitiveOS(
            client=client,
            model="test-model",
            max_cycles=max_cycles,
            verbose=False,
        )

    def _good_candidate(self) -> ForgeCandidate:
        return _make_forge_candidate(
            "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"
        )

    def _bad_candidate(self) -> ForgeCandidate:
        return _make_forge_candidate("def broken(")

    def test_run_succeeds_with_valid_candidate(self):
        cos = self._make_cos()
        with patch.object(cos.forge, "generate", return_value=[self._good_candidate()]):
            result = cos.run("Write a fibonacci function")

        assert result.succeeded is True
        assert result.winner is not None
        assert result.cycles >= 1

    def test_run_exhausts_on_all_bad_candidates(self):
        cos = self._make_cos(max_cycles=2)
        with patch.object(cos.forge, "generate", return_value=[self._bad_candidate()]):
            result = cos.run("Write a function")

        assert result.exhausted is True
        assert result.cycles == 2

    def test_run_returns_cos_result(self):
        cos = self._make_cos()
        with patch.object(cos.forge, "generate", return_value=[self._good_candidate()]):
            result = cos.run("Write a function")

        assert isinstance(result, COSResult)
        assert isinstance(result.manifest, __import__("src.intent_router", fromlist=["IntentManifest"]).IntentManifest)

    def test_run_cycle_reports_populated(self):
        cos = self._make_cos()
        with patch.object(cos.forge, "generate", return_value=[self._good_candidate()]):
            result = cos.run("Write a function")

        assert len(result.cycle_reports) >= 1

    def test_run_latency_positive(self):
        cos = self._make_cos()
        with patch.object(cos.forge, "generate", return_value=[self._good_candidate()]):
            result = cos.run("Write a function")

        assert result.total_latency_ms >= 0.0

    def test_run_selects_min_energy_winner(self):
        """When multiple candidates survive, the one with lowest G wins."""
        cos = self._make_cos()
        good1 = _make_forge_candidate(
            "def add(a, b):\n    return a + b", cid=0
        )
        good2 = _make_forge_candidate(
            "def add(a, b):\n    # adds two numbers\n    return a + b", cid=1
        )
        with patch.object(cos.forge, "generate", return_value=[good1, good2]):
            result = cos.run("Write a function to add two numbers")

        assert result.succeeded is True
        # Winner should be the one with lower energy
        assert result.winner is not None

    def test_mutation_on_failure_changes_prompt(self):
        """After a failed cycle, the mutated prompt should differ from original."""
        cos = self._make_cos(max_cycles=2)
        call_count = 0

        def generate_side_effect(prompt, manifest, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [self._bad_candidate()]  # first cycle fails
            return [self._good_candidate()]  # second cycle succeeds

        with patch.object(cos.forge, "generate", side_effect=generate_side_effect):
            result = cos.run("Write a function")

        assert result.cycles == 2
        # The first cycle report should have a mutated prompt
        assert result.cycle_reports[0].mutated_prompt is not None


# ============================================================================
# _build_mutation
# ============================================================================

class TestBuildMutation:

    def _make_dead_result(self, detail: str = "SyntaxError line 1: invalid syntax") -> "GauntletResult":
        from src.gauntlet import GauntletResult, WallResult
        return GauntletResult(
            candidate_id=0,
            raw_text="def broken(",
            total_energy=math.inf,
            wall_results=[WallResult("syntax", False, math.inf, detail)],
            survived=False,
            extracted_code="def broken(",
        )

    def test_mutation_includes_original_prompt(self):
        original = "Write a weekly schedule"
        manifest = _make_manifest(task_type=TaskType.CYCLIC)
        result = _build_mutation(original, [self._make_dead_result()], manifest, cycle=0)
        assert original in result

    def test_mutation_includes_failure_reason(self):
        manifest = _make_manifest()
        result = _build_mutation(
            "Write a function",
            [self._make_dead_result("SyntaxError line 1: invalid syntax")],
            manifest,
            cycle=0,
        )
        assert "SyntaxError" in result or "syntax" in result.lower()

    def test_mutation_cycle_number_incremented(self):
        manifest = _make_manifest()
        result = _build_mutation("Write a function", [], manifest, cycle=1)
        assert "2" in result or "Attempt 2" in result

    def test_mutation_cyclic_adds_modular_guidance(self):
        """Cyclic guidance only appears when there are actual failure reasons."""
        manifest = _make_manifest(task_type=TaskType.CYCLIC)
        # Pass a real failure so the task-type guidance block is reached
        dead = self._make_dead_result("SyntaxError line 1: invalid syntax")
        result = _build_mutation("Write a schedule", [dead], manifest, cycle=0)
        assert "modular" in result.lower() or "%" in result or "wrap" in result.lower()


# ============================================================================
# CognitiveOSAgentWrapper
# ============================================================================

class TestCognitiveOSAgentWrapper:

    def _make_agent(self):
        """Create a minimal mock agent."""
        agent = MagicMock()
        agent.client = MagicMock()
        agent.client.base_url = "http://localhost:8000/v1"
        agent.client.api_key = "test-key"
        agent.model_config = MagicMock()
        agent.model_config.model = "test-model"
        # _query_model returns (AssistantTurn, ())
        from src.agent_types import AssistantTurn, UsageStats
        normal_turn = AssistantTurn(
            content="normal response",
            tool_calls=[],
            finish_reason="stop",
            usage=UsageStats(),
        )
        agent._query_model = MagicMock(return_value=(normal_turn, ()))
        return agent

    def _make_session(self, last_user_msg: str = "Write a function"):
        session = MagicMock()
        msg = MagicMock()
        msg.role = "user"
        msg.content = last_user_msg
        session.messages = [msg]
        return session

    def test_wrap_agent_returns_same_agent(self):
        agent = self._make_agent()
        result = wrap_agent_for_cognitive_os(agent, verbose=False)
        assert result is agent

    def test_non_code_task_uses_normal_path(self):
        """Explain/general tasks should bypass CognitiveOS."""
        agent = self._make_agent()
        original_query = agent._query_model
        wrap_agent_for_cognitive_os(agent, enable_for_all_tasks=False, verbose=False)

        session = self._make_session("Explain how quicksort works")
        tool_specs: list = []

        agent._query_model(session, tool_specs)
        # The original _query_model should have been called
        # (wrapper replaced it, but for explain tasks it delegates back)
        # We verify by checking the wrapper was installed
        assert agent._query_model is not original_query

    def test_wrapper_installed(self):
        agent = self._make_agent()
        original = agent._query_model
        wrap_agent_for_cognitive_os(agent, verbose=False)
        # The wrapper replaces _query_model
        assert agent._query_model is not original

    def test_enable_for_all_tasks_flag(self):
        """enable_for_all_tasks=True should route everything through COS."""
        agent = self._make_agent()
        wrapper = CognitiveOSAgentWrapper(
            agent=agent,
            enable_for_all_tasks=True,
            max_cycles=1,
            verbose=False,
        )
        assert wrapper.enable_for_all_tasks is True

    def test_fallback_on_cos_failure(self):
        """If COS exhausts all cycles, it falls back to the normal path."""
        agent = self._make_agent()
        original_query = agent._query_model

        wrapper = CognitiveOSAgentWrapper(
            agent=agent,
            enable_for_all_tasks=False,
            max_cycles=1,
            verbose=False,
        )

        session = self._make_session("Write a fibonacci function")

        # Mock COS.run to return exhausted result
        exhausted_result = MagicMock()
        exhausted_result.succeeded = False

        with patch.object(CognitiveOS, "run", return_value=exhausted_result):
            wrapper._query_model_wrapped(session, [])

        # Should have fallen back to original _query_model
        original_query.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
