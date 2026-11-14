"""Tests for OpenRouter provider integration in TARS."""
import os
import unittest
from unittest.mock import patch, MagicMock

from core.llm_provider import LLMProvider, OPENROUTER_DEFAULT_MODEL
from tools.gui_tools import inspect_screen


class TestOpenRouterProvider(unittest.TestCase):
    """Verifies OpenRouter client initialization, availability, and tool agent loop."""

    def test_openrouter_init_defaults(self):
        """Verify OpenRouter initialization with default model and base URL."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            provider = LLMProvider(provider="openrouter")
            self.assertEqual(provider.provider, "openrouter")
            self.assertEqual(provider.model, OPENROUTER_DEFAULT_MODEL)
            self.assertFalse(provider.available())

    def test_openrouter_init_with_key(self):
        """Verify OpenRouter initializes client when API key is provided."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-testkey123"}, clear=False):
            with patch("openai.OpenAI") as mock_openai:
                provider = LLMProvider(provider="openrouter", model="anthropic/claude-3.5-sonnet")
                self.assertEqual(provider.model, "anthropic/claude-3.5-sonnet")
                self.assertTrue(provider.available())
                mock_openai.assert_called_once()
                call_kwargs = mock_openai.call_args[1]
                self.assertEqual(call_kwargs["api_key"], "sk-or-v1-testkey123")
                self.assertIn("openrouter.ai", call_kwargs["base_url"])

    def test_openrouter_unavailable_message(self):
        """Verify descriptive error message returned when OPENROUTER_API_KEY is not configured."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            provider = LLMProvider(provider="openrouter")
            result = provider.run_tool_agent_loop(
                prompt="test prompt",
                system_instruction="system",
                tools_list=[],
                tool_executor=lambda name, args: None
            )
            self.assertFalse(result["success"])
            self.assertIn("OPENROUTER_API_KEY not configured", result["response"])

    def test_openrouter_direct_response(self):
        """Verify OpenRouter returns text response when no tools are called."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-testkey123"}, clear=False):
            provider = LLMProvider(provider="openrouter")
            mock_client = MagicMock()
            provider.client = mock_client

            # Simulate OpenRouter direct answer without tool calls
            choice = MagicMock()
            choice.message.content = "Here is your answer."
            choice.message.tool_calls = None
            response = MagicMock()
            response.choices = [choice]
            mock_client.chat.completions.create.return_value = response

            result = provider.run_tool_agent_loop(
                prompt="Hello TARS",
                system_instruction="Be helpful",
                tools_list=[],
                tool_executor=lambda name, args: None
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["response"], "Here is your answer.")
            self.assertEqual(result["steps"], [])

    def test_openrouter_tool_calling_loop(self):
        """Verify OpenRouter executes tool and synthesizes final answer."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-testkey123"}, clear=False):
            provider = LLMProvider(provider="openrouter")
            mock_client = MagicMock()
            provider.client = mock_client

            # Turn 1: Model requests a tool call
            tc = MagicMock()
            tc.id = "call_abc123"
            tc.function.name = "get_system_volume"
            tc.function.arguments = '{"device": "speaker"}'
            choice1 = MagicMock()
            choice1.message.content = None
            choice1.message.tool_calls = [tc]
            resp1 = MagicMock()
            resp1.choices = [choice1]

            # Turn 2: Model returns final answer
            choice2 = MagicMock()
            choice2.message.content = "The current system volume is 75%."
            choice2.message.tool_calls = None
            resp2 = MagicMock()
            resp2.choices = [choice2]

            mock_client.chat.completions.create.side_effect = [resp1, resp2]

            executed_tools = []
            def dummy_executor(name, args):
                executed_tools.append((name, args))
                return {"volume": 75}

            def get_system_volume(device: str = "speaker") -> dict:
                """Gets the current system volume."""
                return {"volume": 75}

            result = provider.run_tool_agent_loop(
                prompt="What is the volume?",
                system_instruction="Be helpful",
                tools_list=[get_system_volume],
                tool_executor=dummy_executor
            )

            self.assertTrue(result["success"])
            self.assertIn("75%", result["response"])
            self.assertEqual(len(result["steps"]), 1)
            self.assertEqual(result["steps"][0]["tool"], "get_system_volume")
            self.assertEqual(executed_tools, [("get_system_volume", {"device": "speaker"})])
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    def test_openrouter_rate_limit_handling(self):
        """Verify 429 rate limit errors mark quota exhausted and return clear error."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-testkey123"}, clear=False):
            provider = LLMProvider(provider="openrouter")
            mock_client = MagicMock()
            provider.client = mock_client

            mock_client.chat.completions.create.side_effect = Exception("429 Rate limit exceeded")

            result = provider.run_tool_agent_loop(
                prompt="test",
                system_instruction="system",
                tools_list=[],
                tool_executor=lambda n, a: None
            )
            self.assertFalse(result["success"])
            self.assertIn("rate limit reached", result["response"])
            self.assertFalse(provider.available())

    def test_openrouter_vision_screen_inspection(self):
        """Verify inspect_screen supports OpenRouter vision when AI_PROVIDER is openrouter."""
        with patch.dict(os.environ, {
            "AI_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "sk-or-test",
            "GEMINI_API_KEY": ""
        }, clear=False):
            with patch("tools.gui_tools.take_screenshot") as mock_shot:
                mock_shot.return_value = {"success": True, "filepath": "test_shot.png"}
                with patch("builtins.open", unittest.mock.mock_open(read_data=b"fakepngdata")):
                    with patch("openai.OpenAI") as mock_openai_cls:
                        mock_client = MagicMock()
                        mock_openai_cls.return_value = mock_client
                        choice = MagicMock()
                        choice.message.content = "OpenRouter sees VS Code and a terminal open."
                        mock_client.chat.completions.create.return_value = MagicMock(choices=[choice])

                        result = inspect_screen("What is on screen?")
                        self.assertTrue(result["success"])
                        self.assertEqual(result["description"], "OpenRouter sees VS Code and a terminal open.")


if __name__ == "__main__":
    unittest.main()
