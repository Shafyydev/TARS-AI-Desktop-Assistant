"""Unit tests for TARS execution tools, voice components, and safety layer."""
import unittest
from unittest.mock import MagicMock
from tools.safety import SafetyValidator
from tools.terminal_tools import run_powershell
from tools.os_tools import get_system_telemetry
from tools.gui_tools import get_screen_dimensions
from core.memory import MemoryStore
from core.voice_speaker import VoiceSpeaker
from core.voice_listener import VoiceListener
from core.brain import TARSAgent


class TestTARS(unittest.TestCase):

    def setUp(self):
        self.safety = SafetyValidator()
        self.memory = MemoryStore(":memory:")
        self.speaker = VoiceSpeaker()
        self.listener = VoiceListener()
        self.agent = TARSAgent()
        self.agent.set_confirmation_callback(lambda tool, args: True)

    def test_safe_command(self):
        is_allowed, req_confirm, reason = self.safety.evaluate_command("Get-Process")
        self.assertTrue(is_allowed)
        self.assertFalse(req_confirm)

    def test_blocked_destructive_command(self):
        is_allowed, req_confirm, reason = self.safety.evaluate_command("rmdir /s /q c:\\windows")
        self.assertFalse(is_allowed)

    def test_confirmation_required_command(self):
        is_allowed, req_confirm, reason = self.safety.evaluate_command("git reset --hard HEAD~1")
        self.assertTrue(is_allowed)
        self.assertTrue(req_confirm)

    def test_powershell_execution(self):
        res = run_powershell("Write-Output 'TARS Online'")
        self.assertTrue(res["success"])
        self.assertIn("TARS Online", res["stdout"])

    def test_telemetry(self):
        telem = get_system_telemetry()
        self.assertIn("cpu_load_percent", telem)
        self.assertIn("ram_usage_percent", telem)
        self.assertGreaterEqual(telem["ram_usage_percent"], 0)

    def test_screen_dimensions(self):
        dims = get_screen_dimensions()
        self.assertIn("width", dims)
        self.assertIn("height", dims)
        self.assertGreater(dims["width"], 0)
        self.assertGreater(dims["height"], 0)

    def test_memory_store(self):
        self.memory.set_user_preference("humor", 85)
        val = self.memory.get_user_preference("humor")
        self.assertEqual(val, 85)

        self.memory.add_message("user", "Hello TARS")
        msgs = self.memory.get_recent_messages(limit=5)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "Hello TARS")

    def test_voice_speaker_text_cleaning(self):
        raw = "Hello **world**! Check [docs](https://google.com) and `python main.py`"
        clean = self.speaker._clean_text_for_speech(raw)
        self.assertNotIn("**", clean)
        self.assertNotIn("`", clean)
        self.assertNotIn("https://", clean)
        self.assertIn("Hello world!", clean)

    def test_voice_listener_wake_word(self):
        is_act, cmd = self.listener.check_wake_word("TARS open chrome")
        self.assertTrue(is_act)
        self.assertEqual(cmd, "open chrome")

        is_act2, cmd2 = self.listener.check_wake_word("hey tars, minimize windows")
        self.assertTrue(is_act2)
    def test_normalize_voice_command(self):
        from core.voice_listener import normalize_voice_command
        self.assertEqual(normalize_voice_command("close this tap"), "close tab")
        self.assertEqual(normalize_voice_command("new tap"), "new tab")
        self.assertEqual(normalize_voice_command("clothes window"), "close window")
        self.assertEqual(normalize_voice_command("write click"), "right click")
        self.assertEqual(normalize_voice_command("ex"), "exit")

    def test_agent_fast_paths(self):
        # 1. Telemetry
        res = self.agent.run_agent_loop("system status")
        self.assertTrue(res["success"])
        self.assertTrue(len(res["steps"]) > 0)
        self.assertEqual(res["steps"][0]["tool"], "get_system_telemetry")

        # 2. Humor query
        res_humor = self.agent.run_agent_loop("what is your humor setting")
        self.assertTrue(res_humor["success"])
        self.assertIn("Humor", res_humor["response"])

        # 3. Switch window
        res_switch = self.agent.run_agent_loop("switch window")
        self.assertTrue(res_switch["success"])
        self.assertEqual(res_switch["steps"][0]["tool"], "switch_window")

    def test_narrowed_regexes_fall_through(self):
        # "help me write an email" should NOT match the canned help/capabilities fast path
        fast_help = self.agent._autonomous_local_execute("help me write an email")
        self.assertIsNone(fast_help)

        # "tell me a funny story" should not trigger canned joke
        fast_story = self.agent._autonomous_local_execute("tell me a story about interstellar travel")
        self.assertIsNone(fast_story)

        # Non-imperative click sentence should not trigger GUI click
        fast_click = self.agent._autonomous_local_execute("click on the link and read the paper and explain it to me")
        self.assertIsNone(fast_click)

        # Conversational tab sentence should not trigger press_keys ctrl+w
        fast_tab = self.agent._autonomous_local_execute("explain what a browser tab is and how it works")
        self.assertIsNone(fast_tab)

    def test_presence_instant_response(self):
        res = self.agent.run_agent_loop("where are you now")
        self.assertTrue(res["success"])
        self.assertIn("system memory", res["response"])

        res_who = self.agent.run_agent_loop("who are you")
        self.assertTrue(res_who["success"])
        self.assertIn("TARS", res_who["response"])

    def test_github_profile_navigation(self):
        res = self.agent.run_agent_loop("go to my profile github")
        self.assertTrue(res["success"])
        self.assertEqual(res["steps"][0]["tool"], "launch_application")
        self.assertIn("Shafyydev", res["steps"][0]["args"]["app_name"])

    def test_gui_controls_fast_paths(self):
        # Click
        res_click = self.agent.run_agent_loop("click")
        self.assertTrue(res_click["success"])
        self.assertEqual(res_click["steps"][0]["tool"], "click_coordinate")

        # Scroll
        res_scroll = self.agent.run_agent_loop("scroll down")
        self.assertTrue(res_scroll["success"])
        self.assertEqual(res_scroll["steps"][0]["tool"], "scroll_screen")

        # Enter key
        res_enter = self.agent.run_agent_loop("press enter")
        self.assertTrue(res_enter["success"])
        self.assertEqual(res_enter["steps"][0]["tool"], "press_keys")

        # New tab
        res_tab = self.agent.run_agent_loop("new tab")
        self.assertTrue(res_tab["success"])
        self.assertEqual(res_tab["steps"][0]["tool"], "press_keys")

        # Maximize
        res_max = self.agent.run_agent_loop("maximize window")
        self.assertTrue(res_max["success"])
        self.assertEqual(res_max["steps"][0]["tool"], "maximize_active_window")

    def test_windows_system_keys(self):
        res_start = self.agent.run_agent_loop("open start menu")
        self.assertTrue(res_start["success"])
        self.assertEqual(res_start["steps"][0]["tool"], "press_keys")

        res_taskmgr = self.agent.run_agent_loop("open task manager")
        self.assertTrue(res_taskmgr["success"])
        self.assertEqual(res_taskmgr["steps"][0]["tool"], "press_keys")

    def test_file_system_operations(self):
        res_create = self.agent.run_agent_loop("create folder test_unit_tars on desktop")
        self.assertTrue(res_create["success"])
        self.assertEqual(res_create["steps"][0]["tool"], "create_file_or_folder")

        res_list = self.agent.run_agent_loop("list files in desktop")
        self.assertTrue(res_list["success"])
        self.assertEqual(res_list["steps"][0]["tool"], "list_directory")

        res_del = self.agent.run_agent_loop("delete folder test_unit_tars on desktop")
        self.assertTrue(res_del["success"])
        self.assertEqual(res_del["steps"][0]["tool"], "delete_file_or_folder")

    def test_network_and_power(self):
        res_ip = self.agent.run_agent_loop("what is my ip")
        self.assertTrue(res_ip["success"])
        self.assertEqual(res_ip["steps"][0]["tool"], "get_network_info")

        res_abort = self.agent.run_agent_loop("cancel shutdown")
        self.assertTrue(res_abort["success"])
        self.assertEqual(res_abort["steps"][0]["tool"], "system_power")

    def test_resolve_system_target(self):
        from tools.os_tools import resolve_system_target
        from pathlib import Path
        
        # Test Drive resolution
        d_path = resolve_system_target("d drive")
        self.assertIsNotNone(d_path)
        self.assertTrue(str(d_path).upper().startswith("D:"))

        c_path = resolve_system_target("c drive")
        self.assertIsNotNone(c_path)
        self.assertTrue(str(c_path).upper().startswith("C:"))

        # Test Known directory resolution
        codehub_p = resolve_system_target("codehub")
        self.assertIsNotNone(codehub_p)
        self.assertIn("codehub", str(codehub_p).lower())

        codehub_folder_p = resolve_system_target("code hub folder")
        self.assertIsNotNone(codehub_folder_p)
        self.assertIn("codehub", str(codehub_folder_p).lower())

    def test_drive_and_folder_navigation(self):
        # Open D drive
        res_d = self.agent.run_agent_loop("open d drive")
        self.assertTrue(res_d["success"])
        self.assertEqual(res_d["steps"][0]["tool"], "open_folder")
        self.assertIn("D:", res_d["steps"][0]["args"]["folder_path"].upper())

        # Go to D drive
        res_god = self.agent.run_agent_loop("go to d drive")
        self.assertTrue(res_god["success"])
        self.assertEqual(res_god["steps"][0]["tool"], "open_folder")

        # Open code hub folder
        res_ch = self.agent.run_agent_loop("open code hub folder")
        self.assertTrue(res_ch["success"])
        self.assertEqual(res_ch["steps"][0]["tool"], "open_folder")

        # Go to codehub
        res_gch = self.agent.run_agent_loop("go to codehub")
        self.assertTrue(res_gch["success"])
        self.assertEqual(res_gch["steps"][0]["tool"], "open_folder")

    def test_explorer_gui_hotkeys(self):
        # Go up one level
        res_up = self.agent.run_agent_loop("go up")
        self.assertTrue(res_up["success"])
        self.assertEqual(res_up["steps"][0]["tool"], "press_keys")
        self.assertEqual(res_up["steps"][0]["args"]["keys"], ["alt", "up"])

        # Create new folder
        res_nf = self.agent.run_agent_loop("new folder")
        self.assertTrue(res_nf["success"])
        self.assertEqual(res_nf["steps"][0]["tool"], "press_keys")
        self.assertEqual(res_nf["steps"][0]["args"]["keys"], ["ctrl", "shift", "n"])

        # Rename
        res_rn = self.agent.run_agent_loop("rename")
        self.assertTrue(res_rn["success"])
        self.assertEqual(res_rn["steps"][0]["tool"], "press_keys")
        self.assertEqual(res_rn["steps"][0]["args"]["keys"], ["f2"])

        # Dismiss popup / cancel dialog
        res_esc = self.agent.run_agent_loop("dismiss popup")
        self.assertTrue(res_esc["success"])
        self.assertEqual(res_esc["steps"][0]["tool"], "press_keys")
        self.assertEqual(res_esc["steps"][0]["args"]["keys"], ["escape"])

        # Focus address bar
        res_addr = self.agent.run_agent_loop("address bar")
        self.assertTrue(res_addr["success"])
        self.assertEqual(res_addr["steps"][0]["tool"], "press_keys")
        self.assertEqual(res_addr["steps"][0]["args"]["keys"], ["alt", "d"])

    def test_voice_interruption_and_silence(self):
        # 1. Silence & stop talking command
        res_stop = self.agent.run_agent_loop("stop talking")
        self.assertTrue(res_stop["success"])
        self.assertEqual(res_stop["response"], "Standing by.")

        res_quiet = self.agent.run_agent_loop("be quiet")
        self.assertTrue(res_quiet["success"])
        self.assertEqual(res_quiet["response"], "Standing by.")

        # 2. Presence & hear me query
        res_hear = self.agent.run_agent_loop("can you hear me")
        self.assertTrue(res_hear["success"])
        self.assertIn("Loud and clear", res_hear["response"])

        # 3. Speaker stop capability
        self.speaker.stop()
        self.assertFalse(self.speaker.is_speaking)

        # 4. Barge-in monitor creation
        monitor = self.listener.create_barge_in_monitor()
        if monitor:
            self.assertFalse(monitor.is_triggered())
            monitor.stop()

    def test_filesystem_tools_and_missing_directory(self):
        from tools.filesystem_tools import list_drives, navigate_to_path, list_directory, read_file
        # 1. List drives
        drives = list_drives()
        self.assertTrue(drives["success"])
        self.assertGreater(drives["count"], 0)

        # 2. Missing directory diagnostics (Requirement: Test D)
        missing_res = navigate_to_path(r"D:\non_existent_cwd_12345")
        self.assertFalse(missing_res["success"])
        self.assertIn("does not exist", missing_res["error"])
        self.assertIn("available_folders_in_parent", missing_res)

        # 3. Read valid file (README.md)
        readme_res = read_file("README.md")
        self.assertTrue(readme_res["success"])
        self.assertIn("TARS", readme_res["content"])

        # 4. Binary safety: cannot read binary formats
        fake_bin_res = read_file("fake_executable.exe")
        self.assertFalse(fake_bin_res["success"])

    def test_compound_filesystem_orchestration(self):
        # Master example: "open D drive, go to the CWD folder, and tell me what files are inside"
        res = self.agent.run_agent_loop("open D drive, go to the CWD folder, and tell me what files are inside")
        self.assertTrue(res["success"])
        self.assertIn("folder(s)", res["response"])
        self.assertIn("file(s)", res["response"])

        # Contextual follow-up: "tell me what you found"
        follow_up = self.agent.run_agent_loop("tell me what you found")
        self.assertTrue(follow_up["success"])
        self.assertIn("found", follow_up["response"])

    def test_cancellation_and_verification_tools(self):
        from tools.verification import verify_action, cancel_current_task, is_task_cancelled, reset_cancellation
        # 1. Verification of real path
        v_real = verify_action("path:D:\\")
        self.assertTrue(v_real["verified"])

        # 2. Verification of non-existent path
        v_fake = verify_action(r"path:D:\definitely_missing_path_xyz")
        self.assertFalse(v_fake["verified"])

        # 3. Cancellation token
        cancel_res = cancel_current_task()
        self.assertTrue(cancel_res["cancelled"])
        self.assertTrue(is_task_cancelled())
        reset_cancellation()
        self.assertFalse(is_task_cancelled())

    def test_approved_command_execution(self):
        from tools.terminal_tools import run_approved_command, get_command_output
        res = run_approved_command("Write-Output 'TARS_VERIFIED'")
        self.assertTrue(res["success"])
        self.assertIn("TARS_VERIFIED", res["stdout"])

        cached = get_command_output()
        self.assertTrue(cached["success"])
        self.assertIn("TARS_VERIFIED", cached["stdout"])

    def test_brave_browser_detection(self):
        from tools.browser_automation import find_brave_executable
        exe = find_brave_executable()
        # If Brave is installed on this PC, verify path exists
        if exe:
            import os
            self.assertTrue(os.path.exists(exe))
            self.assertIn("brave", exe.lower())


