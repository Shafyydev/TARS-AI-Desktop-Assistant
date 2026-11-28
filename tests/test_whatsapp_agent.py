"""Comprehensive tests for stateful, contextual, verified WhatsApp Desktop AI agent."""
from dataclasses import asdict
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from core.brain import TARSAgent
from core.memory import MemoryStore
from core.whatsapp_state import (
    WHATSAPP_ACTION_PERMISSIONS,
    get_whatsapp_state_manager,
    reset_whatsapp_state_manager,
)
import tools.whatsapp_desktop as wd


def _make_rect(left=0, top=0, right=400, bottom=60):
    return MagicMock(left=left, top=top, right=right, bottom=bottom)


def _make_ctrl(name="", control_type="Text", aid="", auto_value="", rect=None, descendants=None):
    ctrl = MagicMock()
    ctrl.element_info.name = name
    ctrl.element_info.control_type = control_type
    ctrl.element_info.automation_id = aid
    ctrl.rectangle.return_value = rect or _make_rect()
    ctrl.get_value.return_value = auto_value
    ctrl.iface_value.CurrentValue = auto_value
    ctrl.iface_value.SetValue = MagicMock()
    ctrl.window_text.return_value = name
    ctrl.texts.return_value = [name] if name else []
    ctrl.descendants.return_value = descendants if descendants is not None else []
    return ctrl


def _make_whatsapp_window(
    title="WhatsApp",
    pid=None,
    search_value="",
    row_items=None,
    header_name=None,
    composer=None,
    extra_buttons=None,
    bubble_groups=None,
):
    window = MagicMock()
    window.handle = 12345  # Mock handle (win32gui.IsWindow(12345) == 0 -> safe mock UIA path)
    window.element_info.name = title
    window.element_info.class_name = "WinUIDesktopWin32WindowClass"
    window.window_text.return_value = title
    window.rectangle.return_value = _make_rect(0, 0, 1280, 860)
    if pid is not None:
        window.process_id.return_value = pid

    search_edit = _make_ctrl(
        name="Search or start a new chat",
        control_type="Edit",
        aid="_r_e_",
        auto_value=search_value,
        rect=_make_rect(60, 100, 420, 145),
    )

    def _descendants(control_type=None, **kwargs):
        if control_type == "Edit":
            edits = [search_edit]
            if composer is not None:
                edits.append(composer)
            return edits
        if control_type == "DataItem":
            return list(row_items or [])
        if control_type == "Button":
            btns = list(extra_buttons or [])
            if header_name:
                btns.append(
                    _make_ctrl(
                        name=f"Contact info {header_name}",
                        control_type="Button",
                        aid="_r_3u_",
                        rect=_make_rect(520, 35, 900, 95),
                    )
                )
            return btns
        if control_type == "Text":
            if header_name:
                return [
                    _make_ctrl(
                        name=header_name,
                        control_type="Text",
                        rect=_make_rect(540, 40, 800, 72),
                    )
                ]
            return []
        if control_type == "Group":
            return list(bubble_groups or [])
        return []

    window.descendants.side_effect = _descendants
    window._search_edit = search_edit
    return window


