from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    BudgetConfig,
    ModelConfig,
    ModelPricing,
    OutputSchemaConfig,
    UsageStats,
)
from src.session_store import (
    StoredAgentSession,
    StoredSession,
    _deserialize_output_schema,
    _optional_float,
    _optional_int,
    deserialize_model_config,
    deserialize_runtime_config,
    load_agent_session,
    load_session,
    save_agent_session,
    save_session,
    serialize_model_config,
    serialize_runtime_config,
    usage_from_payload,
)


class TestStoredSessionRoundTrip(unittest.TestCase):
    """save_session then load_session preserves all fields."""

    def test_round_trip(self) -> None:
        session = StoredSession(
            session_id='abc-123',
            messages=('hello', 'world', 'foo'),
            input_tokens=100,
            output_tokens=200,
        )
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            save_session(session, directory=directory)
            loaded = load_session('abc-123', directory=directory)

        self.assertEqual(loaded.session_id, session.session_id)
        self.assertEqual(loaded.messages, session.messages)
        self.assertEqual(loaded.input_tokens, session.input_tokens)
        self.assertEqual(loaded.output_tokens, session.output_tokens)

    def test_round_trip_empty_messages(self) -> None:
        session = StoredSession(
            session_id='empty',
            messages=(),
            input_tokens=0,
            output_tokens=0,
        )
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            save_session(session, directory=directory)
            loaded = load_session('empty', directory=directory)

        self.assertEqual(loaded.messages, ())
        self.assertEqual(loaded.input_tokens, 0)


