"""Tests for OmniRoute AI gateway provider integration in TARS."""
import os
import unittest
from unittest.mock import patch, MagicMock

from core.llm_provider import (
    LLMProvider,
    OMNIROUTE_DEFAULT_MODEL,
    OMNIROUTE_DEFAULT_URL,
    get_omniroute_api_key,
)
from tools.gui_tools import inspect_screen


class TestOmniRouterProvider(unittest.TestCase):
    """Verifies OmniRoute gateway client initialization, availability, and tool calling."""

    def test_omniroute_key_env_precedence(self):
        """Verify explicit OMNIROUTE_API_KEY environment variable takes precedence."""
        with patch.dict(os.environ, {"OMNIROUTE_API_KEY": "sk-explicit-omni-key"}, clear=False):
            key = get_omniroute_api_key()
            self.assertEqual(key, "sk-explicit-omni-key")

    def test_omniroute_init_defaults(self):
        """Verify OmniRoute initialization with default model and gateway base URL."""
        with patch.dict(os.environ, {"OMNIROUTE_API_KEY": "test-key"}, clear=False):
            provider = LLMProvider(provider="omniroute")
            self.assertEqual(provider.provider, "omniroute")
            self.assertEqual(provider.model, OMNIROUTE_DEFAULT_MODEL)
            self.assertEqual(provider.omniroute_base_url, OMNIROUTE_DEFAULT_URL)

    def test_omniroute_available_when_gateway_online(self):
        """Verify available() returns True when OmniRoute health check succeeds."""
        provider = LLMProvider(provider="omniroute")
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp
            self.assertTrue(provider.available())

    def test_omniroute_unavailable_when_gateway_offline(self):
        """Verify available() returns False when OmniRoute health check fails."""
        provider = LLMProvider(provider="omniroute")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection refused")
            self.assertFalse(provider.available())

    def test_omniroute_skipped_message_when_offline(self):
        """Verify clear error message returned when OmniRoute gateway is offline."""
        provider = LLMProvider(provider="omniroute")
        with patch.object(provider, "available", return_value=False):
            result = provider.run_tool_agent_loop(
                prompt="test prompt",
                system_instruction="system",
                tools_list=[],
                tool_executor=lambda name, args: None
            )
            self.assertFalse(result["success"])
            self.assertIn("OmniRoute gateway is not running", result["response"])

    def test_omniroute_direct_response(self):
        """Verify OmniRoute returns direct text response when no tools are called."""
        provider = LLMProvider(provider="omniroute")
        mock_client = MagicMock()
        provider.client = mock_client

        choice = MagicMock()
        choice.message.content = "OmniRoute routed your query successfully."
        choice.message.tool_calls = None
        response = MagicMock(choices=[choice])
        mock_client.chat.completions.create.return_value = response

        with patch.object(provider, "available", return_value=True):
            result = provider.run_tool_agent_loop(
                prompt="Hello OmniRoute",
                system_instruction="Be helpful",
                tools_list=[],
                tool_executor=lambda name, args: None
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["response"], "OmniRoute routed your query successfully.")
            self.assertEqual(result["steps"], [])

    def test_omniroute_tool_calling_loop(self):
        """Verify OmniRoute executes tool and returns final synthesized response."""
        provider = LLMProvider(provider="omniroute")
        mock_client = MagicMock()
        provider.client = mock_client

        # Turn 1: tool call request
        tc = MagicMock()
        tc.id = "call_omni_01"
        tc.function.name = "list_directory"
        tc.function.arguments = '{"path": "C:\\\\"}'
        choice1 = MagicMock()
        choice1.message.content = None
        choice1.message.tool_calls = [tc]
        resp1 = MagicMock(choices=[choice1])

        # Turn 2: final answer
        choice2 = MagicMock()
        choice2.message.content = "Found 4 directories in root."
        choice2.message.tool_calls = None
        resp2 = MagicMock(choices=[choice2])

        mock_client.chat.completions.create.side_effect = [resp1, resp2]

        executed_tools = []
        def dummy_executor(name, args):
            executed_tools.append((name, args))
            return {"items": ["Windows", "Program Files", "Users", "codehub"]}

        def list_directory(path: str = "C:\\") -> dict:
            """Lists files in directory."""
            return {"items": []}

        with patch.object(provider, "available", return_value=True):
            result = provider.run_tool_agent_loop(
                prompt="List root directory",
                system_instruction="System prompt",
                tools_list=[list_directory],
                tool_executor=dummy_executor
            )

            self.assertTrue(result["success"])
            self.assertIn("Found 4 directories", result["response"])
            self.assertEqual(len(result["steps"]), 1)
            self.assertEqual(result["steps"][0]["tool"], "list_directory")
            self.assertEqual(executed_tools, [("list_directory", {"path": "C:\\"})])
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    def test_omniroute_rate_limit_handling(self):
        """Verify 429 rate limit triggers quota cooldown for OmniRoute."""
        provider = LLMProvider(provider="omniroute")
        mock_client = MagicMock()
        provider.client = mock_client
        mock_client.chat.completions.create.side_effect = Exception("429 Too Many Requests")

        with patch.object(provider, "available", return_value=True):
            result = provider.run_tool_agent_loop(
                prompt="test prompt",
                system_instruction="system",
                tools_list=[],
                tool_executor=lambda n, a: None
            )
            self.assertFalse(result["success"])
            self.assertIn("rate limit reached", result["response"])

    def test_omniroute_vision_screen_inspection(self):
        """Verify inspect_screen supports OmniRoute vision when AI_PROVIDER is omniroute."""
        with patch.dict(os.environ, {
            "AI_PROVIDER": "omniroute",
            "GEMINI_API_KEY": "",
            "OPENROUTER_API_KEY": ""
        }, clear=False):
            with patch("tools.gui_tools.take_screenshot") as mock_shot:
                mock_shot.return_value = {"success": True, "filepath": "test_shot.png"}
                with patch("builtins.open", unittest.mock.mock_open(read_data=b"fakepngdata")):
                    with patch("openai.OpenAI") as mock_openai_cls:
                        mock_client = MagicMock()
                        mock_openai_cls.return_value = mock_client
                        choice = MagicMock()
                        choice.message.content = "OmniRoute sees desktop with a browser open."
                        mock_client.chat.completions.create.return_value = MagicMock(choices=[choice])

                        result = inspect_screen("What is on screen?")
                        self.assertTrue(result["success"])
                        self.assertEqual(result["description"], "OmniRoute sees desktop with a browser open.")


if __name__ == "__main__":
    unittest.main()