class TestWhatsAppAgentScenarios(unittest.TestCase):
    """Covers Part 10 Tests 1-10 plus Part 9 safety & contextual state regressions."""

    def setUp(self):
        reset_whatsapp_state_manager()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_memory = None
        with patch("core.brain.TARSAgent._init_llm_client"):
            self.agent = TARSAgent()
        # Isolate persistence away from live data/memory.db
        self.agent.memory = MemoryStore(db_path=Path(self._tmpdir.name) / "test_memory.db")
        self.agent.last_whatsapp_contact = None
        self.agent.whatsapp_state = reset_whatsapp_state_manager()

    def tearDown(self):
        reset_whatsapp_state_manager()
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Test 1: Multiple search results -> no guessing, retained in context
    # ------------------------------------------------------------------
    @patch("tools.whatsapp_desktop.time.sleep")
    @patch("tools.whatsapp_desktop._post_client_click", return_value=True)
    @patch("pywinauto.Desktop")
    def test_01_multi_match_search_retains_candidates_without_guessing(self, mock_desktop, _mock_click, _mock_sleep):
        rows = [
            _make_ctrl(name="Chats", control_type="DataItem", rect=_make_rect(50, 170, 420, 195)),
            _make_ctrl(name="Shafi Friend 10:15 See you tomorrow", control_type="DataItem", rect=_make_rect(50, 200, 420, 265)),
            _make_ctrl(name="Shafi College 09:40 Lab assignment PDF", control_type="DataItem", rect=_make_rect(50, 270, 420, 335)),
            _make_ctrl(name="Shafi Work Yesterday Standup notes", control_type="DataItem", rect=_make_rect(50, 340, 420, 405)),
        ]
        win = _make_whatsapp_window(search_value="Shafi", row_items=rows)
        mock_desktop.return_value.windows.return_value = [win]

        res = wd.search_contacts("Shafi")

        self.assertTrue(res["success"])
        self.assertTrue(res["ambiguous"])
        self.assertEqual(res["count"], 3)
        names = [c["name"] for c in res["results"]]
        self.assertEqual(names, ["Shafi Friend", "Shafi College", "Shafi Work"])
        self.assertIn("Which one should I open", res["message"])

        snap = self.agent.whatsapp_state.snapshot()
        self.assertEqual(snap["last_search_query"], "Shafi")
        self.assertEqual(snap["workflow_status"], "awaiting_user_selection")
        self.assertEqual([r["name"] for r in snap["search_results"]], names)
        self.assertIsNone(snap["active_chat"])

    # ------------------------------------------------------------------
    # Test 2: Follow-up "Open the first chat" uses state, verifies header,
    #         routes through Groq orchestrator without blind Enter click
    # ------------------------------------------------------------------
    @patch("tools.gui_tools.press_keys")
    def test_02_open_first_chat_resolves_from_state_through_orchestrator(self, mock_press_keys):
        state = self.agent.whatsapp_state
        state.record_window_state(title="WhatsApp", process="whatsapp.root.exe", active=True, visible=True, verified=True, screen="search_results")
        state.record_search(
            "Shafi",
            [
                asdict(wd.SearchResultEntry(index=1, name="Shafi Friend", section="chats")),
                asdict(wd.SearchResultEntry(index=2, name="Shafi College", section="chats")),
                asdict(wd.SearchResultEntry(index=3, name="Shafi Work", section="chats")),
            ],
            verified=True,
        )

        # Simulate real verified open when Groq selects open_whatsapp_chat
        opened_calls = []

        def fake_open_chat(contact_name):
            opened_calls.append(contact_name)
            ref = state.resolve_contact_reference(contact_name)
            chosen = ref["contact"]
            state.record_verified_chat(chosen, header_raw=chosen, selected_entry=ref.get("selected_result"))
            return {"success": True, "verified": True, "contact": chosen, "message": f"Opened WhatsApp chat with '{chosen}'."}

        self.agent.tool_registry["open_whatsapp_chat"] = fake_open_chat
        self.agent.orchestrator.llm = MagicMock()
        self.agent.orchestrator.llm.provider = "groq"
        self.agent.orchestrator.llm.available.return_value = True

        seen_prompts = []

        def fake_llm_loop(**kwargs):
            seen_prompts.append(kwargs["system_instruction"])
            tool_res = kwargs["tool_executor"]("open_whatsapp_chat", {"contact_name": "first chat"})
            self.assertTrue(tool_res["success"])
            self.assertEqual(tool_res["whatsapp_state"]["verified_state"]["active_chat"], "Shafi Friend")
            return {
                "success": True,
                "response": "Opened Shafi Friend's chat.",
                "steps": [{"tool": "open_whatsapp_chat", "args": {"contact_name": "first chat"}, "result": tool_res}],
            }

        self.agent.orchestrator.llm.run_tool_agent_loop.side_effect = fake_llm_loop

        with patch("tools.get_running_windows", return_value=["WhatsApp"]):
            out = self.agent.run_agent_loop("Open the first chat")

        self.assertTrue(out["success"])
        self.assertIn("Shafi Friend", out["response"])
        self.assertEqual(opened_calls, ["first chat"])
        self.assertEqual(self.agent.whatsapp_state.active_chat, "Shafi Friend")
        # Context injected to Groq must contain numbered search results
        self.assertTrue(any("1. Shafi Friend" in p and "2. Shafi College" in p for p in seen_prompts))
        # Must NEVER execute blind press_keys(["enter"]) fast path
        mock_press_keys.assert_not_called()

    # ------------------------------------------------------------------
    # Test 3: Follow-up "Open the second result" / "other Shafi"
    # ------------------------------------------------------------------
    def test_03_open_second_and_other_result_from_context(self):
        state = self.agent.whatsapp_state
        state.record_search(
            "Shafi",
            [
                asdict(wd.SearchResultEntry(index=1, name="Shafi Friend", section="chats")),
                asdict(wd.SearchResultEntry(index=2, name="Shafi College", section="chats")),
                asdict(wd.SearchResultEntry(index=3, name="Shafi Work", section="chats")),
            ],
            verified=True,
        )
        state.record_verified_chat("Shafi Friend", header_raw="Shafi Friend", selected_entry={"index": 1, "name": "Shafi Friend"})

        second_ref = wd.resolve_contact("second result")
        self.assertTrue(second_ref["success"])
        self.assertEqual(second_ref["contact"], "Shafi College")

        # Simulate opening second result, then asking for "the other one" / "other Shafi"
        state.record_verified_chat("Shafi College", header_raw="Shafi College", selected_entry={"index": 2, "name": "Shafi College"})
        self.assertEqual(state.snapshot()["previously_selected_contact"], "Shafi Friend")

        other_ref = wd.resolve_contact("the other Shafi")
        self.assertTrue(other_ref["success"])
        # Ambiguous among remaining matches (#1 and #3), or unambiguous if 2 results; check valid state resolution
        self.assertIn(other_ref.get("contact") or "", {"", "Shafi Friend", "Shafi Work"})

    # ------------------------------------------------------------------
    # Test 4: "Open Shameem's chat" -> "Read his last message"
    # ------------------------------------------------------------------
    def test_04_read_his_last_message_resolves_without_restating_name(self):
        state = self.agent.whatsapp_state
        state.record_window_state(title="WhatsApp", process="whatsapp.root.exe", active=True, visible=True, verified=True, screen="active_chat")
        state.record_verified_chat("Shameem", header_raw="Shameem")

        resolved = state.resolve_contact_reference("his")
        self.assertTrue(resolved["success"])
        self.assertEqual(resolved["contact"], "Shameem")

        def fake_get_latest(contact=None, direction="any"):
            target = contact or state.active_chat
            return {
                "success": True,
                "verified": True,
                "contact": target,
                "latest_message": {
                    "sender": "Shameem",
                    "direction": "incoming",
                    "text": "Exam starts at 9:30 AM tomorrow.",
                    "timestamp": "18:05",
                },
                "message": "Latest message in Shameem's chat: \"Exam starts at 9:30 AM tomorrow.\" (at 18:05).",
            }

        self.agent.tool_registry["get_latest_message"] = fake_get_latest
        self.agent.orchestrator.llm = MagicMock()
        self.agent.orchestrator.llm.provider = "groq"
        self.agent.orchestrator.llm.available.return_value = True

        def fake_llm_loop(**kwargs):
            self.assertIn("Verified active chat: Shameem", kwargs["system_instruction"])
            res = kwargs["tool_executor"]("get_latest_message", {"direction": "incoming"})
            return {
                "success": True,
                "response": f"Shameem's last message says: {res['latest_message']['text']}",
                "steps": [{"tool": "get_latest_message", "args": {"direction": "incoming"}, "result": res}],
            }

        self.agent.orchestrator.llm.run_tool_agent_loop.side_effect = fake_llm_loop

        with patch("tools.get_running_windows", return_value=["WhatsApp"]):
            out = self.agent.run_agent_loop("Read his last message")

        self.assertTrue(out["success"])
        self.assertIn("Exam starts at 9:30 AM tomorrow.", out["response"])

    # ------------------------------------------------------------------
    # Test 5: Topic search while chat open -> "Find the conversation about the exam"
    #         -> "Open that conversation"
    # ------------------------------------------------------------------
    @patch("tools.whatsapp_desktop.time.sleep")
    @patch("tools.whatsapp_desktop._post_client_click", return_value=True)
    @patch("pywinauto.Desktop")
    def test_05_topic_search_while_chat_open_and_open_that_conversation(self, mock_desktop, _mock_click, _mock_sleep):
        state = self.agent.whatsapp_state
        state.record_verified_chat("Shameem", header_raw="Shameem")

        msg_rows = [
            _make_ctrl(name="Messages", control_type="DataItem", rect=_make_rect(50, 175, 420, 200)),
            _make_ctrl(name="CS Study Group Yesterday exam schedule posted", control_type="DataItem", rect=_make_rect(50, 205, 420, 270)),
        ]
        win = _make_whatsapp_window(search_value="exam", row_items=msg_rows, header_name="Shameem")
        mock_desktop.return_value.windows.return_value = [win]

        search_res = wd.search_messages("exam")

        self.assertTrue(search_res["success"])
        self.assertEqual(search_res["count"], 1)
        self.assertEqual(search_res["results"][0]["name"], "CS Study Group")
        self.assertEqual(search_res["results"][0]["section"], "messages")

        # Next follow-up: "Open that conversation" must resolve CS Study Group
        ref = wd.resolve_contact("Open that conversation")
        self.assertTrue(ref["success"])
        self.assertEqual(ref["contact"], "CS Study Group")

    # ------------------------------------------------------------------
    # Test 6: WhatsApp Desktop not running -> clear error, no random clicks
    # ------------------------------------------------------------------
    @patch("tools.whatsapp_desktop._call_launch_whatsapp", return_value={"success": False, "error": "WhatsApp failed to start."})
    @patch("tools.whatsapp_desktop.whatsapp_desktop_windows", return_value=[])
    def test_06_whatsapp_not_running_reports_failure_honestly(self, _mock_wins, _mock_launch):
        res_search = wd.search_contacts("Shafi")
        self.assertFalse(res_search["success"])
        self.assertFalse(res_search.get("whatsapp_running", True))
        self.assertTrue(any(w in res_search["error"].lower() for w in ("not open", "failed to start", "not running")))

        res_active = wd.get_active_chat()
        self.assertFalse(res_active["success"])
        self.assertIn("not open", res_active["error"].lower())
        self.assertEqual(self.agent.whatsapp_state.active_screen, "not_running")
        self.assertFalse(self.agent.whatsapp_state.window_verified)

    # ------------------------------------------------------------------
    # Test 7: Strictly Desktop WhatsApp -> reject Brave/Chrome/Edge always
    # ------------------------------------------------------------------
    def test_07_never_targets_chrome_or_brave_for_whatsapp(self):
        brave_proc = MagicMock()
        brave_proc.name.return_value = "brave.exe"
        chrome_proc = MagicMock()
        chrome_proc.name.return_value = "chrome.exe"

        brave_win = MagicMock()
        brave_win.process_id.return_value = 9001
        brave_win.window_text.return_value = "WhatsApp Web - Brave"

        chrome_win = MagicMock()
        chrome_win.process_id.return_value = 9002
        chrome_win.window_text.return_value = "(2) WhatsApp - Google Chrome"

        wa_proc = MagicMock()
        wa_proc.name.return_value = "WhatsApp.Root.exe"
        wa_win = MagicMock()
        wa_win.process_id.return_value = 9003
        wa_win.window_text.return_value = "WhatsApp"

        def fake_process(pid):
            return {9001: brave_proc, 9002: chrome_proc, 9003: wa_proc}[pid]

        with patch("psutil.Process", side_effect=fake_process):
            self.assertFalse(wd.is_whatsapp_desktop_window(brave_win))
            self.assertFalse(wd.is_whatsapp_desktop_window(chrome_win))
            self.assertTrue(wd.is_whatsapp_desktop_window(wa_win))

        # Regression: "Search for Shafi" during active WhatsApp session must NEVER hit web search
        mock_smart_search = MagicMock(return_value={"success": True})
        mock_browser_nav = MagicMock(return_value={"success": True})
        self.agent.tool_registry["smart_search"] = mock_smart_search
        self.agent.tool_registry["browser_navigate"] = mock_browser_nav
        self.agent.whatsapp_state.record_window_state(title="WhatsApp", process="whatsapp.root.exe", active=True, visible=True, verified=True, screen="chat_list")

        self.agent.orchestrator.llm = MagicMock()
        self.agent.orchestrator.llm.provider = "groq"
        self.agent.orchestrator.llm.available.return_value = True
        self.agent.orchestrator.llm.run_tool_agent_loop.return_value = {
            "success": True,
            "response": "Found 3 Shafi contacts.",
            "steps": [{"tool": "search_contacts", "args": {"query": "Shafi"}, "result": {"success": True}}],
        }

        self.agent.run_agent_loop("Search for Shafi")
        mock_smart_search.assert_not_called()
        mock_browser_nav.assert_not_called()

    # ------------------------------------------------------------------
    # Test 8: Partial-message prevention (composer only gets "h" -> ABORT)
    # ------------------------------------------------------------------
    @patch("tools.whatsapp_desktop.time.sleep")
    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value=wd.VERIFIED)
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Shameem"})
    @patch("pywinauto.Desktop")
    def test_08_partial_composer_text_never_dispatches_enter(
        self, mock_desktop, _mock_open, _mock_verify, _mock_copy, _mock_write, mock_keys, _mock_sleep
    ):
        # Composer accepts only single character "h" instead of full expected message
        composer = _make_ctrl(
            name="Type a message",
            control_type="Edit",
            auto_value="h",
            rect=_make_rect(550, 790, 1200, 840),
        )
        win = _make_whatsapp_window(composer=composer, header_name="Shameem")
        mock_desktop.return_value.windows.return_value = [win]

        res = wd.send_whatsapp_message("Shameem", "hello team meeting at 5pm")

        self.assertFalse(res["success"])
        self.assertEqual(res.get("verification"), "FAILED")
        self.assertIn("complete expected text", res["error"].lower())
        self.assertNotIn(call(["enter"]), mock_keys.mock_calls)

    # ------------------------------------------------------------------
    # Test 9: Failed click / mismatched header aborts cleanly
    # ------------------------------------------------------------------
    @patch("tools.whatsapp_desktop.time.sleep")
    @patch("tools.interaction_tools.gui.press_keys")
    @patch("pywinauto.Desktop")
    def test_09_mismatched_chat_header_refuses_send_and_reports_mismatch(
        self, mock_desktop, mock_keys, _mock_sleep
    ):
        # Expected Shameem, but UI header actually shows "John Work"
        composer = _make_ctrl(name="Type a message", control_type="Edit", auto_value="", rect=_make_rect(550, 790, 1200, 840))
        win = _make_whatsapp_window(composer=composer, header_name="John Work")
        mock_desktop.return_value.windows.return_value = [win]

        with patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Shameem"}):
            res = wd.send_whatsapp_message("Shameem", "hi")

        self.assertFalse(res["success"])
        self.assertEqual(res.get("verification"), wd.MISMATCH)
        self.assertIn("mismatch", res["error"].lower())
        mock_keys.assert_not_called()

    # ------------------------------------------------------------------
    # Test 10: Unconfirmed delivery blocks autonomous duplicate resend
    # ------------------------------------------------------------------
    @patch("tools.whatsapp_desktop.time.sleep")
    @patch("tools.interaction_tools.gui.press_keys")
    @patch("tools.interaction_tools.pyautogui.write")
    @patch("tools.interaction_tools.copy_to_clipboard", return_value=True)
    @patch("tools.interaction_tools._verify_whatsapp_chat_header", return_value=wd.VERIFIED)
    @patch("tools.interaction_tools.open_whatsapp_chat", return_value={"success": True, "contact": "Shameem"})
    @patch("pywinauto.Desktop")
    def test_10_unconfirmed_delivery_blocks_autonomous_duplicate_resend(
        self, mock_desktop, _mock_open, _mock_verify, _mock_copy, _mock_write, mock_keys, _mock_sleep
    ):
        composer = _make_ctrl(name="Type a message", control_type="Edit", rect=_make_rect(550, 790, 1200, 840))
        # First read verifies full text in composer; post-Enter read shows empty composer
        composer.get_value.side_effect = ["meeting at 6", "", "", "", ""]
        composer.iface_value.CurrentValue = "meeting at 6"
        win = _make_whatsapp_window(composer=composer, header_name="Shameem", row_items=[])
        mock_desktop.return_value.windows.return_value = [win]

        res = wd.send_whatsapp_message("Shameem", "meeting at 6")

        self.assertTrue(res["success"])
        self.assertEqual(res["verification"], "UNCONFIRMED")
        self.assertEqual(mock_keys.call_count, 1)
        self.assertTrue(self.agent.whatsapp_state.has_recent_uncertain_send("Shameem", "meeting at 6"))

        # Now verify orchestrator blocks Groq from autonomously resending the same unconfirmed message
        self.agent.orchestrator._active_user_request = "check if it went through"
        dup_block = self.agent.orchestrator._llm_tool_executor(
            "send_whatsapp_message",
            {"contact_name": "Shameem", "message": "meeting at 6"},
        )
        self.assertFalse(dup_block["success"])
        self.assertEqual(dup_block.get("verification"), "BLOCKED_DUPLICATE")
        self.assertEqual(mock_keys.call_count, 1)

    # ------------------------------------------------------------------
    # Part 9: Permission classification & confirmation gate
    # ------------------------------------------------------------------
    def test_11_permission_classification_and_external_write_confirmation(self):
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["get_active_chat"], "READ")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["search_contacts"], "READ")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["open_whatsapp"], "NAVIGATION")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["open_chat"], "NAVIGATION")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["send_message"], "EXTERNAL_WRITE")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["reply_to_message"], "EXTERNAL_WRITE")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["delete_chat"], "DESTRUCTIVE")
        self.assertEqual(WHATSAPP_ACTION_PERMISSIONS["change_profile_name"], "ACCOUNT_SETTINGS")

        # Model-initiated external write triggers confirmation gate; approval executes once
        mock_reply = MagicMock(return_value={"success": True, "verified": True, "contact": "Shameem", "verification": "VERIFIED", "message": "Sent."})
        self.agent.tool_registry["reply_to_message"] = mock_reply

        gated = self.agent.execute_tool("reply_to_message", {"message": "Yes I will join"})
        self.assertFalse(gated["success"])
        self.assertTrue(gated.get("requires_confirmation"))
        self.assertIsNotNone(self.agent.pending_confirmation)
        mock_reply.assert_not_called()

        # User confirms with "yes"
        approved = self.agent.run_agent_loop("yes")
        self.assertTrue(approved["success"])
        mock_reply.assert_called_once_with(message="Yes I will join")


if __name__ == "__main__":
    unittest.main()