class TestStoredAgentSessionRoundTrip(unittest.TestCase):
    """save_agent_session then load_agent_session preserves all fields."""

    def _make_session(self, **overrides: object) -> StoredAgentSession:
        defaults: dict = {
            'session_id': 'agent-001',
            'model_config': {'model': 'gpt-4', 'temperature': 0.5},
            'runtime_config': {'cwd': '/home/user', 'max_turns': 20},
            'system_prompt_parts': ('You are helpful.',),
            'user_context': {'lang': 'en'},
            'system_context': {'os': 'linux'},
            'messages': ({'role': 'user', 'content': 'hi'},),
            'turns': 3,
            'tool_calls': 7,
            'usage': {'input_tokens': 500, 'output_tokens': 300},
            'total_cost_usd': 0.05,
            'file_history': ({'file': 'a.py', 'action': 'edit'},),
            'budget_state': {'remaining': 100},
            'plugin_state': {'key': 'value'},
            'typed_state': {'session_id': 'agent-001', 'turn_id': 'turn_1'},
            'scratchpad_directory': '/scratch/pad',
        }
        defaults.update(overrides)
        return StoredAgentSession(**defaults)

    def test_round_trip_all_fields(self) -> None:
        session = self._make_session()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            save_agent_session(session, directory=directory)
            loaded = load_agent_session('agent-001', directory=directory)

        self.assertEqual(loaded.session_id, session.session_id)
        self.assertEqual(loaded.model_config, session.model_config)
        self.assertEqual(loaded.runtime_config, session.runtime_config)
        self.assertEqual(loaded.system_prompt_parts, session.system_prompt_parts)
        self.assertEqual(loaded.user_context, session.user_context)
        self.assertEqual(loaded.system_context, session.system_context)
        self.assertEqual(loaded.messages, session.messages)
        self.assertEqual(loaded.turns, session.turns)
        self.assertEqual(loaded.tool_calls, session.tool_calls)
        self.assertEqual(loaded.usage, session.usage)
        self.assertAlmostEqual(loaded.total_cost_usd, session.total_cost_usd)
        self.assertEqual(loaded.file_history, session.file_history)
        self.assertEqual(loaded.budget_state, session.budget_state)
        self.assertEqual(loaded.plugin_state, session.plugin_state)
        self.assertEqual(loaded.typed_state, session.typed_state)
        self.assertEqual(loaded.scratchpad_directory, session.scratchpad_directory)

    def test_round_trip_no_scratchpad(self) -> None:
        session = self._make_session(scratchpad_directory=None)
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            save_agent_session(session, directory=directory)
            loaded = load_agent_session('agent-001', directory=directory)

        self.assertIsNone(loaded.scratchpad_directory)

    def test_load_filters_non_dict_messages(self) -> None:
        """Non-dict entries in messages list are filtered out on load."""
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            path = directory / 'mixed.json'
            data = {
                'session_id': 'mixed',
                'model_config': {},
                'runtime_config': {'cwd': '/'},
                'system_prompt_parts': [],
                'user_context': {},
                'system_context': {},
                'messages': [
                    {'role': 'user', 'content': 'hi'},
                    'not a dict',
                    42,
                    None,
                    {'role': 'assistant', 'content': 'hey'},
                ],
                'turns': 0,
                'tool_calls': 0,
                'usage': {},
                'total_cost_usd': 0.0,
                'file_history': [],
                'budget_state': {},
                'plugin_state': {},
            }
            path.write_text(json.dumps(data))
            loaded = load_agent_session('mixed', directory=directory)

        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[0]['role'], 'user')
        self.assertEqual(loaded.messages[1]['role'], 'assistant')

    def test_load_defaults_for_missing_optional_fields(self) -> None:
        """Missing optional fields get sensible defaults."""
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            path = directory / 'minimal.json'
            data = {
                'session_id': 'minimal',
                'model_config': {},
                'runtime_config': {'cwd': '/'},
                'system_prompt_parts': [],
                'user_context': {},
                'system_context': {},
                'messages': [],
                'turns': 1,
                'tool_calls': 2,
            }
            path.write_text(json.dumps(data))
            loaded = load_agent_session('minimal', directory=directory)

        self.assertEqual(loaded.usage, {})
        self.assertAlmostEqual(loaded.total_cost_usd, 0.0)
        self.assertEqual(loaded.file_history, ())
        self.assertEqual(loaded.budget_state, {})
        self.assertEqual(loaded.plugin_state, {})
        self.assertEqual(loaded.typed_state, {})
        self.assertIsNone(loaded.scratchpad_directory)

    def test_load_non_dict_budget_state_defaults_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            path = directory / 'bad-budget.json'
            data = {
                'session_id': 'bad-budget',
                'model_config': {},
                'runtime_config': {'cwd': '/'},
                'system_prompt_parts': [],
                'user_context': {},
                'system_context': {},
                'messages': [],
                'turns': 0,
                'tool_calls': 0,
                'budget_state': 'not-a-dict',
                'plugin_state': 123,
            }
            path.write_text(json.dumps(data))
            loaded = load_agent_session('bad-budget', directory=directory)

        self.assertEqual(loaded.budget_state, {})
        self.assertEqual(loaded.plugin_state, {})


