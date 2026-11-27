"""Tests for TARS enhancement features: new tools, fast paths, vision cascade, browser tools, and provider failover."""
import io
import os
import sys
import tempfile
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestOSToolsEnhancements(unittest.TestCase):
    """Tests for new OS tool functions."""

    def test_get_top_processes(self):
        from tools.os_tools import get_top_processes

        result = get_top_processes(sort_by="cpu", limit=5)
        self.assertTrue(result.get("success"), f"get_top_processes failed: {result}")
        self.assertIn("processes", result)
        self.assertIsInstance(result["processes"], list)
        self.assertGreater(len(result["processes"]), 0, "Should return at least 1 process")
        self.assertIn("summary", result)

        proc = result["processes"][0]
        self.assertIn("name", proc)
        self.assertIn("pid", proc)

    def test_get_top_processes_sort_by_ram(self):
        from tools.os_tools import get_top_processes

        result = get_top_processes(sort_by="ram", limit=3)
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("sort_by"), "ram")
        self.assertLessEqual(len(result["processes"]), 3)

    def test_set_screen_brightness(self):
        from tools.os_tools import set_screen_brightness

        with patch("tools.os_tools.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = set_screen_brightness(level_percent=50)
            self.assertTrue(result.get("success"))
            self.assertEqual(result.get("brightness"), 50)

    def test_snap_window_invalid_direction(self):
        from tools.os_tools import snap_window

        result = snap_window(direction="diagonal")
        self.assertFalse(result.get("success", True))

    def test_snap_window_valid_mocked(self):
        from tools.os_tools import snap_window

        with patch("tools.gui_tools.press_keys", return_value={"success": True}) as mock_pk:
            result = snap_window(direction="left")
            self.assertTrue(result.get("success"))
            mock_pk.assert_called_once_with(["win", "left"])

    def test_manage_virtual_desktop_invalid(self):
        from tools.os_tools import manage_virtual_desktop

        result = manage_virtual_desktop(action="invalid_action")
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("success", True))

    def test_manage_virtual_desktop_valid_mocked(self):
        from tools.os_tools import manage_virtual_desktop

        with patch("tools.gui_tools.press_keys", return_value={"success": True}) as mock_pk:
            result = manage_virtual_desktop(action="new")
            self.assertTrue(result.get("success"))
            mock_pk.assert_called_once_with(["ctrl", "win", "d"])

    def test_open_windows_setting(self):
        from tools.os_tools import open_windows_setting

        with patch("tools.os_tools.subprocess.run") as mock_run:
            result = open_windows_setting(setting_name="display")
            self.assertTrue(result.get("success"))
            self.assertEqual(result.get("uri"), "ms-settings:display")
            mock_run.assert_called_once()

    def test_open_windows_setting_unknown(self):
        from tools.os_tools import open_windows_setting

        with patch("tools.os_tools.subprocess.run") as mock_run:
            result = open_windows_setting(setting_name="nonexistent_setting_xyz")
            self.assertTrue(result.get("success"))
            self.assertEqual(result.get("uri"), "ms-settings:")
            mock_run.assert_called_once()


