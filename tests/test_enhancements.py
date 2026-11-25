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


if __name__ == "__main__":
    unittest.main()
