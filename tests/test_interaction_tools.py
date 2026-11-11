"""Unit and integration tests for TARS Smart Interaction Engine."""
import unittest
from unittest.mock import patch, MagicMock

import tools.interaction_tools as it
from core.brain import TARSAgent


class TestInteractionTools(unittest.TestCase):
    """Tests for tools.interaction_tools module functions."""

    def test_get_active_window_context_structure(self):
        ctx = it.get_active_window_context()
        self.assertIsInstance(ctx, dict)
        self.assertIn("title", ctx)
        self.assertIn("category", ctx)
        self.assertIn("class_name", ctx)
        self.assertIn(ctx["category"], ["browser", "explorer", "terminal", "editor", "player", "unknown"])

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.get_active_window_context")
    def test_smart_click_search_bar_browser(self, mock_ctx, mock_keys):
        mock_ctx.return_value = {"title": "Brave", "category": "browser", "class_name": "Chrome_WidgetWin_1"}
        mock_keys.return_value = {"success": True}
        res = it.smart_click("search bar")
        self.assertTrue(res["success"])
        mock_keys.assert_called_with(["ctrl", "l"])

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.get_active_window_context")
    def test_smart_click_search_bar_explorer(self, mock_ctx, mock_keys):
        mock_ctx.return_value = {"title": "File Explorer", "category": "explorer", "class_name": "CabinetWClass"}
        mock_keys.return_value = {"success": True}
        res = it.smart_click("address bar")
        self.assertTrue(res["success"])
        mock_keys.assert_called_with(["alt", "d"])

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.get_active_window_context")
    def test_smart_click_first_result(self, mock_ctx, mock_keys):
        mock_ctx.return_value = {"title": "YouTube - Brave", "category": "browser", "class_name": "Chrome_WidgetWin_1"}
        mock_keys.return_value = {"success": True}
        res = it.smart_click("first video")
        self.assertTrue(res["success"])

    @patch("tools.interaction_tools.gui.press_keys")
    def test_smart_click_tab_shortcuts(self, mock_keys):
        mock_keys.return_value = {"success": True}
        res = it.smart_click("new tab")
        self.assertTrue(res["success"])
        mock_keys.assert_called_with(["ctrl", "t"])

        res2 = it.smart_click("close tab")
        self.assertTrue(res2["success"])
        mock_keys.assert_called_with(["ctrl", "w"])

    @patch("tools.interaction_tools.gui.press_keys")
    def test_smart_click_media_controls(self, mock_keys):
        mock_keys.return_value = {"success": True}
        res = it.smart_click("play")
        self.assertTrue(res["success"])
        mock_keys.assert_called_with(["space"])

        res2 = it.smart_click("fullscreen")
        self.assertTrue(res2["success"])
        mock_keys.assert_called_with(["f"])

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.copy_to_clipboard")
    @patch("pyautogui.press")
    def test_smart_type_with_enter(self, mock_press, mock_copy, mock_keys):
        mock_copy.return_value = True
        res = it.smart_type("interstellar trailer and enter")
        self.assertTrue(res["success"])
        self.assertEqual(res["text"], "interstellar trailer")
        self.assertTrue(res["press_enter"])
        mock_copy.assert_called_with("interstellar trailer")
        mock_keys.assert_called_with(["ctrl", "v"])
        mock_press.assert_called_with("enter")

    @patch("tools.interaction_tools.smart_click")
    @patch("tools.interaction_tools.copy_to_clipboard")
    @patch("tools.interaction_tools.gui.press_keys")
    def test_smart_type_with_target(self, mock_keys, mock_copy, mock_click):
        mock_copy.return_value = True
        mock_click.return_value = {"success": True}
        res = it.smart_type("hello world", target="search bar")
        self.assertTrue(res["success"])
        mock_click.assert_called_with("search bar")
        mock_copy.assert_called_with("hello world")

    def test_smart_type_empty_error(self):
        res = it.smart_type("")
        self.assertFalse(res["success"])
        self.assertIn("error", res)

    @patch("tools.interaction_tools.smart_type")
    @patch("tools.interaction_tools.get_active_window_context")
    def test_smart_search_in_browser(self, mock_ctx, mock_type):
        mock_ctx.return_value = {"title": "Brave", "category": "browser", "class_name": "Chrome_WidgetWin_1"}
        mock_type.return_value = {"success": True}
        res = it.smart_search("interstellar", engine="google")
        self.assertTrue(res["success"])
        self.assertIn("google.com/search?q=interstellar", res["url"])
        mock_type.assert_called_once()

    @patch("tools.interaction_tools.smart_type")
    @patch("tools.interaction_tools.get_active_window_context")
    def test_smart_search_youtube_in_browser(self, mock_ctx, mock_type):
        mock_ctx.return_value = {"title": "Brave", "category": "browser", "class_name": "Chrome_WidgetWin_1"}
        mock_type.return_value = {"success": True}
        res = it.smart_search("interstellar trailer", engine="youtube")
        self.assertTrue(res["success"])
        self.assertIn("youtube.com/results?search_query=interstellar+trailer", res["url"])
        mock_type.assert_called_once()


class TestAgentInteractionIntegration(unittest.TestCase):
    """End-to-end command parsing and execution tests via TARSAgent."""

    def setUp(self):
        self.agent = TARSAgent()

    def test_agent_click_search_bar(self):
        mock_click = MagicMock(return_value={"success": True, "message": "Focused search bar."})
        self.agent.tool_registry["smart_click"] = mock_click
        result = self.agent.run_agent_loop("click search bar")
        self.assertTrue(result["success"])
        mock_click.assert_called_with(target="search bar")

    def test_agent_compound_click_and_type(self):
        mock_smart_type = MagicMock(return_value={"success": True, "message": "Typed into search bar."})
        self.agent.tool_registry["smart_type"] = mock_smart_type
        result = self.agent.run_agent_loop("click search bar and type interstellar and press enter")
        self.assertTrue(result["success"])
        mock_smart_type.assert_called_with(text="interstellar", target="search bar", press_enter=True)

    def test_agent_type_into_target(self):
        mock_smart_type = MagicMock(return_value={"success": True, "message": "Typed into search box."})
        self.agent.tool_registry["smart_type"] = mock_smart_type
        result = self.agent.run_agent_loop("type python tutorial into search box and hit enter")
        self.assertTrue(result["success"])
        mock_smart_type.assert_called_with(text="python tutorial", target="search box", press_enter=True)

    def test_agent_search_command(self):
        mock_search = MagicMock(return_value={"success": True, "message": "Searching for interstellar."})
        self.agent.tool_registry["smart_search"] = mock_search
        result = self.agent.run_agent_loop("search for interstellar on youtube")
        self.assertTrue(result["success"])
        mock_search.assert_called_with(query="interstellar", engine="youtube")


if __name__ == "__main__":
    unittest.main()
