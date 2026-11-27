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

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("pywinauto.Desktop")
    def test_open_whatsapp_chat_uses_search_and_matching_contact(self, mock_desktop, mock_copy, mock_keys):
        window = MagicMock()
        search_edit = MagicMock()
        contact = MagicMock()
        search_edit.element_info.name = "Search"
        contact.element_info.name = "Reshma"
        header = MagicMock()
        header.element_info.name = "Contacts"
        contact.descendants.return_value = []
        search_edit.get_value.side_effect = ["", "Reshma", "Reshma"]
        window.descendants.side_effect = [
            [search_edit], [header, contact]
        ]
        mock_desktop.return_value.windows.return_value = [window]

        res = it.open_whatsapp_chat("Reshma")

        self.assertTrue(res["success"])
        mock_copy.assert_called_once_with("Reshma")
        mock_keys.assert_any_call(["ctrl", "v"])
        mock_keys.assert_any_call(["enter"])
        contact.click_input.assert_called_once()

    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Reshmaa"})
    @patch("pywinauto.Desktop")
    def test_send_whatsapp_message_types_and_sends_via_enter(self, mock_desktop, _mock_open, _mock_verify, _mock_copy, mock_keys, mock_write):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.get_value.side_effect = ["hello there", "", "", "", ""]
        composer.iface_value.CurrentValue = "hello there"

        window.descendants.side_effect = [[composer]]
        mock_desktop.return_value.windows.return_value = [window]

        res = it.send_whatsapp_message("Reshmaa", "hello there")

        self.assertTrue(res["success"])
        mock_keys.assert_any_call(["enter"])

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Reshmaa"})
    @patch("pywinauto.Desktop")
    def test_send_whatsapp_message_verified_header(self, mock_desktop, _mock_open, _mock_verify, mock_copy, _mock_keys):
        """VERIFIED: header contains contact name -> message sent."""
        window = MagicMock()
        window.element_info.name = "Chat with Reshmaa"
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.get_value.side_effect = ["hello there", "", "", "", ""]
        composer.iface_value.CurrentValue = "hello there"
        send_btn = MagicMock()
        send_btn.element_info.name = "Send"
        send_btn.element_info.automation_id = "send"
        send_btn.rectangle.return_value = MagicMock(top=1100, left=1800, right=1860, bottom=1150)
        msg_item = MagicMock()
        msg_item.element_info.name = "Reshmaa 3:14 pm hello there"
        window.descendants.side_effect = [[composer], [msg_item]]
        mock_desktop.return_value.windows.return_value = [window]

        res = it.send_whatsapp_message("Reshmaa", "hello there")

        self.assertTrue(res["success"])
        _mock_keys.assert_any_call(["enter"])

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Reshmaa"})
    @patch("pywinauto.Desktop")
    def test_send_whatsapp_message_mismatch_header(self, mock_desktop, _mock_open, mock_copy, _mock_keys):
        """MISMATCH: header shows different person -> message NOT sent."""
        window = MagicMock()
        window.element_info.name = "WhatsApp"
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        # Header element with a DIFFERENT name in the top 15%
        header_ctrl = MagicMock()
        header_ctrl.element_info.control_type = "Text"
        header_ctrl.element_info.name = "John Doe"
        header_ctrl.rectangle.return_value = MagicMock(top=50, left=100, right=300, bottom=80)
        window.descendants.side_effect = [[header_ctrl], [], []]
        mock_desktop.return_value.windows.return_value = [window]

        res = it.send_whatsapp_message("Reshmaa", "hello there")

        self.assertFalse(res["success"])
        self.assertIn("mismatch", res["error"].lower())

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Reshmaa"})
    @patch("pywinauto.Desktop")
    def test_send_whatsapp_message_unknown_header(self, mock_desktop, _mock_open, mock_copy, _mock_keys):
        """UNKNOWN: cannot access UI elements -> message NOT sent."""
        window = MagicMock()
        window.element_info.name = "WhatsApp"
        window.rectangle.side_effect = Exception("no access")
        mock_desktop.return_value.windows.return_value = [window]

        res = it.send_whatsapp_message("Reshmaa", "hello there")

        self.assertFalse(res["success"])
        self.assertIn("unknown", res["error"].lower())


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

    @patch("tools.interaction_tools.os_tools.launch_application")
    @patch("tools.interaction_tools.get_active_window_context")
    def test_smart_search_new_tab_phrasing_opens_tab(self, mock_ctx, mock_launch):
        # "in new tab" must not end up inside the query string.
        mock_ctx.return_value = {"title": "PowerShell", "category": "terminal", "class_name": "ConsoleWindowClass"}
        mock_launch.return_value = {"success": True, "message": "opened"}
        res = it.smart_search("ttf vasan in youtube in new tab")
        self.assertTrue(res["success"])
        url = mock_launch.call_args[0][0]
        self.assertNotIn("new+tab", url)
        self.assertIn("ttf+vasan", url)


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

    def test_agent_opens_whatsapp_chat_instead_of_launching_contact_name(self):
        mock_chat = MagicMock(return_value={"success": True, "message": "Opened chat."})
        self.agent.tool_registry["open_whatsapp_chat"] = mock_chat

        result = self.agent.run_agent_loop("go to Reshma chat")

        self.assertTrue(result["success"])
        mock_chat.assert_called_once_with(contact_name="reshma")

    def test_agent_searches_whatsapp_instead_of_the_web(self):
        mock_chat = MagicMock(return_value={"success": True, "message": "Opened chat."})
        self.agent.tool_registry["open_whatsapp_chat"] = mock_chat

        result = self.agent.run_agent_loop("search Shafi in WhatsApp")

        self.assertTrue(result["success"])
        mock_chat.assert_called_once_with(contact_name="shafi")

    def test_agent_uses_implicit_contact_name_when_whatsapp_is_running(self):
        mock_chat = MagicMock(return_value={"success": True, "message": "Opened chat."})
        self.agent.tool_registry["open_whatsapp_chat"] = mock_chat

        with patch("tools.get_running_windows", return_value=["WhatsApp"]):
            with patch("tools.resolve_system_target", return_value=None):
                result = self.agent.run_agent_loop("go to Shafi")

        self.assertTrue(result["success"])
        mock_chat.assert_called_once_with(contact_name="shafi")

    def test_agent_clicks_named_whatsapp_contact_before_generic_click(self):
        mock_chat = MagicMock(return_value={"success": True, "message": "Opened chat."})
        self.agent.tool_registry["open_whatsapp_chat"] = mock_chat

        with patch("tools.get_running_windows", return_value=["WhatsApp"]):
            result = self.agent.run_agent_loop("click Shafi")

        self.assertTrue(result["success"])
        mock_chat.assert_called_once_with(contact_name="shafi")

    def test_agent_sets_exact_volume_without_calling_llm(self):
        mock_volume = MagicMock(return_value={"success": True, "message": "Volume set."})
        self.agent.tool_registry["set_system_volume"] = mock_volume

        result = self.agent.run_agent_loop("decrease volume to 50%")

        self.assertTrue(result["success"])
        mock_volume.assert_called_once_with(level_percent=50)

    def test_agent_understands_british_minimise_terminal(self):
        mock_minimize = MagicMock(return_value={"success": True, "message": "Minimized."})
        self.agent.tool_registry["minimize_active_window"] = mock_minimize

        result = self.agent.run_agent_loop("minimise terminal")

        self.assertTrue(result["success"])
        mock_minimize.assert_called_once_with()

    def test_agent_sends_whatsapp_message_without_confirmation(self):
        """WhatsApp sending is not gated: it goes straight through."""
        mock_send = MagicMock(return_value={"success": True, "message": "Sent."})
        self.agent.tool_registry["send_whatsapp_message"] = mock_send

        result = self.agent.run_agent_loop("send message to amma saying hello")

        self.assertTrue(result["success"])
        mock_send.assert_called_once_with(contact_name="amma", message="hello")
        self.assertIsNone(self.agent.pending_confirmation)

    def test_agent_bare_send_form_reaches_whatsapp(self):
        """A keyword-less "send reshmaa hello" must not fall through to the model."""
        mock_send = MagicMock(return_value={"success": True, "message": "Sent."})
        self.agent.tool_registry["send_whatsapp_message"] = mock_send

        result = self.agent.run_agent_loop("send reshmaa hello")

        self.assertTrue(result["success"])
        mock_send.assert_called_once_with(contact_name="reshmaa", message="hello")

    def test_agent_placeholder_contact_asks_instead_of_searching(self):
        """"Send another one" with no previous chat has no target to search for."""
        mock_send = MagicMock(return_value={"success": True, "message": "Sent."})
        self.agent.tool_registry["send_whatsapp_message"] = mock_send
        self.agent.last_whatsapp_contact = None

        result = self.agent.run_agent_loop("send another messg saying hello")

        self.assertFalse(result["success"])
        mock_send.assert_not_called()

    def test_agent_does_not_treat_bare_send_as_whatsapp_message(self):
        """A misheard "send ..." must not turn into a WhatsApp message."""
        mock_send = MagicMock(return_value={"success": True, "message": "Sent."})
        self.agent.tool_registry["send_whatsapp_message"] = mock_send
        self.agent.last_whatsapp_contact = "amma"

        result = self.agent.run_agent_loop("send hello how are you")

        self.assertFalse(result["success"])
        mock_send.assert_not_called()

    def test_agent_handles_filler_prefixed_browser_youtube_search(self):
        """Spoken filler must not push a browser/YouTube search into the LLM path."""
        mock_search = MagicMock(return_value={"success": True, "message": "Searching YouTube."})
        self.agent.tool_registry["smart_search"] = mock_search

        result = self.agent.run_agent_loop(
            "ok now the task is open brave search YouTube and search for ttf"
        )

        self.assertTrue(result["success"])
        mock_search.assert_called_once_with(query="ttf", engine="youtube")

    def test_agent_handles_open_youtube_and_search_for_query(self):
        mock_search = MagicMock(return_value={"success": True, "message": "Searching YouTube."})
        self.agent.tool_registry["smart_search"] = mock_search

        result = self.agent.run_agent_loop("open youtube and search for lofi beats")

        self.assertTrue(result["success"])
        mock_search.assert_called_once_with(query="lofi beats", engine="youtube")

    def test_agent_does_not_send_bare_send_to_llm(self):
        result = self.agent.run_agent_loop("send")
        self.assertFalse(result["success"])
        self.assertIn("message to send", result["response"])