class TestFilesystemToolsEnhancements(unittest.TestCase):
    """Tests for new filesystem tools."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tars_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_or_append_file_create(self):
        from tools.filesystem_tools import write_or_append_file

        test_file = str(Path(self.test_dir) / "test_write.txt")
        result = write_or_append_file(path=test_file, content="Hello TARS", append=False)
        self.assertTrue(result.get("success"), f"write_or_append_file failed: {result}")
        self.assertEqual(result.get("mode"), "written")
        self.assertTrue(Path(test_file).exists())
        self.assertEqual(Path(test_file).read_text(encoding="utf-8"), "Hello TARS")

    def test_write_or_append_file_append(self):
        from tools.filesystem_tools import write_or_append_file

        test_file = str(Path(self.test_dir) / "test_append.txt")
        Path(test_file).write_text("Line 1\n", encoding="utf-8")
        result = write_or_append_file(path=test_file, content="Line 2\n", append=True)
        self.assertTrue(result.get("success"), f"append failed: {result}")
        self.assertEqual(result.get("mode"), "appended")
        content = Path(test_file).read_text(encoding="utf-8")
        self.assertIn("Line 1", content)
        self.assertIn("Line 2", content)

    def test_write_file_protected_path(self):
        from tools.filesystem_tools import write_or_append_file

        result = write_or_append_file(path=r"C:\Windows\System32\evil.txt", content="bad", append=False)
        self.assertFalse(result.get("success"), "Should block writes to protected paths")
        self.assertIn("protected", result.get("error", "").lower())

    def test_copy_or_move_item_copy(self):
        from tools.filesystem_tools import copy_or_move_item

        src = Path(self.test_dir) / "source.txt"
        dst = Path(self.test_dir) / "dest.txt"
        src.write_text("copy me", encoding="utf-8")

        result = copy_or_move_item(source=str(src), destination=str(dst), move=False)
        self.assertTrue(result.get("success"), f"copy failed: {result}")
        self.assertTrue(src.exists(), "Source should still exist after copy")
        self.assertTrue(dst.exists(), "Destination should exist after copy")
        self.assertEqual(dst.read_text(encoding="utf-8"), "copy me")

    def test_copy_or_move_item_move(self):
        from tools.filesystem_tools import copy_or_move_item

        src = Path(self.test_dir) / "moveme.txt"
        dst = Path(self.test_dir) / "moved.txt"
        src.write_text("move me", encoding="utf-8")

        result = copy_or_move_item(source=str(src), destination=str(dst), move=True)
        self.assertTrue(result.get("success"), f"move failed: {result}")
        self.assertFalse(src.exists(), "Source should not exist after move")
        self.assertTrue(dst.exists(), "Destination should exist after move")

    def test_zip_or_unzip(self):
        from tools.filesystem_tools import zip_or_unzip

        payload_dir = Path(self.test_dir) / "payload"
        payload_dir.mkdir()
        (payload_dir / "a.txt").write_text("file a", encoding="utf-8")
        (payload_dir / "b.txt").write_text("file b", encoding="utf-8")
        zip_path = str(Path(self.test_dir) / "test.zip")

        result = zip_or_unzip(source=str(payload_dir), destination=zip_path, action="zip")
        self.assertTrue(result.get("success"), f"zip failed: {result}")
        self.assertTrue(Path(zip_path).exists())

        extract_dir = str(Path(self.test_dir) / "extracted")
        result2 = zip_or_unzip(source=zip_path, destination=extract_dir, action="unzip")
        self.assertTrue(result2.get("success"), f"unzip failed: {result2}")
        self.assertTrue((Path(extract_dir) / "payload" / "a.txt").exists())

    def test_zip_blocks_path_traversal(self):
        from tools.filesystem_tools import zip_or_unzip

        malicious_zip = Path(self.test_dir) / "evil.zip"
        with zipfile.ZipFile(malicious_zip, "w") as zf:
            zf.writestr("../../escaped.txt", "pwned")

        extract_dir = Path(self.test_dir) / "safe_out"
        result = zip_or_unzip(source=str(malicious_zip), destination=str(extract_dir), action="unzip")
        self.assertFalse(result.get("success"))
        self.assertIn("traversal", result.get("error", "").lower())


class TestBrowserToolsEnhancements(unittest.TestCase):
    """Tests for weather and new Playwright browser automation tools."""

    def test_get_weather(self):
        from tools.browser_tools import get_weather

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "current_condition": [{
                "temp_C": "22",
                "FeelsLikeC": "21",
                "humidity": "55",
                "windspeedKmph": "12",
                "weatherDesc": [{"value": "Partly cloudy"}],
            }],
            "nearest_area": [{
                "areaName": [{"value": "London"}],
                "country": [{"value": "United Kingdom"}],
            }],
        }
        with patch("tools.browser_tools.requests.get", return_value=mock_resp):
            result = get_weather(location="London")
            self.assertTrue(result.get("success"))
            self.assertIn("London", result.get("message", ""))
            self.assertEqual(result.get("temperature_c"), "22")

    def test_browser_type_scroll_history_and_tabs(self):
        import tools.browser_automation as ba

        mock_locator = MagicMock()
        mock_nth = MagicMock()
        mock_locator.nth.return_value = mock_nth

        mock_page1 = MagicMock()
        mock_page1.is_closed.return_value = False
        mock_page1.url = "https://example.com"
        mock_page1.title.return_value = "Example"
        mock_page1.locator.return_value = mock_locator

        mock_page2 = MagicMock()
        mock_page2.is_closed.return_value = False
        mock_page2.url = "https://example.org"
        mock_page2.title.return_value = "Second Tab"

        mock_context = MagicMock()
        mock_context.pages = [mock_page1, mock_page2]

        with patch.object(ba, "_ensure_browser", return_value=mock_page1):
            ba._context = mock_context
            ba._page = mock_page1

            t_res = ba.browser_type("input[name=q]", "hello tars", press_enter=True)
            self.assertTrue(t_res["success"])
            mock_nth.fill.assert_called_once_with("hello tars", timeout=5000)
            mock_nth.press.assert_called_once_with("Enter")

            s_res = ba.browser_scroll(direction="down", amount=400)
            self.assertTrue(s_res["success"])
            mock_page1.mouse.wheel.assert_called_once_with(0, 400)

            h_res = ba.browser_history_nav(action="back")
            self.assertTrue(h_res["success"])
            mock_page1.go_back.assert_called_once()

            tabs_res = ba.browser_get_tabs()
            self.assertTrue(tabs_res["success"])
            self.assertEqual(tabs_res["count"], 2)

            sw_res = ba.browser_switch_tab(1)
            self.assertTrue(sw_res["success"])
            self.assertEqual(sw_res["title"], "Second Tab")
            mock_page2.bring_to_front.assert_called_once()


class TestMemoryAndSchedulerEnhancements(unittest.TestCase):
    """Tests for forget_fact, memory search, and flexible reminder units."""

    def test_forget_fact(self):
        from core.memory import MemoryStore, remember_fact, forget_fact, set_default_memory

        mem = MemoryStore(db_path=":memory:")
        set_default_memory(mem)

        remember_fact(key="test_key_forget", value="test_value")
        self.assertIn("test_key_forget", mem.get_all_preferences())

        result = forget_fact(key="test_key_forget")
        self.assertTrue(result.get("success"), f"forget_fact failed: {result}")
        self.assertNotIn("test_key_forget", mem.get_all_preferences())

    def test_forget_nonexistent_key(self):
        from core.memory import MemoryStore, forget_fact, set_default_memory

        mem = MemoryStore(db_path=":memory:")
        set_default_memory(mem)

        result = forget_fact(key="nonexistent_key_xyz")
        self.assertFalse(result.get("success"))

    def test_search_history(self):
        from core.memory import MemoryStore

        mem = MemoryStore(db_path=":memory:")
        mem.add_message("user", "check docker containers")
        mem.add_message("assistant", "Listing running containers.")
        hits = mem.search_history("docker")
        self.assertEqual(len(hits), 1)
        self.assertIn("docker", hits[0]["content"])

    def test_set_reminder_units(self):
        from core.scheduler import set_reminder

        fake_scheduler = MagicMock()
        fake_scheduler.add_reminder.return_value = {"id": "rem_1", "due_time": "12:00 PM"}

        with patch("core.scheduler.get_default_scheduler", return_value=fake_scheduler):
            res_sec = set_reminder("stretch", seconds=30)
            self.assertTrue(res_sec["success"])
            fake_scheduler.add_reminder.assert_called_with("stretch", 30.0)

            res_hr = set_reminder("standup", hours=2)
            self.assertTrue(res_hr["success"])
            fake_scheduler.add_reminder.assert_called_with("standup", 7200.0)


class TestFastPaths(unittest.TestCase):
    """Tests for new fast-path branches in brain.py."""

    def setUp(self):
        with patch("core.brain.TARSAgent._init_llm_client"):
            self.agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()

    def test_weather_fast_path(self):
        with patch.object(self.agent, "execute_tool", return_value={"success": True, "message": "Sunny 22°C"}):
            result = self.agent._autonomous_local_execute("weather")
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])

    def test_top_processes_fast_path(self):
        mock_result = {"success": True, "processes": [], "summary": "none"}
        with patch.object(self.agent, "execute_tool", return_value=mock_result):
            result = self.agent._autonomous_local_execute("top processes")
            self.assertIsNotNone(result)

    def test_snap_left_and_right_fast_paths(self):
        with patch.object(self.agent, "execute_tool", return_value={"success": True}) as mock_exec:
            r_left = self.agent._autonomous_local_execute("snap left")
            self.assertIsNotNone(r_left)
            mock_exec.assert_called_with("snap_window", {"direction": "left"})

            r_right = self.agent._autonomous_local_execute("snap right")
            self.assertIsNotNone(r_right)
            mock_exec.assert_called_with("snap_window", {"direction": "right"})

    def test_focus_window_fast_path_passes_title_or_process(self):
        mock_res = {"success": True, "window": "Notepad", "message": "Focused window 'Notepad'."}
        with patch.object(self.agent, "execute_tool", return_value=mock_res) as mock_exec:
            result = self.agent._autonomous_local_execute("focus on notepad")
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            mock_exec.assert_called_once_with("focus_window", {"title_or_process": "notepad"})

    def test_new_virtual_desktop_and_task_view_fast_paths(self):
        with patch.object(self.agent, "execute_tool", return_value={"success": True}):
            self.assertIsNotNone(self.agent._autonomous_local_execute("new desktop"))
            self.assertIsNotNone(self.agent._autonomous_local_execute("task view"))

    def test_brightness_and_settings_fast_paths(self):
        with patch.object(self.agent, "execute_tool", return_value={"success": True}) as mock_exec:
            self.assertIsNotNone(self.agent._autonomous_local_execute("set screen brightness to 70%"))
            mock_exec.assert_called_with("set_screen_brightness", {"level_percent": 70})

            self.assertIsNotNone(self.agent._autonomous_local_execute("wifi settings"))
            mock_exec.assert_called_with("open_windows_setting", {"setting_name": "wifi"})

    def test_forget_fact_and_list_memories_fast_paths(self):
        mock_result = {"success": True, "message": "Forgotten."}
        with patch.object(self.agent, "_execute_tool_inner", return_value=mock_result), \
             patch.object(self.agent, "_log_tool_call"):
            result = self.agent._autonomous_local_execute("forget my favorite color")
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])

        with patch.object(self.agent.memory, "get_all_preferences", return_value={"name": "Shafy"}):
            result = self.agent._autonomous_local_execute("list memories")
            self.assertIsNotNone(result)
            self.assertIn("Shafy", result["response"])

    def test_active_window_timer_clipboard_and_snip_fast_paths(self):
        with patch.object(self.agent, "execute_tool", return_value={"success": True, "title": "Code", "process_name": "Code.exe"}):
            res = self.agent._autonomous_local_execute("active window")
            self.assertIsNotNone(res)
            self.assertIn("Code", res["response"])

        with patch.object(self.agent, "execute_tool", return_value={"success": True, "message": "Timer set."}):
            self.assertIsNotNone(self.agent._autonomous_local_execute("set timer for 30 seconds"))

        with patch.object(self.agent, "execute_tool", return_value={"success": True}) as mock_exec:
            self.assertIsNotNone(self.agent._autonomous_local_execute("copy hello world to clipboard"))
            mock_exec.assert_called_with("write_clipboard", {"text": "hello world"})
            self.assertIsNotNone(self.agent._autonomous_local_execute("snip"))


class TestProviderFailover(unittest.TestCase):
    """Tests for multi-provider failover and tool exposure in orchestrator & llm_provider."""

    def test_tools_for_provider_ollama_subset(self):
        with patch("core.brain.TARSAgent._init_llm_client"):
            agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
            orch = agent.orchestrator

        ollama_tools = orch._tools_for_provider(provider_name="ollama")
        cloud_tools = orch._tools_for_provider(provider_name="gemini")

        self.assertLess(len(ollama_tools), len(cloud_tools))
        cloud_names = {t.__name__ for t in cloud_tools}
        self.assertIn("forget_fact", cloud_names)
        self.assertIn("snap_window", cloud_names)

    def test_fallback_providers_disabled_via_env(self):
        with patch("core.brain.TARSAgent._init_llm_client"):
            agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
            orch = agent.orchestrator

        with patch.dict(os.environ, {"TARS_DISABLE_PROVIDER_FAILOVER": "1"}):
            self.assertEqual(orch._get_fallback_providers(), [])

    def test_failover_triggers_only_when_no_side_effects(self):
        from core.llm_provider import LLMProvider

        with patch("core.brain.TARSAgent._init_llm_client"):
            agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
            orch = agent.orchestrator

        primary = MagicMock(spec=LLMProvider)
        primary.provider = "gemini"
        primary.available.return_value = True
        primary.run_tool_agent_loop.return_value = {"success": False, "response": "LLM unavailable: 503", "steps": []}

        secondary = MagicMock(spec=LLMProvider)
        secondary.provider = "openrouter"
        secondary.available.return_value = True
        secondary.run_tool_agent_loop.return_value = {
            "success": True,
            "response": "Done via fallback.",
            "steps": [{"tool": "get_system_telemetry", "args": {}, "result": {"success": True}}],
        }

        orch.llm = primary
        with patch.object(orch, "_get_fallback_providers", return_value=[secondary]):
            res = orch.run_orchestrated_task("summarize system health")
            self.assertTrue(res["success"])
            self.assertEqual(res["response"], "Done via fallback.")
            secondary.run_tool_agent_loop.assert_called_once()

        # Now ensure failover does NOT run if primary already executed tool side effects
        secondary.run_tool_agent_loop.reset_mock()
        primary.run_tool_agent_loop.return_value = {
            "success": False,
            "response": "Partial failure",
            "steps": [{"tool": "create_file_or_folder", "args": {}, "result": {"success": True}}],
        }
        with patch.object(orch, "_get_fallback_providers", return_value=[secondary]):
            res2 = orch.run_orchestrated_task("create something")
            self.assertFalse(res2["success"])
            secondary.run_tool_agent_loop.assert_not_called()

    def test_switch_provider(self):
        from core.llm_provider import LLMProvider

        llm = LLMProvider(provider="ollama")
        with patch.object(llm, "_init_client"), patch.object(llm, "available", return_value=True), \
             patch.object(llm, "unavailable_reason", return_value=""):
            res = llm.switch_provider("openrouter", "openai/gpt-4o")
            self.assertTrue(res["success"])
            self.assertEqual(llm.provider, "openrouter")
            self.assertEqual(llm.model, "openai/gpt-4o")

            bad = llm.switch_provider("nonexistent")
            self.assertFalse(bad["success"])


class TestVisionCascade(unittest.TestCase):
    """Tests for multi-provider inspect_screen vision cascade."""

    def test_inspect_screen_offline_fallback(self):
        from tools.gui_tools import inspect_screen

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            shot_path = tf.name
        try:
            with patch("tools.gui_tools.take_screenshot", return_value={"success": True, "filepath": shot_path}), \
                 patch("tools.ui_automation.get_active_window", return_value={"success": True, "title": "Editor"}), \
                 patch("tools.ui_automation.get_ui_elements", return_value={"success": True, "elements": [{"name": "Save"}, {"name": "Run"}]}), \
                 patch.dict(os.environ, {"GEMINI_API_KEY": "", "OPENROUTER_API_KEY": "", "OMNIROUTE_API_KEY": ""}, clear=False):
                # Also force _try_omniroute_vision to return None so offline fallback runs deterministically
                res = inspect_screen(question="What is visible?")
                self.assertTrue(res["success"])
                self.assertEqual(res["active_window"], "Editor")
                self.assertIn("Editor", res["description"])
        finally:
            if os.path.exists(shot_path):
                os.unlink(shot_path)


class TestDashboardEndpoints(unittest.TestCase):
    """Tests for dashboard API endpoints."""

    def test_tools_providers_switch_and_cancel_endpoints(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client not available")

        from dashboard.server import app
        client = TestClient(app)

        tools_resp = client.get("/api/tools")
        self.assertEqual(tools_resp.status_code, 200)
        t_data = tools_resp.json()
        self.assertTrue(t_data["success"])
        self.assertGreater(t_data["count"], 50)
        self.assertIn("launch_application", t_data["tools"])
        self.assertIn("forget_fact", t_data["tools"])

        prov_resp = client.get("/api/providers")
        self.assertEqual(prov_resp.status_code, 200)
        p_data = prov_resp.json()
        self.assertIn("providers", p_data)
        self.assertIn("current", p_data)

        cancel_resp = client.post("/api/cancel-task")
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertTrue(cancel_resp.json()["success"])


class TestRegressionFixes(unittest.TestCase):
    """Tests covering bugs diagnosed in logs and live memory traces."""

    def test_whatsapp_saying_to_order_and_typo(self):
        agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
        mock_send = MagicMock(return_value={"success": True, "message": "Sent WhatsApp message to reshmaa."})
        agent.tool_registry["send_whatsapp_message"] = mock_send

        res = agent._autonomous_local_execute("send a messg saying hello to reshmaa")
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])
        mock_send.assert_called_once_with(contact_name="reshmaa", message="hello")
        self.assertEqual(agent.last_whatsapp_contact, "reshmaa")

    def test_whatsapp_followup_uses_last_contact(self):
        agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
        mock_send = MagicMock(return_value={"success": True, "message": "Sent."})
        agent.tool_registry["send_whatsapp_message"] = mock_send
        agent.last_whatsapp_contact = "reshmaa"

        res = agent._autonomous_local_execute("send another one saying hello")
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])
        mock_send.assert_called_once_with(contact_name="reshmaa", message="hello")

        mock_send.reset_mock()
        res2 = agent._autonomous_local_execute("send her another hi")
        self.assertIsNotNone(res2)
        self.assertTrue(res2["success"])
        mock_send.assert_called_once_with(contact_name="reshmaa", message="hi")

        # Misheard bare send must still NOT match
        mock_send.reset_mock()
        self.assertIsNone(agent._autonomous_local_execute("send hello how are you"))
        mock_send.assert_not_called()

    def test_honest_llm_result_blocks_yes_send_hallucination_and_keeps_questions(self):
        with patch("core.brain.TARSAgent._init_llm_client"):
            agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
            orch = agent.orchestrator

        # 1. Hallucinated completion after "yes send" with zero tool calls
        hallucinated = {
            "success": True,
            "response": "Done. Message sent to Reshmaa.",
            "steps": [],
            "no_tool_calls": True,
        }
        cleaned = orch._honest_llm_result(hallucinated, "yes send")
        self.assertFalse(cleaned["success"])
        self.assertIn("didn't run anything", cleaned["response"])
        self.assertNotIn("Message sent", cleaned["response"])

        # 1b. Hallucinated completion with trailing "Anything else?" must ALSO be blocked
        hallucinated_trailing_q = {
            "success": True,
            "response": "Message sent to Reshmaa. Anything else?",
            "steps": [],
            "no_tool_calls": True,
        }
        cleaned_q = orch._honest_llm_result(hallucinated_trailing_q, "send reshmaa hi")
        self.assertFalse(cleaned_q["success"])
        self.assertIn("didn't run anything", cleaned_q["response"])
        self.assertNotIn("Message sent", cleaned_q["response"])

        # 2. Question asking for clarification/confirmation should stay visible
        question = {
            "success": True,
            "response": "Should I send 'hello' to Reshmaa? Please confirm.",
            "steps": [],
            "no_tool_calls": True,
        }
        kept = orch._honest_llm_result(question, "send it")
        self.assertFalse(kept["success"])
        self.assertIn("Should I send", kept["response"])

    def test_whatsapp_person_first_skips_bare_article(self):
        agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()
        mock_send = MagicMock(return_value={"success": True, "message": "Sent."})
        agent.tool_registry["send_whatsapp_message"] = mock_send

        self.assertIsNone(agent._autonomous_local_execute("send a messg hello"))
        mock_send.assert_not_called()

        res = agent._autonomous_local_execute("send reshmaa a messg saying hi")
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])
        mock_send.assert_called_once_with(contact_name="reshmaa", message="hi")

    def test_llm_provider_gemini_ignores_omniroute_ai_model(self):
        from core.llm_provider import LLMProvider, OPENROUTER_DEFAULT_MODEL

        with patch.dict(os.environ, {"AI_PROVIDER": "omniroute", "AI_MODEL": "auto/best-fast"}, clear=False):
            gem = LLMProvider(provider="gemini")
            self.assertEqual(gem.model, "gemini-3.1-flash-lite")

        self.assertNotEqual(OPENROUTER_DEFAULT_MODEL, "anthropic/claude-3.5-sonnet")
        self.assertEqual(OPENROUTER_DEFAULT_MODEL, "google/gemini-2.5-flash")

    def test_gemini_config_skips_thinking_budget_for_latest(self):
        from core.llm_provider import LLMProvider

        cfg_latest = LLMProvider._gemini_config("sys", [], 10.0, model_name="gemini-flash-lite-latest")
        self.assertIsNone(getattr(cfg_latest, "thinking_config", None))

        cfg_31 = LLMProvider._gemini_config("sys", [], 10.0, model_name="gemini-3.1-flash-lite")
        self.assertIsNotNone(getattr(cfg_31, "thinking_config", None))

    def test_select_whatsapp_window_prefers_chrome_widget_host(self):
        from tools.interaction_tools import _select_whatsapp_window

        frame = MagicMock()
        frame.element_info.class_name = "WinUIDesktopWin32WindowClass"
        chrome = MagicMock()
        chrome.element_info.class_name = "Chrome_WidgetWin_1"

        self.assertIs(_select_whatsapp_window([frame, chrome]), chrome)
        self.assertIs(_select_whatsapp_window([frame]), frame)

    def test_whatsapp_launch_ignores_headless_background_process(self):
        from tools.os_tools import _launch_target_present, launch_application

        fake_proc = MagicMock()
        fake_proc.info = {"name": "WhatsApp.Root.exe"}

        with patch("tools.os_tools.psutil.process_iter", return_value=[fake_proc]), \
             patch("win32gui.EnumWindows"):
            self.assertFalse(_launch_target_present("whatsapp"))

        with patch("tools.os_tools._has_visible_window", return_value=True), \
             patch("tools.gui_tools.focus_window", return_value={"success": True, "window": "WhatsApp"}) as mock_foc, \
             patch("tools.os_tools.subprocess.Popen") as mock_popen:
            res = launch_application("whatsapp")
            self.assertTrue(res["success"])
            mock_foc.assert_called_once_with("whatsapp")
            mock_popen.assert_not_called()

    def test_openrouter_loop_passes_max_tokens_and_retries_402(self):
        from core.llm_provider import LLMProvider

        llm = LLMProvider(provider="openrouter", model="google/gemini-2.5-flash")
        mock_msg = MagicMock(content="OK", tool_calls=[])
        mock_resp = MagicMock(choices=[MagicMock(message=mock_msg)])
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            Exception("Error code: 402 - requires fewer max_tokens"),
            mock_resp,
        ]
        llm.client = mock_client

        res = llm._run_openai_compatible_loop("hi", "sys", [], lambda n, a: {})
        self.assertTrue(res["success"])
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)
        first_kwargs = mock_client.chat.completions.create.call_args_list[0].kwargs
        second_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
        self.assertLessEqual(first_kwargs["max_tokens"], 2048)
        self.assertEqual(second_kwargs["max_tokens"], 512)

    def test_nvidia_stopword_tool_result_includes_tool_call_id(self):
        """Every tool-role message appended in OpenAI-compatible mode must carry tool_call_id."""
        import json
        from core.llm_provider import LLMProvider

        llm = LLMProvider(provider="nvidia", model="nvidia/nemotron-3.5-lightning-30b-a3b")
        tc = MagicMock()
        tc.id = "call-nvidia-stopword-1"
        tc.function.name = "send_whatsapp_message"
        tc.function.arguments = json.dumps({"contact_name": "hello", "message": "how are you"})

        choice1 = MagicMock()
        choice1.message.content = ""
        choice1.message.tool_calls = [tc]

        choice2 = MagicMock()
        choice2.message.content = "Who should I send that message to?"
        choice2.message.tool_calls = None

        seen_payloads = []

        def fake_create(**kwargs):
            seen_payloads.append(json.loads(json.dumps(kwargs["messages"])))
            return MagicMock(choices=[choice1 if len(seen_payloads) == 1 else choice2])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create
        llm.client = mock_client

        res = llm._run_openai_compatible_loop(
            "send hello how are you",
            "sys",
            [],
            lambda n, a: {"success": True},
            provider_name="NVIDIA",
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["response"], "Who should I send that message to?")
        self.assertEqual(len(seen_payloads), 2)
        tool_msgs = [m for m in seen_payloads[1] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call-nvidia-stopword-1")
        self.assertEqual(tool_msgs[0]["name"], "send_whatsapp_message")

    def test_whatsapp_emoji_honorific_and_default_chats_section(self):
        """Contacts with emojis/honorifics or without a Chats DataItem divider must match cleanly."""
        import tools.interaction_tools as it

        window = MagicMock()
        window.element_info.name = "WhatsApp"
        window.rectangle.return_value = MagicMock(top=0, left=0, right=1920, bottom=1200)

        search_edit = MagicMock()
        search_edit.element_info.name = "Search or start a new chat"
        search_edit.get_value.return_value = "reshmaa"

        row_amma = MagicMock()
        row_amma.element_info.name = "Amma 💗 3:33 pm hi"
        row_amma.descendants.return_value = []

        row_reshma = MagicMock()
        row_reshma.element_info.name = "Dr. Reshmaa 🫴🏻 🤍 4:36 pm Ena pannuva"
        row_reshma.descendants.return_value = []

        header_text = MagicMock()
        header_text.element_info.name = "Dr. Reshmaa 🫴🏻 🤍"
        header_text.rectangle.return_value = MagicMock(top=40, left=600, right=900, bottom=70)

        window.descendants.side_effect = [
            [search_edit],
            [row_amma, row_reshma],
            [header_text],
        ]

        with patch("pywinauto.Desktop") as mock_desktop, \
             patch("tools.interaction_tools.copy_to_clipboard", return_value=True), \
             patch("tools.interaction_tools.gui.press_keys"):
            mock_desktop.return_value.windows.return_value = [window]
            res = it.open_whatsapp_chat("reshma")

        self.assertTrue(res["success"], f"Expected match for Dr. Reshmaa, got: {res}")
        row_reshma.click_input.assert_called_once()
        # Unrelated person must still mismatch header verification
        self.assertEqual(it._contact_match_tier("reshmaa", "John Doe"), None)
        # Unread prefix must be stripped cleanly from chat row labels
        self.assertEqual(
            it._extract_chat_name("1 unread message Dr. Reshmaa 🫴🏻 🤍 5:03 pm hello"),
            "Dr. Reshmaa 🫴🏻 🤍",
        )
        fake_edit = MagicMock()
        fake_edit.get_value.return_value = "Shafyy\nShafyy\nShafyy"
        self.assertTrue(it._search_edit_matches(fake_edit, "shafyy"))
        fake_edit.get_value.return_value = "Reshma\nReshma"
        self.assertFalse(it._search_edit_matches(fake_edit, "shafyy"))

    def test_switch_provider_and_new_fast_paths(self):
        from core.llm_provider import LLMProvider

        llm = LLMProvider(provider="gemini")
        with patch.object(llm, "_init_client"), patch.object(llm, "available", return_value=True), \
             patch.object(llm, "unavailable_reason", return_value=""):
            self.assertTrue(llm.switch_provider("nvidia")["success"])
            self.assertTrue(llm.switch_provider("groq")["success"])

        with patch("core.brain.TARSAgent._init_llm_client"):
            agent = __import__("core.brain", fromlist=["TARSAgent"]).TARSAgent()

        cloud_names = {t.__name__ for t in agent.orchestrator._tools_for_provider(provider_name="nvidia")}
        self.assertIn("close_active_window", cloud_names)
        self.assertIn("search_emails", cloud_names)

        with patch.object(agent, "execute_tool", return_value={"success": True}) as mock_exec:
            self.assertIsNotNone(agent._autonomous_local_execute("make the video full screen"))
            mock_exec.assert_called_with("smart_click", {"target": "fullscreen"})

            self.assertIsNotNone(agent._autonomous_local_execute("close another one too"))
            mock_exec.assert_called_with("close_active_window", {})

            self.assertIsNotNone(agent._autonomous_local_execute("send a message to amma telling hello"))
            mock_exec.assert_called_with("send_whatsapp_message", {"contact_name": "amma", "message": "hello"})

            res_wa = agent._autonomous_local_execute(
                "Send a message to Amma saying hi hello how are you when will you meet me and where are you."
            )
            self.assertIsNotNone(res_wa)
            mock_exec.assert_called_with(
                "send_whatsapp_message",
                {"contact_name": "amma", "message": "hi hello how are you when will you meet me and where are you"},
            )


class TestVoiceListenerSpeedAndFallback(unittest.TestCase):
    def _make_speech_audio(self, seconds: float = 1.0, amplitude: int = 1200):
        import struct
        import speech_recognition as sr

        count = int(16000 * seconds)
        raw = struct.pack("<" + "h" * count, *([amplitude if i % 2 == 0 else -amplitude for i in range(count)]))
        return sr.AudioData(raw, 16000, 2)

    def test_acoustic_gate_skips_too_short_or_quiet_audio(self):
        from core.voice_listener import VoiceListener, _is_whisper_hallucination

        vl = VoiceListener(energy_threshold=250, stt_engine="google")
        vl._recognizer = MagicMock()
        vl._microphone = MagicMock()
        quiet_audio = self._make_speech_audio(seconds=0.8, amplitude=25)
        vl._recognizer.listen.return_value = quiet_audio

        text, status = vl.listen_once(timeout=3)
        self.assertIsNone(text)
        self.assertEqual(status, "timeout")
        vl._recognizer.recognize_google.assert_not_called()

        self.assertTrue(_is_whisper_hallucination("Thank you.", peak_rms=260, threshold=250, duration=0.8))
        self.assertFalse(_is_whisper_hallucination("open whatsapp", peak_rms=260, threshold=250, duration=0.8))
        self.assertFalse(_is_whisper_hallucination("Thank you.", peak_rms=900, threshold=250, duration=1.5))

    def test_auto_engine_uses_groq_whisper_then_falls_back_to_google(self):
        from core.voice_listener import VoiceListener

        vl = VoiceListener(energy_threshold=200, stt_engine="auto")
        vl._recognizer = MagicMock()
        vl._microphone = MagicMock()
        vl._whisper_failed = True  # skip local whisper in unit test
        speech_audio = self._make_speech_audio(seconds=1.0, amplitude=1000)
        vl._recognizer.listen.return_value = speech_audio

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"text": "open whatsapp"}
        vl._http_session = MagicMock()
        vl._http_session.post.return_value = mock_resp

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test_123"}):
            text, status = vl.listen_once(timeout=3)

        self.assertEqual(status, "ok")
        self.assertEqual(text, "open whatsapp")
        self.assertEqual(vl._stt_timings["engine"], "groq")
        vl._recognizer.recognize_google.assert_not_called()

        # When Groq hits rate limit, falls back seamlessly to 16kHz Google STT
        vl._http_session.post.return_value = MagicMock(status_code=429, text="rate limit")
        vl._recognizer.recognize_google.return_value = "close tab"
        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test_123"}):
            text2, status2 = vl.listen_once(timeout=3)

        self.assertEqual(status2, "ok")
        self.assertEqual(text2, "close tab")
        self.assertEqual(vl._stt_timings["engine"], "google")

    def test_groq_200_hallucination_does_not_cascade_into_slow_fallbacks(self):
        from core.voice_listener import VoiceListener

        vl = VoiceListener(energy_threshold=200, stt_engine="auto")
        vl._recognizer = MagicMock()
        vl._microphone = MagicMock()
        vl._recognizer.listen.return_value = self._make_speech_audio(seconds=0.8, amplitude=240)
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {"text": "Thank you."}
        vl._http_session = MagicMock()
        vl._http_session.post.return_value = resp_200

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test_123"}), \
             patch.object(vl, "_transcribe_whisper") as mock_fw:
            text, status = vl.listen_once(timeout=3)
        self.assertIsNone(text)
        self.assertEqual(status, "unrecognized")
        mock_fw.assert_not_called()
        vl._recognizer.recognize_google.assert_not_called()

    def test_calibrate_updates_floor_above_legacy_800_cap(self):
        from core.voice_listener import VoiceListener

        vl = VoiceListener(energy_threshold=250, stt_engine="google")
        vl._recognizer = MagicMock()
        vl._microphone = MagicMock()
        vl._microphone.CHUNK = 1024
        vl._microphone.SAMPLE_RATE = 16000

        def fake_adjust(source, duration=0.4):
            vl._recognizer.energy_threshold = 920.0

        vl._recognizer.adjust_for_ambient_noise.side_effect = fake_adjust
        thresh = vl.calibrate(duration=0.4)
        self.assertGreaterEqual(thresh, 920.0)
        self.assertEqual(vl.energy_threshold, int(thresh))


class TestFemaleSingleVoiceSpeaker(unittest.TestCase):
    def test_rejects_male_edge_voice_and_selects_female_sapi(self):
        from core.voice_speaker import VoiceSpeaker, DEFAULT_FEMALE_VOICE, _pick_female_sapi_voice

        with patch.dict("os.environ", {"TARS_VOICE": "en-US-GuyNeural"}):
            sp = VoiceSpeaker()
        self.assertEqual(sp.voice_name, DEFAULT_FEMALE_VOICE)

        with patch.dict("os.environ", {"TARS_VOICE": "en-US-JennyNeural"}):
            sp_custom = VoiceSpeaker()
        self.assertEqual(sp_custom.voice_name, "en-US-JennyNeural")

        david = MagicMock()
        david.GetDescription.return_value = "Microsoft David Desktop - English (United States)"
        zira = MagicMock()
        zira.GetDescription.return_value = "Microsoft Zira Desktop - English (United States)"
        fake_sapi = MagicMock()
        fake_sapi.GetVoices.return_value = [david, zira]
        self.assertIs(_pick_female_sapi_voice(fake_sapi), zira)

    def test_powershell_fallback_enforces_female_voice_and_stops_prior_utterances(self):
        from core.voice_speaker import VoiceSpeaker

        sp = VoiceSpeaker(fast_voice=False)
        uid1 = sp._begin_utterance()
        uid2 = sp._begin_utterance()
        self.assertGreater(uid2, uid1)

        with patch("win32com.client.Dispatch", side_effect=RuntimeError("no com")), \
             patch("subprocess.run") as mock_sub:
            ok = sp._speak_sapi("Hello there", async_mode=True)
        self.assertTrue(ok)
        cmd = mock_sub.call_args[0][0][3]
        self.assertIn("Gender]::Female", cmd)
        self.assertIn("Zira", cmd)

    def test_neural_speak_wires_echo_aware_barge_in_and_chunked_playback(self):
        from unittest.mock import AsyncMock
        from core.voice_speaker import VoiceSpeaker, _split_speech_chunks

        chunks = _split_speech_chunks(
            "Opening WhatsApp now. Searching for Reshmaa across your recent chats and checking your messages. Ready to type.",
            max_chars=70,
        )
        self.assertGreaterEqual(len(chunks), 2)

        sp = VoiceSpeaker(fast_voice=False)
        fake_monitor = MagicMock()
        fake_monitor.is_triggered.return_value = False
        fake_listener = MagicMock()
        fake_listener.create_barge_in_monitor.return_value = fake_monitor

        with patch.object(sp, "_synthesize_edge_tts", new=AsyncMock(return_value=True)), \
             patch("core.voice_speaker.os.path.getsize", return_value=1024), \
             patch.object(sp, "_play_audio_pygame", return_value=True) as mock_play:
            interrupted = sp.speak("Opening WhatsApp now.", blocking=True, listener=fake_listener)

        self.assertFalse(interrupted)
        fake_listener.create_barge_in_monitor.assert_called_once()
        fake_monitor.start.assert_called_once()
        fake_monitor.stop.assert_called_once()
        mock_play.assert_called_once()
        self.assertEqual(mock_play.call_args.kwargs.get("interrupt_check"), fake_monitor.is_triggered)
        self.assertTrue(mock_play.call_args.kwargs.get("check_keyboard"))

    def test_stop_handles_unhashable_com_sapi_dispatch(self):
        from core.voice_speaker import VoiceSpeaker

        class _UnhashableSapi:
            __hash__ = None

            def __init__(self):
                self.calls = []

            def Speak(self, text, flags):
                self.calls.append((text, flags))

        sp = VoiceSpeaker(fast_voice=False)
        com_obj = _UnhashableSapi()
        sp._sapi_speaker = com_obj
        sp._sapi_local.speaker = com_obj
        sp.stop()
        self.assertEqual(com_obj.calls, [("", 2)])

    def test_edge_tts_retries_transient_error_and_drops_empty_symbols(self):
        import asyncio
        import os
        import tempfile
        from core.voice_speaker import VoiceSpeaker

        sp = VoiceSpeaker(fast_voice=False)
        self.assertEqual(sp._clean_text_for_speech("... 🤍"), "")
        self.assertEqual(sp._clean_text_for_speech("Opened Dr. Reshmaa 🫴🏻 🤍."), "Opened Dr. Reshmaa.")

        calls = {"n": 0}

        class _FakeComm:
            def __init__(self, **kwargs):
                pass

            async def save(self, out_path):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient websocket drop")
                with open(out_path, "wb") as f:
                    f.write(b"ID3" + b"\x00" * 64)

        fd, tmp = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            with patch("edge_tts.Communicate", _FakeComm):
                ok = asyncio.run(sp._synthesize_edge_tts("Hello world.", tmp))
            self.assertTrue(ok)
            self.assertEqual(calls["n"], 2)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_ansi_escape_sequences_ignored_and_echo_barge_in_discriminates_speech(self):
        import numpy as np
        from core.voice_speaker import VoiceSpeaker
        from core.voice_listener import VoiceBargeInMonitor

        # Terminal focus escape sequence (\x1b[I) must NOT cut off TTS playback
        seq = [b"\x1b", b"[", b"I"]
        with patch("msvcrt.kbhit", side_effect=lambda: bool(seq)), \
             patch("msvcrt.getch", side_effect=lambda: seq.pop(0)):
            self.assertFalse(VoiceSpeaker._poll_key_interrupt())

        # Intentional Spacebar press DOES interrupt
        space_seq = [b" "]
        with patch("msvcrt.kbhit", side_effect=lambda: bool(space_seq)), \
             patch("msvcrt.getch", side_effect=lambda: space_seq.pop(0)):
            self.assertTrue(VoiceSpeaker._poll_key_interrupt())

        # Echo-aware VoiceBargeInMonitor: rejects speaker bleed proportional to ref_pcm,
        # but triggers when overlapping human voice exceeds echo floor + VAD
        ref = np.full(16000, 0.15, dtype=np.float32)  # TARS loud speaker playback
        mon = VoiceBargeInMonitor(MagicMock(), MagicMock(), energy_threshold=260, required_frames=2)
        mon.set_reference_audio(ref, lambda: 500.0)
        fake_vad = MagicMock(return_value=np.array([0.85], dtype=np.float32))
        mon._vad_model = fake_vad
        mon._vad_checked = True

        # Pure speaker echo (~25% of 0.15 = 0.0375): must NOT trigger
        echo_frame = np.full(512, 0.035, dtype=np.float32)
        self.assertFalse(mon._evaluate_frame(echo_frame, pos_ms=500.0, use_vad=True))

        # Loud human speech over speaker playback (0.12 > 0.15 * 0.40): MUST trigger
        user_frame = np.full(512, 0.12, dtype=np.float32)
        self.assertTrue(mon._evaluate_frame(user_frame, pos_ms=500.0, use_vad=True))

    def test_barge_in_lag_echo_subtraction_stateful_vad_and_null_ref_safety(self):
        import time as _time
        import numpy as np
        from core.voice_listener import VoiceBargeInMonitor

        # 1. Delayed AC speaker bleed (18% at 20ms lag) is subtracted cleanly while stateful VAD runs
        t = np.arange(16000, dtype=np.float32) / 16000.0
        ref = 0.22 * np.sin(2.0 * np.pi * 440.0 * t) + 0.10 * np.sin(2.0 * np.pi * 780.0 * t)

        fake_session = MagicMock()

        def _fake_session_run(_out, feed):
            self.assertEqual(feed["input"].shape, (1, 576))
            self.assertEqual(feed["h"].shape, (1, 1, 128))
            self.assertEqual(feed["c"].shape, (1, 1, 128))
            return np.array([[0.82]], dtype=np.float32), feed["h"] + 0.1, feed["c"] + 0.1

        fake_session.run.side_effect = _fake_session_run
        fake_vad = MagicMock()
        fake_vad.session = fake_session

        mon = VoiceBargeInMonitor(MagicMock(), MagicMock(), energy_threshold=260, required_frames=3)
        mon.set_reference_audio(ref, lambda: 500.0)
        mon._vad_model = fake_vad
        mon._vad_checked = True

        # Pure speaker echo delayed by 320 samples (20ms) relative to pos_ms=500ms:
        # evaluate across warmup frames (like grace_period) so _echo_gain adapts cleanly to 0.20 floor
        echo_delayed = 0.18 * ref[8000 - 320 : 8000 - 320 + 512]
        for _ in range(6):
            self.assertFalse(mon._evaluate_frame(echo_delayed, pos_ms=500.0, use_vad=True))
        self.assertEqual(fake_session.run.call_count, 6)
        self.assertLess(mon._echo_gain, 0.11)

        # Overlapping user speech (~1275 int16 RMS at non-aligned frequency) triggers reliably
        ut = np.arange(512, dtype=np.float32) / 16000.0
        user_voice = 0.055 * np.sin(2.0 * np.pi * 230.0 * ut)
        mixed = echo_delayed + user_voice
        self.assertTrue(mon._evaluate_frame(mixed, pos_ms=500.0, use_vad=True))
        # Stateful LSTM hidden state advanced continuously across evaluated frames
        last_call_feed = fake_session.run.call_args_list[-1][0][1]
        self.assertAlmostEqual(float(last_call_feed["h"][0, 0, 0]), 0.6, places=4)

        # Trailing chunk-end pos_ms == -1 after valid playback position must NOT trigger false reverb
        self.assertFalse(mon._evaluate_frame(user_voice, pos_ms=-1.0, use_vad=True))

        # 2. Background _run() with ref_audio=None (synthesizing chunk 0 / SAPI) must not crash
        #    on missing _microphone attribute and must detect sustained speech after grace_period
        class _FakeSource:
            def __init__(self, frames):
                self._frames = list(frames)
                self.stream = self

            def read(self, _chunk):
                if self._frames:
                    return self._frames.pop(0)
                _time.sleep(0.01)
                return b""

        class _FakeMic:
            def __init__(self, frames):
                self._src = _FakeSource(frames)

            def __enter__(self):
                return self._src

            def __exit__(self, *args):
                return False

        loud_pcm = (np.full(1024, 1800, dtype=np.int16)).tobytes()
        mic = _FakeMic([loud_pcm] * 8)
        bg_mon = VoiceBargeInMonitor(
            MagicMock(),
            mic,
            energy_threshold=300,
            grace_period=0.0,
            required_frames=2,
            ref_audio=None,
        )
        bg_mon.start()
        deadline = _time.time() + 1.5
        while not bg_mon.is_triggered() and _time.time() < deadline:
            _time.sleep(0.02)
        triggered = bg_mon.is_triggered()
        bg_mon.stop()
        self.assertTrue(triggered)


if __name__ == "__main__":
    unittest.main()