class TestModelConfigSerialization(unittest.TestCase):
    """serialize_model_config + deserialize_model_config round-trip."""

    def test_round_trip_preserves_pricing(self) -> None:
        pricing = ModelPricing(
            input_cost_per_million_tokens_usd=3.0,
            output_cost_per_million_tokens_usd=15.0,
            cache_creation_input_cost_per_million_tokens_usd=1.5,
            cache_read_input_cost_per_million_tokens_usd=0.5,
        )
        config = ModelConfig(
            model='claude-3-sonnet',
            base_url='https://api.example.com/v1',
            api_key='sk-test-key',
            temperature=0.7,
            timeout_seconds=60.0,
            pricing=pricing,
        )
        payload = serialize_model_config(config)
        restored = deserialize_model_config(payload)

        self.assertEqual(restored.model, config.model)
        self.assertEqual(restored.base_url, config.base_url)
        self.assertEqual(restored.api_key, config.api_key)
        self.assertAlmostEqual(restored.temperature, config.temperature)
        self.assertAlmostEqual(restored.timeout_seconds, config.timeout_seconds)
        self.assertAlmostEqual(
            restored.pricing.input_cost_per_million_tokens_usd,
            pricing.input_cost_per_million_tokens_usd,
        )
        self.assertAlmostEqual(
            restored.pricing.output_cost_per_million_tokens_usd,
            pricing.output_cost_per_million_tokens_usd,
        )
        self.assertAlmostEqual(
            restored.pricing.cache_creation_input_cost_per_million_tokens_usd,
            pricing.cache_creation_input_cost_per_million_tokens_usd,
        )
        self.assertAlmostEqual(
            restored.pricing.cache_read_input_cost_per_million_tokens_usd,
            pricing.cache_read_input_cost_per_million_tokens_usd,
        )

    def test_deserialize_defaults_for_missing_fields(self) -> None:
        payload = {'model': 'gpt-4'}
        config = deserialize_model_config(payload)

        self.assertEqual(config.model, 'gpt-4')
        self.assertEqual(config.base_url, 'http://127.0.0.1:8000/v1')
        self.assertEqual(config.api_key, 'local-token')
        self.assertAlmostEqual(config.temperature, 0.0)
        self.assertAlmostEqual(config.timeout_seconds, 120.0)
        self.assertAlmostEqual(config.pricing.input_cost_per_million_tokens_usd, 0.0)
        self.assertAlmostEqual(config.pricing.output_cost_per_million_tokens_usd, 0.0)

    def test_deserialize_with_non_dict_pricing(self) -> None:
        payload = {'model': 'test', 'pricing': 'invalid'}
        config = deserialize_model_config(payload)
        self.assertAlmostEqual(config.pricing.input_cost_per_million_tokens_usd, 0.0)

    def test_deserialize_with_none_pricing(self) -> None:
        payload = {'model': 'test', 'pricing': None}
        config = deserialize_model_config(payload)
        self.assertEqual(config.pricing, ModelPricing())


