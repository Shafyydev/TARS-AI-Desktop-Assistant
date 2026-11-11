"""Comprehensive test suite for TARS Phase 2 features."""
import os
import time
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.brain import TARSAgent
from core.wake_word import WakeWordDetector
from core.scheduler import set_reminder, list_active_reminders, cancel_active_reminder, get_default_scheduler
from tools.gui_tools import inspect_screen, describe_screen
from tools.google_workspace_tools import list_unread_emails, list_upcoming_events, send_email


class TestPhase2Features(unittest.TestCase):
    """Verifies all Phase 2 features and integrations."""

    def setUp(self):
        self.agent = TARSAgent()

    def test_wake_word_detector_initialization(self):
        """Verify WakeWordDetector initializes without unhandled crashes."""
        detector = WakeWordDetector()
        self.assertIsNotNone(detector)
        self.assertIn(detector.engine_name, ["porcupine", "acoustic_vad"])
        detector.close()

    def test_scheduler_reminders_and_persistence(self):
        """Verify reminders and timers schedule, list, persist, and cancel."""
        # 1. Schedule a reminder
        res = set_reminder("water plants", minutes=5.0)
        self.assertTrue(res["success"])
        rem_id = res["reminder_id"]
        self.assertIn("water plants", res["message"])

        # 2. List reminders
        listed = list_active_reminders()
        self.assertTrue(listed["success"])
        self.assertGreaterEqual(listed["count"], 1)
        found = any(r["id"] == rem_id for r in listed["reminders"])
        self.assertTrue(found)

        # 3. Test persistence across scheduler instance
        sched = get_default_scheduler()
        items = sched.list_reminders()
        self.assertTrue(any(i["id"] == rem_id for i in items))

        # 4. Fast path "list reminders"
        fast_list = self.agent.run_agent_loop("list reminders")
        self.assertTrue(fast_list["success"])
        self.assertIn("water plants", fast_list["response"])

        # 5. Cancel reminder
        cancelled = cancel_active_reminder(rem_id)
        self.assertTrue(cancelled["success"])

    @patch("tools.gui_tools.take_screenshot")
    def test_screen_understanding(self, mock_shot):
        """Verify inspect_screen and describe_screen produce multimodal descriptions."""
        mock_shot.return_value = {
            "success": True,
            "filepath": str(Path(__file__).resolve().parent.parent / "config" / "settings.yaml"),
            "width": 1920,
            "height": 1080
        }
        res = inspect_screen()
        self.assertTrue(res["success"])
        self.assertIn("description", res)

        desc_res = describe_screen()
        self.assertTrue(desc_res["success"])
        self.assertIn("description", desc_res)

        # Fast path test
        fast_res = self.agent._autonomous_local_execute("what is on my screen")
        self.assertIsNotNone(fast_res)
        self.assertTrue(fast_res["success"])
        self.assertIn("description", fast_res["steps"][0]["result"])

    def test_tool_call_audit_logging(self):
        """Verify every tool execution is logged with timestamp, name, args, result."""
        log_path = Path(__file__).resolve().parent.parent / "logs" / "tool_calls.jsonl"
        initial_lines = 0
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                initial_lines = len(f.readlines())

        # Execute a tool
        self.agent.execute_tool("get_screen_dimensions", {})

        self.assertTrue(log_path.exists())
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertGreater(len(lines), initial_lines)
        last_record = json.loads(lines[-1].strip())
        self.assertEqual(last_record["tool_name"], "get_screen_dimensions")
        self.assertIn("timestamp", last_record)
        self.assertIn("arguments", last_record)
        self.assertIn("result", last_record)

    def test_gmail_send_email_confirmation_gate(self):
        """Verify send_email is treated as destructive and requires user confirmation."""
        self.assertIn("send_email", self.agent.DESTRUCTIVE_TOOLS)

        # 1. Blocked when unconfirmed
        res_blocked = self.agent.execute_tool(
            "send_email",
            {"to": "test@example.com", "subject": "Test", "body": "Hello"}
        )
        self.assertFalse(res_blocked.get("success", False))
        self.assertTrue(res_blocked.get("requires_confirmation"))
        self.assertIn("CONFIRMATION REQUIRED", res_blocked.get("error", ""))

        # 2. Blocked when user denies confirmation
        self.agent.set_confirmation_callback(lambda tool, args: False)
        res_denied = self.agent.execute_tool(
            "send_email",
            {"to": "test@example.com", "subject": "Test", "body": "Hello"}
        )
        self.assertFalse(res_denied.get("success", False))
        self.assertIn("DENIED", res_denied.get("error", ""))

    def test_google_workspace_unauthenticated_graceful_handling(self):
        """Verify Google tools return informative dicts instead of raising when credentials are missing."""
        emails_res = list_unread_emails()
        self.assertFalse(emails_res["success"])
        self.assertIn("Google Workspace not authenticated", emails_res["error"])

        events_res = list_upcoming_events()
        self.assertFalse(events_res["success"])
        self.assertIn("Google Workspace not authenticated", events_res["error"])


if __name__ == "__main__":
    unittest.main()