class TestWhatsAppVerification(TestInteractionTools):
    """Tests for message delivery verification statuses."""

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._find_whatsapp_message_composer")
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Amma"})
    @patch("pywinauto.Desktop")
    def test_verified_message(self, mock_desktop, _mock_open, _mock_verify, mock_composer, _mock_copy, _mock_write, _mock_keys):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        window.handle = 12345
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.rectangle.return_value = MagicMock(top=1100, left=600, right=1700, bottom=1160)
        composer.get_value.side_effect = ["hi hello how are you", "", "", "", "", ""]
        composer.iface_value.CurrentValue = "hi hello how are you"
        msg_item = MagicMock()
        msg_item.element_info.name = "Amma 3:14 pm hi hello how are you"
        window.descendants.side_effect = [[msg_item]] * 5
        mock_desktop.return_value.windows.return_value = [window]
        mock_composer.return_value = composer

        res = it.send_whatsapp_message("Amma", "hi hello how are you")

        self.assertTrue(res["success"])
        self.assertEqual(res.get("verification"), "VERIFIED")

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._find_whatsapp_message_composer")
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Amma"})
    @patch("pywinauto.Desktop")
    def test_unconfirmed_message(self, mock_desktop, _mock_open, _mock_verify, mock_composer, _mock_copy, _mock_write, _mock_keys):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        window.handle = 12345
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.rectangle.return_value = MagicMock(top=1100, left=600, right=1700, bottom=1160)
        composer.get_value.side_effect = ["hi hello how are you", "", "", "", "", ""]
        composer.iface_value.CurrentValue = "hi hello how are you"
        window.descendants.side_effect = [[]] * 5
        mock_desktop.return_value.windows.return_value = [window]
        mock_composer.return_value = composer

        res = it.send_whatsapp_message("Amma", "hi hello how are you")

        self.assertTrue(res["success"])
        self.assertEqual(res.get("verification"), "UNCONFIRMED")
        self.assertIn("could not be confirmed", res.get("message", "").lower())

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._find_whatsapp_message_composer")
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Amma"})
    @patch("pywinauto.Desktop")
    def test_failed_send(self, mock_desktop, _mock_open, _mock_verify, mock_composer, _mock_copy, _mock_write, _mock_keys):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        window.handle = 12345
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.rectangle.return_value = MagicMock(top=1100, left=600, right=1700, bottom=1160)
        composer.get_value.return_value = ""
        composer.iface_value.CurrentValue = ""
        window.descendants.side_effect = [[]] * 5
        mock_desktop.return_value.windows.return_value = [window]
        mock_composer.return_value = composer

        res = it.send_whatsapp_message("Amma", "hi hello how are you")

        self.assertFalse(res["success"])
        self.assertEqual(res.get("verification"), "FAILED")

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._find_whatsapp_message_composer")
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Amma"})
    @patch("pywinauto.Desktop")
    def test_uia_exception_during_verification(self, mock_desktop, _mock_open, _mock_verify, mock_composer, _mock_copy, _mock_write, _mock_keys):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        window.handle = 12345
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.rectangle.return_value = MagicMock(top=1100, left=600, right=1700, bottom=1160)
        composer.get_value.side_effect = ["hi hello how are you"] + [Exception("stale element")] + [""] * 10
        composer.iface_value.CurrentValue = "hi hello how are you"
        window.descendants.side_effect = [[]] * 5
        mock_desktop.return_value.windows.return_value = [window]
        mock_composer.return_value = composer

        res = it.send_whatsapp_message("Amma", "hi hello how are you")

        self.assertFalse(res["success"])
        self.assertEqual(res.get("verification"), "FAILED")

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._find_whatsapp_message_composer")
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Amma"})
    @patch("pywinauto.Desktop")
    def test_exact_match_verification(self, mock_desktop, _mock_open, _mock_verify, mock_composer, _mock_copy, _mock_write, _mock_keys):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        window.handle = 12345
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.rectangle.return_value = MagicMock(top=1100, left=600, right=1700, bottom=1160)
        composer.get_value.side_effect = ["hi hello how are you", "", "", "", "", ""]
        composer.iface_value.CurrentValue = "hi hello how are you"
        old_msg_item = MagicMock()
        old_msg_item.element_info.name = "Amma 2:00 pm hello world"
        window.descendants.side_effect = [[old_msg_item]] * 5
        mock_desktop.return_value.windows.return_value = [window]
        mock_composer.return_value = composer

        res = it.send_whatsapp_message("Amma", "hi hello how are you")

        self.assertTrue(res["success"])
        self.assertEqual(res.get("verification"), "UNCONFIRMED")

    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._find_whatsapp_message_composer")
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value="verified")
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Amma"})
    @patch("pywinauto.Desktop")
    def test_no_automatic_resend(self, mock_desktop, _mock_open, _mock_verify, mock_composer, _mock_copy, _mock_write, _mock_keys):
        window = MagicMock()
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)
        window.handle = 12345
        composer = MagicMock()
        composer.element_info.name = "Type a message"
        composer.element_info.control_type = "Edit"
        composer.rectangle.return_value = MagicMock(top=1100, left=600, right=1700, bottom=1160)
        composer.get_value.side_effect = ["hi hello how are you", "", "", "", "", ""]
        composer.iface_value.CurrentValue = "hi hello how are you"
        window.descendants.side_effect = [[]] * 5
        mock_desktop.return_value.windows.return_value = [window]
        mock_composer.return_value = composer

        res = it.send_whatsapp_message("Amma", "hi hello how are you")

        self.assertEqual(_mock_write.call_count, 0)
        self.assertEqual(_mock_keys.call_count, 1)


if __name__ == "__main__":
    unittest.main()