class TestRuntimeConfigSerialization(unittest.TestCase):
    """serialize_runtime_config + deserialize_runtime_config round-trip."""

    def test_round_trip_preserves_all(self) -> None:
        config = AgentRuntimeConfig(
            cwd=Path('/home/user/project'),
            max_turns=25,
            command_timeout_seconds=45.0,
            max_output_chars=8000,
            stream_model_responses=True,
            auto_snip_threshold_tokens=5000,
            auto_compact_threshold_tokens=10000,
            compact_preserve_messages=6,
            permissions=AgentPermissions(
                allow_file_write=True,
                allow_shell_commands=True,
                allow_destructive_shell_commands=False,
            ),
            additional_working_directories=(Path('/extra/dir'),),
            disable_claude_md_discovery=True,
            budget_config=BudgetConfig(
                max_total_tokens=100000,
                max_input_tokens=50000,
                max_output_tokens=30000,
                max_reasoning_tokens=20000,
                max_total_cost_usd=5.0,
                max_tool_calls=100,
                max_delegated_tasks=10,
                max_model_calls=200,
                max_session_turns=50,
            ),
            output_schema=OutputSchemaConfig(
                name='test_schema',
                schema={'type': 'object', 'properties': {'answer': {'type': 'string'}}},
                strict=True,
            ),
            session_directory=Path('/sessions'),
            scratchpad_root=Path('/scratch'),
        )
        payload = serialize_runtime_config(config)
        restored = deserialize_runtime_config(payload)

        self.assertEqual(restored.cwd, config.cwd.resolve())
        self.assertEqual(restored.max_turns, 25)
        self.assertAlmostEqual(restored.command_timeout_seconds, 45.0)
        self.assertEqual(restored.max_output_chars, 8000)
        self.assertTrue(restored.stream_model_responses)
        self.assertEqual(restored.auto_snip_threshold_tokens, 5000)
        self.assertEqual(restored.auto_compact_threshold_tokens, 10000)
        self.assertEqual(restored.compact_preserve_messages, 6)
        self.assertTrue(restored.permissions.allow_file_write)
        self.assertTrue(restored.permissions.allow_shell_commands)
        self.assertFalse(restored.permissions.allow_destructive_shell_commands)
        self.assertTrue(restored.disable_claude_md_discovery)

        self.assertEqual(restored.budget_config.max_total_tokens, 100000)
        self.assertEqual(restored.budget_config.max_input_tokens, 50000)
        self.assertEqual(restored.budget_config.max_output_tokens, 30000)
        self.assertEqual(restored.budget_config.max_reasoning_tokens, 20000)
        self.assertAlmostEqual(restored.budget_config.max_total_cost_usd, 5.0)
        self.assertEqual(restored.budget_config.max_tool_calls, 100)
        self.assertEqual(restored.budget_config.max_delegated_tasks, 10)
        self.assertEqual(restored.budget_config.max_model_calls, 200)
        self.assertEqual(restored.budget_config.max_session_turns, 50)

        self.assertIsNotNone(restored.output_schema)
        assert restored.output_schema is not None
        self.assertEqual(restored.output_schema.name, 'test_schema')
        self.assertEqual(restored.output_schema.schema, config.output_schema.schema)
        self.assertTrue(restored.output_schema.strict)

    def test_round_trip_none_output_schema(self) -> None:
        config = AgentRuntimeConfig(
            cwd=Path('/home/user'),
            output_schema=None,
        )
        payload = serialize_runtime_config(config)
        restored = deserialize_runtime_config(payload)
        self.assertIsNone(restored.output_schema)

    def test_deserialize_defaults_for_missing_fields(self) -> None:
        payload = {'cwd': '/home/user'}
        config = deserialize_runtime_config(payload)

        self.assertEqual(config.max_turns, 12)
        self.assertAlmostEqual(config.command_timeout_seconds, 30.0)
        self.assertEqual(config.max_output_chars, 12000)
        self.assertFalse(config.stream_model_responses)
        self.assertIsNone(config.auto_snip_threshold_tokens)
        self.assertIsNone(config.auto_compact_threshold_tokens)
        self.assertEqual(config.compact_preserve_messages, 4)
        self.assertFalse(config.permissions.allow_file_write)
        self.assertFalse(config.permissions.allow_shell_commands)
        self.assertFalse(config.permissions.allow_destructive_shell_commands)
        self.assertEqual(config.additional_working_directories, ())
        self.assertFalse(config.disable_claude_md_discovery)
        self.assertIsNone(config.budget_config.max_total_tokens)
        self.assertIsNone(config.output_schema)

    def test_deserialize_non_dict_permissions(self) -> None:
        payload = {'cwd': '/home', 'permissions': 'invalid'}
        config = deserialize_runtime_config(payload)
        self.assertFalse(config.permissions.allow_file_write)

    def test_deserialize_non_dict_budget_config(self) -> None:
        payload = {'cwd': '/home', 'budget_config': 42}
        config = deserialize_runtime_config(payload)
        self.assertIsNone(config.budget_config.max_total_tokens)


class TestUsageFromPayload(unittest.TestCase):
    """usage_from_payload correctly maps fields including defaults."""

    def test_full_payload(self) -> None:
        payload = {
            'input_tokens': 1000,
            'output_tokens': 500,
            'cache_creation_input_tokens': 200,
            'cache_read_input_tokens': 100,
            'reasoning_tokens': 50,
        }
        usage = usage_from_payload(payload)
        self.assertEqual(usage.input_tokens, 1000)
        self.assertEqual(usage.output_tokens, 500)
        self.assertEqual(usage.cache_creation_input_tokens, 200)
        self.assertEqual(usage.cache_read_input_tokens, 100)
        self.assertEqual(usage.reasoning_tokens, 50)

    def test_partial_payload_uses_defaults(self) -> None:
        payload = {'input_tokens': 42}
        usage = usage_from_payload(payload)
        self.assertEqual(usage.input_tokens, 42)
        self.assertEqual(usage.output_tokens, 0)
        self.assertEqual(usage.cache_creation_input_tokens, 0)
        self.assertEqual(usage.cache_read_input_tokens, 0)
        self.assertEqual(usage.reasoning_tokens, 0)

    def test_none_returns_empty(self) -> None:
        usage = usage_from_payload(None)
        self.assertEqual(usage, UsageStats())

    def test_empty_dict_returns_defaults(self) -> None:
        usage = usage_from_payload({})
        self.assertEqual(usage, UsageStats())

    def test_non_dict_returns_empty(self) -> None:
        usage = usage_from_payload('not a dict')  # type: ignore[arg-type]
        self.assertEqual(usage, UsageStats())

    def test_string_token_values_parsed(self) -> None:
        payload = {'input_tokens': '99', 'output_tokens': '77'}
        usage = usage_from_payload(payload)
        self.assertEqual(usage.input_tokens, 99)
        self.assertEqual(usage.output_tokens, 77)