class TestConfirmationGate(unittest.TestCase):
    """Tests for Phase 1 Task 1: Central confirmation gate and safety normalization."""

    def setUp(self):
        self.safety = SafetyValidator()
        self.agent = TARSAgent()
        # Default unconfirmed agent

    def test_safety_whitespace_and_case_bypasses(self):
        # Two spaces bypass
        allowed, req, reason = self.safety.evaluate_command("format  c:")
        self.assertFalse(allowed)
        self.assertIn("blocked", reason.lower())

        # Multiple spaces and uppercase
        allowed2, req2, reason2 = self.safety.evaluate_command("  FORMAT   C:  ")
        self.assertFalse(allowed2)

        # Del with spaces on protected path
        allowed3, req3, reason3 = self.safety.evaluate_command("del /f /s   c:\\windows\\system32")
        self.assertFalse(allowed3)

    def test_destructive_tool_unconfirmed_blocks(self):
        # execute_tool on destructive tool without callback or confirmed flag
        res = self.agent.execute_tool("delete_file_or_folder", {"name": "test.txt", "location": "desktop"})
        self.assertFalse(res.get("success", False))
        self.assertTrue(res.get("requires_confirmation"))
        self.assertFalse(res.get("confirmed"))
        self.assertIn("CONFIRMATION REQUIRED", res.get("error", ""))
        self.assertIsNotNone(self.agent.pending_confirmation)

    def test_tool_arguments_cannot_forge_confirmation(self):
        """An LLM must not bypass the gate by adding an internal-looking field."""
        destructive = MagicMock(return_value={"success": True})
        self.agent.tool_registry["delete_file_or_folder"] = destructive

        res = self.agent.execute_tool(
            "delete_file_or_folder",
            {"name": "important", "location": "desktop", "_confirmed": True},
        )

        self.assertTrue(res["requires_confirmation"])
        destructive.assert_not_called()

    def test_only_yes_executes_pending_destructive_action(self):
        destructive = MagicMock(return_value={"success": True, "message": "Deleted."})
        self.agent.tool_registry["delete_file_or_folder"] = destructive

        self.agent.run_agent_loop("delete folder important on desktop")
        not_yes = self.agent.run_agent_loop("confirm")

        self.assertFalse(not_yes["success"])
        destructive.assert_not_called()

        self.agent.run_agent_loop("delete folder important on desktop")
        yes = self.agent.run_agent_loop("yes")
        self.assertTrue(yes["success"])
        destructive.assert_called_once_with(name="important", location="desktop")

    def test_destructive_tool_denied_by_callback(self):
        self.agent.set_confirmation_callback(lambda tool, args: False)
        res = self.agent.execute_tool("empty_recycle_bin", {})
        self.assertFalse(res.get("success", False))
        self.assertIn("DENIED", res.get("error", ""))

    def test_destructive_tool_approved_by_callback(self):
        self.agent.set_confirmation_callback(lambda tool, args: True)
        res = self.agent.execute_tool("run_approved_command", {"command": "Write-Output 'OK'"})
        self.assertTrue(res.get("success", True))

    def test_destructive_tool_cmd_requires_confirmation(self):
        from tools.terminal_tools import run_cmd
        res = run_cmd("shutdown /s /t 60")
        self.assertFalse(res["success"])
        self.assertTrue(res["requires_confirmation"])
        self.assertIn("CONFIRMATION REQUIRED", res["stderr"])

    def test_two_turn_conversational_confirmation(self):
        # 1. User requests destructive delete
        res1 = self.agent.run_agent_loop("delete folder dummy_test on desktop")
        self.assertIsNotNone(self.agent.pending_confirmation)

        # 2. User confirms with 'yes'
        res2 = self.agent.run_agent_loop("yes")
        self.assertIsNone(self.agent.pending_confirmation)
        self.assertIn("Confirmed", res2["response"])

    def test_two_turn_conversational_cancellation(self):
        # 1. User requests destructive action
        self.agent.run_agent_loop("delete folder dummy_test on desktop")
        self.assertIsNotNone(self.agent.pending_confirmation)

        # 2. User cancels with 'no'
        res = self.agent.run_agent_loop("no")
        self.assertIsNone(self.agent.pending_confirmation)
        self.assertIn("cancelled", res["response"].lower())

    def test_remember_fact_tool_and_fast_path(self):
        from core.memory import remember_fact
        # Direct tool execution
        res = remember_fact("test_key", "test_val")
        self.assertTrue(res["success"])
        self.assertEqual(self.agent.memory.get_user_preference("test_key"), "test_val")

        # Conversational fast path
        res_loop = self.agent.run_agent_loop("remember that my editor is VSCode")
        self.assertTrue(res_loop["success"])
        self.assertEqual(self.agent.memory.get_user_preference("my editor"), "VSCode")
        self.assertIn("VSCode", res_loop["response"])

        all_prefs = self.agent.memory.get_all_preferences()
        self.assertIn("my editor", all_prefs)
        self.assertIn("test_key", all_prefs)

    def test_llm_provider_error_surfacing(self):
        from core.llm_provider import LLMProvider
        provider = LLMProvider()
        # Force client to None
        provider.client = None
        res = provider.run_tool_agent_loop(
            prompt="Hello",
            system_instruction="sys",
            tools_list=[],
            tool_executor=lambda name, args: None
        )
        self.assertIsNotNone(res)
        self.assertFalse(res["success"])
        self.assertTrue(res["response"].startswith("LLM unavailable:"))

    def test_settings_models_no_duplicates(self):
        from core.llm_provider import DEFAULT_MODELS
        # Verify no duplicates in DEFAULT_MODELS
        self.assertEqual(len(DEFAULT_MODELS), len(set(DEFAULT_MODELS)))

        # Verify no duplicate in settings.yaml fallback
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        primary = cfg["llm"]["model"]
        fallbacks = cfg["llm"]["fallback_models"]
        self.assertNotIn(primary, fallbacks)



if __name__ == "__main__":
    unittest.main()
