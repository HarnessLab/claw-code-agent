from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.agent_types import ModelConfig
from src.langchain_model import _LANGCHAIN_IMPORT_ERROR, openai_chat_messages_to_langchain

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
except ImportError:
    AIMessage = None  # type: ignore[misc, assignment]
    HumanMessage = None
    SystemMessage = None
    ToolMessage = None


@unittest.skipIf(_LANGCHAIN_IMPORT_ERROR, 'langchain optional extra not installed')
class TestOpenAiMessagesToLangchain(unittest.TestCase):
    def test_converts_system_user_assistant_tool(self) -> None:
        assert SystemMessage is not None and HumanMessage is not None
        assert AIMessage is not None and ToolMessage is not None
        messages = [
            {'role': 'system', 'content': 'sys'},
            {'role': 'user', 'content': 'hi'},
            {
                'role': 'assistant',
                'content': '',
                'tool_calls': [
                    {
                        'id': 'c1',
                        'type': 'function',
                        'function': {
                            'name': 'echo',
                            'arguments': '{"x": 1}',
                        },
                    }
                ],
            },
            {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"ok": true}'},
        ]
        lc = openai_chat_messages_to_langchain(messages)
        self.assertIsInstance(lc[0], SystemMessage)
        self.assertIsInstance(lc[1], HumanMessage)
        self.assertIsInstance(lc[2], AIMessage)
        self.assertEqual(len(lc[2].tool_calls), 1)  # type: ignore[union-attr]
        self.assertIsInstance(lc[3], ToolMessage)


@unittest.skipIf(_LANGCHAIN_IMPORT_ERROR, 'langchain optional extra not installed')
class TestLangChainChatClient(unittest.TestCase):
    def test_complete_maps_ai_message_to_assistant_turn(self) -> None:
        from src.langchain_model import LangChainChatClient

        assert AIMessage is not None
        cfg = ModelConfig(model='test-model', model_backend='langchain')
        client = LangChainChatClient(cfg)
        fake_ai = AIMessage(
            content='hello',
            tool_calls=[],
            usage_metadata={'input_tokens': 3, 'output_tokens': 2},
            response_metadata={'finish_reason': 'stop'},
        )
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = fake_ai
        with patch.object(LangChainChatClient, '_chat_model', return_value=fake_llm):
            turn = client.complete([{'role': 'user', 'content': 'x'}], [])
        self.assertEqual(turn.content, 'hello')
        self.assertEqual(turn.finish_reason, 'stop')
        self.assertEqual(turn.usage.input_tokens, 3)
        self.assertEqual(turn.usage.output_tokens, 2)