class TestOptionalInt(unittest.TestCase):
    """_optional_int handles int, str, float, None, bool correctly."""

    def test_int_value(self) -> None:
        self.assertEqual(_optional_int(42), 42)

    def test_zero(self) -> None:
        self.assertEqual(_optional_int(0), 0)

    def test_negative(self) -> None:
        self.assertEqual(_optional_int(-5), -5)

    def test_str_numeric(self) -> None:
        self.assertEqual(_optional_int('123'), 123)

    def test_float_value(self) -> None:
        self.assertEqual(_optional_int(3.9), 3)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_optional_int(None))

    def test_bool_true_returns_none(self) -> None:
        self.assertIsNone(_optional_int(True))

    def test_bool_false_returns_none(self) -> None:
        self.assertIsNone(_optional_int(False))

    def test_non_numeric_string_returns_none(self) -> None:
        self.assertIsNone(_optional_int('hello'))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_optional_int(''))


class TestOptionalFloat(unittest.TestCase):
    """_optional_float handles int, str, float, None, bool correctly."""

    def test_float_value(self) -> None:
        self.assertAlmostEqual(_optional_float(3.14), 3.14)

    def test_int_value(self) -> None:
        self.assertAlmostEqual(_optional_float(42), 42.0)

    def test_zero(self) -> None:
        self.assertAlmostEqual(_optional_float(0), 0.0)

    def test_str_numeric(self) -> None:
        self.assertAlmostEqual(_optional_float('2.5'), 2.5)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_optional_float(None))

    def test_bool_true_returns_none(self) -> None:
        self.assertIsNone(_optional_float(True))

    def test_bool_false_returns_none(self) -> None:
        self.assertIsNone(_optional_float(False))

    def test_non_numeric_string_returns_none(self) -> None:
        self.assertIsNone(_optional_float('abc'))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_optional_float(''))


class TestDeserializeOutputSchema(unittest.TestCase):
    """_deserialize_output_schema with valid, None, invalid data."""

    def test_valid_payload(self) -> None:
        payload = {
            'name': 'my_schema',
            'schema': {'type': 'object'},
            'strict': True,
        }
        result = _deserialize_output_schema(payload)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, 'my_schema')
        self.assertEqual(result.schema, {'type': 'object'})
        self.assertTrue(result.strict)

    def test_strict_defaults_false(self) -> None:
        payload = {
            'name': 'basic',
            'schema': {'type': 'string'},
        }
        result = _deserialize_output_schema(payload)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.strict)

    def test_none_payload(self) -> None:
        self.assertIsNone(_deserialize_output_schema(None))

    def test_non_dict_payload(self) -> None:
        self.assertIsNone(_deserialize_output_schema('not a dict'))
        self.assertIsNone(_deserialize_output_schema(42))
        self.assertIsNone(_deserialize_output_schema([]))

    def test_missing_schema_key(self) -> None:
        self.assertIsNone(_deserialize_output_schema({'name': 'test'}))

    def test_non_dict_schema(self) -> None:
        self.assertIsNone(_deserialize_output_schema({'name': 'test', 'schema': 'bad'}))

    def test_missing_name(self) -> None:
        self.assertIsNone(_deserialize_output_schema({'schema': {'type': 'object'}}))

    def test_empty_name(self) -> None:
        self.assertIsNone(
            _deserialize_output_schema({'name': '', 'schema': {'type': 'object'}})
        )

    def test_non_string_name(self) -> None:
        self.assertIsNone(
            _deserialize_output_schema({'name': 123, 'schema': {'type': 'object'}})
        )


if __name__ == '__main__':
    unittest.main()
