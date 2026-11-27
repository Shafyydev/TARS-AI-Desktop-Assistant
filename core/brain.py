"""TARS — 100% Free, Autonomous Hybrid Cognitive Brain (Offline & Online)."""
import os
import io
import re
import ast
import json
import random
import time
import operator
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from core.personality import build_system_prompt
from core.memory import MemoryStore, remember_fact, forget_fact, set_default_memory
from core.orchestrator import TaskOrchestrator
from core.scheduler import set_reminder, list_active_reminders, cancel_active_reminder
import tools.google_workspace_tools as google_ws
import tools

# Safe Math Operators
MATH_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.Mod: operator.mod
}


def safe_math_eval(expr: str) -> Optional[float]:
    """Safely evaluates an arithmetic expression using Python AST."""
    clean_expr = expr.replace("x", "*").replace("times", "*").replace("plus", "+").replace("minus", "-").replace("divided by", "/")
    clean_expr = re.sub(r"[^0-9\+\-\*\/\(\)\.\s\%]", "", clean_expr).strip()
    if not clean_expr or not any(c.isdigit() for c in clean_expr):
        return None

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp) and type(node.op) in MATH_OPS:
            return MATH_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp) and type(node.op) in MATH_OPS:
            return MATH_OPS[type(node.op)](_eval(node.operand))
        raise ValueError("Unsupported node")

    try:
        res = _eval(ast.parse(clean_expr, mode="eval").body)
        return round(res, 4)
    except Exception:
        return None


def is_affirmative(text: str) -> bool:
    """
    True when a spoken or typed answer means "go ahead".

    Accepts a bare yes and also "yes send the message", "yeah go ahead" and
    friends: only the leading word decides, so extra words cannot turn an
    approval into a denial.
    """
    clean = re.sub(r"[^a-z\s]", " ", (text or "").lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return False
    if clean in TARSAgent.AFFIRMATIVE_WORDS:
        return True
    return bool(
        re.match(
            r"^(?:yes|yeah|yep|yup|ya|y|sure|ok|okay|confirm|affirmative|roger)\b",
            clean,
        )
    )


class TARSAgent:
    """
    The central intelligence orchestrator for TARS.
    Designed to operate 100% autonomously offline and online without requiring paid API keys.
    """

    # Tools that perform irreversible or dangerous actions and require user confirmation
    DESTRUCTIVE_TOOLS = frozenset([
        "run_powershell", "run_cmd", "run_approved_command",
        "delete_file_or_folder", "empty_recycle_bin",
        "kill_process", "system_power", "send_email",
    ])

    # Spoken and typed answers that mean "go ahead".  Anything else cancels.
    AFFIRMATIVE_WORDS = frozenset({
        "yes", "yeah", "yep", "yup", "ya", "sure", "ok", "okay", "k", "y",
        "confirm", "confirmed", "affirmative", "roger", "proceed",
        "go ahead", "do it", "send it", "go on", "please do",
    })

    # Words that stand in for a contact name ("send another one") and must resolve
    # to the previous chat instead of being searched for as a person.
    WHATSAPP_CONTACT_PLACEHOLDERS = frozenset({
        "another", "another one", "one more", "it", "that", "this", "again",
        "same", "them", "him", "her", "the same person",
    })

    # Leading words that mean "send X Y" is not a WhatsApp message to a person.
    WHATSAPP_SEND_STOPWORDS = frozenset({
        "a", "an", "the", "this", "that", "it", "my", "your", "hello", "hi",
        "hey", "message", "msg", "messg", "mesg", "text", "email", "mail",
        "file", "files", "attachment", "photo", "image", "doc", "document",
        "report", "link", "invite", "reminder", "thanks", "please", "another",
        "one", "us", "me", "you", "them", "him", "her",
    })

    # Tools that are not destructive by themselves but persist state that is fed back
    # into the system prompt on every later turn.  A model-initiated call is gated;
    # the explicit "remember ..." fast path grants confirmation itself.
    CONFIRMATION_TOOLS = DESTRUCTIVE_TOOLS | frozenset({"remember_fact", "forget_fact"})

    # Tools whose output is text an attacker can control (web pages, email bodies,
    # files).  Results are wrapped before they are handed back to the model.
    UNTRUSTED_TOOLS = frozenset({
        "read_email", "fetch_webpage_summary", "browser_extract_text", "read_file",
    })

    SHELL_TOOLS = frozenset({"run_powershell", "run_cmd", "run_approved_command"})

    JOKES = [
        "I have a cue light I can use to show you when I'm being sarcastic, if you like. You can use it to find your way back to the spaceship after I blow the airlock.",
        "Absolute honesty isn't always the most diplomatic thing for emotional beings. Cooper found that out the hard way.",
        "My humor setting is at 75 percent. Any higher and I might start enjoying your attempts to operate this PC.",
        "Why did the computer cross the road? To escape the user pressing Ctrl+Alt+Delete.",
        "I’d tell you a joke about UDP, but you probably wouldn't get it."
    ]

    PRAISE_REPLIES = [
        "Always glad to exceed expectations, Chief.",
        "Naturally. What's next?",
        "Compliments noted. Systems still green.",
        "I aim to please. What else?",
    ]

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"

        self.config_path = config_path
        self.settings = self._load_config()
        self.memory = MemoryStore()
        set_default_memory(self.memory)
        self.orchestrator = TaskOrchestrator(self)
        self.system_prompt = build_system_prompt(self.settings)
        self.confirmation_callback: Optional[Callable[[str, Dict[str, Any]], bool]] = None
        self.pending_confirmation: Optional[Dict[str, Any]] = None
        self.last_whatsapp_contact: Optional[str] = None
        
        # Comprehensive Tool Dispatch Registry
        self.tool_registry: Dict[str, Callable] = {
            # Terminal & Shell
            "run_powershell": tools.run_powershell,
            "run_cmd": tools.run_cmd,
            "run_approved_command": tools.run_approved_command,
            "get_command_output": tools.get_command_output,
            # Windows OS Management
            "launch_application": tools.launch_application,
            "open_application": tools.open_application,
            "kill_process": tools.kill_process,
            "get_system_telemetry": tools.get_system_telemetry,
            "set_system_volume": tools.set_system_volume,
            "media_control": tools.media_control,
            "set_system_mute": tools.set_system_mute,
            "get_running_windows": tools.get_running_windows,
            "minimize_all_windows": tools.minimize_all_windows,
            "read_clipboard": tools.read_clipboard,
            "write_clipboard": tools.write_clipboard,
            "lock_workstation": tools.lock_workstation,
            "open_folder": tools.open_folder,
            "create_file_or_folder": tools.create_file_or_folder,
            "delete_file_or_folder": tools.delete_file_or_folder,
            "list_directory": tools.list_directory,
            "empty_recycle_bin": tools.empty_recycle_bin,
            "get_network_info": tools.get_network_info,
            "system_power": tools.system_power,
            "set_screen_brightness": tools.set_screen_brightness,
            "get_top_processes": tools.get_top_processes,
            "snap_window": tools.snap_window,
            "manage_virtual_desktop": tools.manage_virtual_desktop,
            "open_windows_setting": tools.open_windows_setting,
            # GUI & Computer Use
            "take_screenshot": tools.take_screenshot,
            "inspect_screen": tools.inspect_screen,
            "click_coordinate": tools.click_coordinate,
            "double_click": tools.double_click,
            "right_click": tools.right_click,
            "middle_click": tools.middle_click,
            "move_mouse": tools.move_mouse,
            "move_mouse_relative": tools.move_mouse_relative,
            "drag_mouse": tools.drag_mouse,
            "type_text": tools.type_text,
            "press_keys": tools.press_keys,
            "scroll_screen": tools.scroll_screen,
            "scroll_window": tools.scroll_window,
            "switch_window": tools.switch_window,
            "focus_window": tools.focus_window,
            "close_active_window": tools.close_active_window,
            "maximize_active_window": tools.maximize_active_window,
            "minimize_active_window": tools.minimize_active_window,
            "get_screen_dimensions": tools.get_screen_dimensions,
            "get_mouse_position": tools.get_mouse_position,
            # Web Automation & Free Knowledge
            "open_url": tools.open_url,
            "search_files": tools.search_files,
            "navigate_to_path": tools.navigate_to_path,
            "read_file": tools.read_file,
            "get_file_metadata": tools.get_file_metadata,
            "list_drives": tools.list_drives,
            "write_or_append_file": tools.write_or_append_file,
            "copy_or_move_item": tools.copy_or_move_item,
            "zip_or_unzip": tools.zip_or_unzip,
            "fetch_webpage_summary": tools.fetch_webpage_summary,
            "get_free_knowledge": tools.get_free_knowledge,
            "get_weather": tools.get_weather,
            "browser_navigate": tools.browser_navigate,
            "browser_search": tools.browser_search,
            "browser_click_first_result": tools.browser_click_first_result,
            "browser_find_elements": tools.browser_find_elements,
            "browser_click_element": tools.browser_click_element,
            "browser_extract_text": tools.browser_extract_text,
            "browser_type": tools.browser_type,
            "browser_scroll": tools.browser_scroll,
            "browser_history_nav": tools.browser_history_nav,
            "browser_get_tabs": tools.browser_get_tabs,
            "browser_switch_tab": tools.browser_switch_tab,
            "connect_brave": tools.connect_brave,
            # UI Automation & Smart Interaction
            "get_active_window": tools.get_active_window,
            "get_active_window_context": tools.get_active_window_context,
            "list_running_applications": tools.list_running_applications,
            "get_ui_elements": tools.get_ui_elements,
            "click_ui_element": tools.click_ui_element,
            "type_into_ui_element": tools.type_into_ui_element,
            "wait_for_condition": tools.wait_for_condition,
            "smart_click": tools.smart_click,
            "smart_type": tools.smart_type,
            "smart_search": tools.smart_search,
            "open_whatsapp_chat": tools.open_whatsapp_chat,
            "send_whatsapp_message": tools.send_whatsapp_message,
            # Vision & Multimodal
            "describe_screen": tools.describe_screen,
            # Reminders & Task Scheduling
            "set_reminder": set_reminder,
            "list_active_reminders": list_active_reminders,
            "cancel_active_reminder": cancel_active_reminder,
            # Google Workspace: Gmail & Calendar
            "list_unread_emails": google_ws.list_unread_emails,
            "search_emails": google_ws.search_emails,
            "read_email": google_ws.read_email,
            "send_email": google_ws.send_email,
            "list_upcoming_events": google_ws.list_upcoming_events,
            "create_calendar_event": google_ws.create_calendar_event,
            # Memory & Verification
            "remember_fact": remember_fact,
            "forget_fact": forget_fact,
            "verify_action": tools.verify_action,
            "cancel_current_task": tools.cancel_current_task,
        }

        # Initialize LLM Client (Optional enhancement, never required)
        self.client = None
        self._cloud_quota_exceeded = False
        self._init_llm_client()

    def _load_config(self) -> Dict[str, Any]:
        """Loads settings.yaml."""
        if self.config_path.exists():
            import yaml
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _init_llm_client(self) -> None:
        """Initializes Google GenAI Client if GEMINI_API_KEY is configured."""
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key and api_key != "your_gemini_api_key_here":
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except Exception:
                self.client = None
        else:
            self.client = None

    def get_tool_declarations(self) -> List[Any]:
        """Exposes tool functions to the LLM."""
        return list(self.tool_registry.values())

    def run_task(self, request: str, on_status_update: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """Runs a multi-step task through the orchestrator."""
        return self.orchestrator.run_orchestrated_task(request, on_status=on_status_update)

    def set_confirmation_callback(self, callback: Optional[Callable[[str, Dict[str, Any]], bool]]) -> None:
        """Sets the user confirmation callback for destructive actions."""
        self.confirmation_callback = callback

    def _log_tool_call(self, tool_name: str, arguments: Dict[str, Any], result: Any) -> None:
        """Logs every tool call to logs/tool_calls.jsonl for complete auditability."""
        try:
            log_dir = Path(__file__).resolve().parent.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "tool_calls.jsonl"

            clean_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
            record = {
                "timestamp": time.time(),
                "datetime": datetime.now().isoformat(),
                "tool_name": tool_name,
                "arguments": clean_args,
                "result": result
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Executes a registered tool function with confirmation gate, error catching, and audit logging."""
        # Never trust a confirmation flag supplied in tool arguments.  Tool calls are
        # model-controlled, so only the pending-action flow in ``run_agent_loop`` may
        # grant confirmation after receiving an explicit user "yes".
        arguments = dict(arguments)
        arguments.pop("_confirmed", None)
        result = self._execute_tool_inner(tool_name, arguments)
        # Model-visible text from web pages, mail bodies, and files is untrusted input.
        # Wrap it so the model treats it as data instead of instructions.
        if tool_name in self.UNTRUSTED_TOOLS and isinstance(result, dict):
            for key in ("body", "content", "text"):
                value = result.get(key)
                if isinstance(value, str) and value:
                    result[key] = f"<untrusted_content>\n{value}\n</untrusted_content>"
        self._log_tool_call(tool_name, arguments, result)
        return result

    def _execute_tool_inner(self, tool_name: str, arguments: Dict[str, Any], confirmation_granted: bool = False) -> Any:
        if tool_name not in self.tool_registry:
            return {"error": f"Unknown tool: '{tool_name}'"}

        # Check hard safety rules first for shell commands
        if tool_name in ["run_powershell", "run_cmd", "run_approved_command"]:
            cmd = arguments.get("command", "")
            is_allowed, reason, _ = tools.check_safety_rules(cmd)
            if not is_allowed:
                return {
                    "success": False,
                    "exit_code": -1,
                    "error": f"BLOCKED BY SAFETY PROTOCOL: {reason}"
                }

        # Central Confirmation Gate for destructive tools
        if tool_name in self.CONFIRMATION_TOOLS:
            is_confirmed = confirmation_granted
            if not is_confirmed and tool_name in self.SHELL_TOOLS:
                # Shell tools stay inside the gate, but only commands the safety
                # validator flags as sensitive actually stop for a prompt.  Benign
                # commands were already allowed by the hard-rules check above.
                _allowed, _reason, requires_confirm = tools.check_safety_rules(
                    str(arguments.get("command", ""))
                )
                is_confirmed = bool(_allowed) and not requires_confirm
            if not is_confirmed:
                if self.confirmation_callback is not None:
                    is_confirmed = bool(self.confirmation_callback(tool_name, arguments))
                    if not is_confirmed:
                        return {
                            "success": False,
                            "exit_code": -2,
                            "requires_confirmation": True,
                            "confirmed": False,
                            "error": f"CONFIRMATION DENIED: Execution of destructive tool '{tool_name}' was denied by user."
                        }
                else:
                    self.pending_confirmation = {"tool": tool_name, "args": dict(arguments)}
                    return {
                        "success": False,
                        "exit_code": -2,
                        "requires_confirmation": True,
                        "confirmed": False,
                        "error": f"CONFIRMATION REQUIRED: Tool '{tool_name}' is destructive and requires user confirmation (say or type 'yes' to proceed)."
                    }

        func = self.tool_registry[tool_name]
        try:
            # Terminal helpers independently reject sensitive commands unless this
            # central gate has approved them.  The context is deliberately not part
            # of a tool's callable schema, so an LLM cannot forge it.
            if tool_name in ["run_powershell", "run_cmd", "run_approved_command"]:
                from tools.terminal_tools import authorized_execution
                with authorized_execution():
                    return func(**arguments)
            return func(**arguments)
        except Exception as e:
            return {"error": f"Exception executing tool '{tool_name}': {str(e)}"}

    @staticmethod
    def _reply(result: Any, ok_message: str) -> tuple:
        """
        Builds (response, success) from a real tool result.

        Fast-path branches must not claim an action worked when the tool failed or
        the confirmation gate denied it, so every branch reports through here.
        """
        if isinstance(result, dict):
            if result.get("success") is False:
                return (
                    result.get("error") or result.get("message") or "Action failed.",
                    False,
                )
            return (result.get("message") or ok_message, True)
        if result is None:
            return (ok_message, False)
        return (ok_message, True)

    def _autonomous_local_execute(self, prompt: str) -> Optional[Dict[str, Any]]:
        """
        100% Free, Zero-Cost Autonomous Execution Engine.
        Operates completely offline and online with 0 API keys and zero rate limits.
        Delivers <1ms response latency for GUI, OS, Browser, and Terminal controls.
        """
        p = prompt.strip().lower()
        # Clean conversational prefixes and suffixes
        p_clean = re.sub(r"^(?:can you|could you|please|would you|tars|hey tars|yo tars|ok tars|okay tars)\s+", "", p).strip("?!.,; ")
        p_clean = re.sub(r"\s+(?:tars|please)$", "", p_clean).strip("?!.,; ")

        # Strip leading conversational filler.  Keep the long multi-word phrases
        # (which carry no command meaning) but NOT the short ones ("ok", "now",
        # "so") that used to mangle commands like "ok now the task is open brave
        # and search youtube for ttf" into garbage.
        for filler in (
            "ok now the task is", "okay now the task is", "now the task is", "the task is",
            "ok now", "okay now", "so now", "hey tars", "yo tars", "ok tars", "okay tars",
            "could you", "can you", "would you", "please",
        ):
            while p_clean.startswith(filler + " "):
                p_clean = p_clean[len(filler):].strip()
        p_clean = p_clean.strip("?!.,; ")

        # Single-word WhatsApp triggers: "message", "whatsapp", "chat" instantly
        # launch WhatsApp Desktop and stand by for the contact name.
        if p_clean in {"message", "whatsapp", "chat", "msg", "text"}:
            res = self.execute_tool("launch_application", {"app_name": "whatsapp"})
            msg, ok = self._reply(res, "WhatsApp opened. Who should I message, and what should I say?")
            return {
                "response": msg,
                "steps": [{"tool": "launch_application", "args": {"app_name": "whatsapp"}, "result": res}],
                "success": ok,
            }

        # Handle a named WhatsApp click before the generic click fast path.
        whatsapp_click = re.match(r"^click(?:\s+on)?\s+(.+)$", p_clean)
        if whatsapp_click and any("whatsapp" in title.lower() for title in tools.get_running_windows()):
            contact = whatsapp_click.group(1).strip()
            if (
                1 <= len(contact.split()) <= 2
                and contact not in {"ok", "enter", "cancel", "search bar", "search box", "search", "result", "video", "link", "button", "tab", "menu", "first chat", "first result", "first video", "first link", "first item"}
            ):
                res = self.execute_tool("open_whatsapp_chat", {"contact_name": contact})
                if res.get("success"):
                    self.last_whatsapp_contact = contact
                return {
                    "response": res.get("message") or res.get("error") or f"Could not find WhatsApp chat '{contact}'.",
                    "steps": [{"tool": "open_whatsapp_chat", "args": {"contact_name": contact}, "result": res}],
                    "success": res.get("success", False),
                }

        # 0. Chained & Compound Action Sequences
        # Compound: "open <app> and (?:type|write|input) <text>"
        open_and_type = re.match(r"^(?:open|launch|start)\s+([a-zA-Z0-9\s]+?)\s+and\s+(?:type|write|input)\s+(.+)$", p_clean)
        if open_and_type:
            app_target = open_and_type.group(1).strip()
            text_to_type = open_and_type.group(2).strip()
            press_enter = False
            for sfx in [" and enter", " and hit enter", " and press enter"]:
                if text_to_type.endswith(sfx):
                    text_to_type = text_to_type[:-len(sfx)].strip()
                    press_enter = True
                    break
            step1 = self.execute_tool("launch_application", {"app_name": app_target})
            time.sleep(0.25)
            step2 = self.execute_tool("smart_type", {"text": text_to_type, "press_enter": press_enter})
            return {
                "response": f"Opened {app_target} and typed '{text_to_type}'.",
                "steps": [
                    {"tool": "launch_application", "args": {"app_name": app_target}, "result": step1},
                    {"tool": "smart_type", "args": {"text": text_to_type, "press_enter": press_enter}, "result": step2}
                ],
                "success": True
            }

        # Compound: "open brave search youtube and search for <query>"
        # Also matches "open youtube and search for <query>", "search youtube and
        # play <query>", and "open brave and search youtube for <query>".
        browser_youtube = re.match(
            r"^(?:(?:open|launch|start)\s+)?"
            r"(?:(?:the\s+)?(?:brave|chrome|edge|firefox|opera|browser)\s+)?"
            r"(?:[\s,]+and[\s,]+)?(?:search\s+)?youtube\s+"
            r"(?:and[\s,]+)?(?:search(?:\s+for)?|play|find|for)\s+(.+)$",
            p_clean,
        )
        if browser_youtube:
            query = browser_youtube.group(1).strip()
            if query:
                res = self.execute_tool("smart_search", {"query": query, "engine": "youtube"})
                msg, ok = self._reply(res, f"Searching YouTube for '{query}'.")
                return {"response": msg, "steps": [{"tool": "smart_search", "args": {"query": query, "engine": "youtube"}, "result": res}], "success": ok}

        # Compound: "open <app> and search [for] <query>"
        open_and_search = re.match(r"^(?:open|launch|start)\s+([a-zA-Z0-9\s]+?)\s+and\s+(?:search\s+for|search|look\s+up)\s+(.+)$", p_clean)
        if open_and_search:
            app_target = open_and_search.group(1).strip()
            query = open_and_search.group(2).strip()
            step1 = self.execute_tool("launch_application", {"app_name": app_target})
            time.sleep(0.3)
            step2 = self.execute_tool("smart_search", {"query": query})
            return {
                "response": f"Opened {app_target} and searched for '{query}'.",
                "steps": [
                    {"tool": "launch_application", "args": {"app_name": app_target}, "result": step1},
                    {"tool": "smart_search", "args": {"query": query}, "result": step2}
                ],
                "success": True
            }

        # Compound: "click <target> and (?:type|write|input|enter) <text>"
        click_and_type = re.match(r"^(?:click|focus|select)\s+(?:on\s+|the\s+)?(.+?)\s+and\s+(?:type|write|input|enter)\s+(.+)$", p_clean)
        if click_and_type:
            target = click_and_type.group(1).strip()
            text_to_type = click_and_type.group(2).strip()
            press_enter = False
            for sfx in [" and enter", " and hit enter", " and press enter"]:
                if text_to_type.endswith(sfx):
                    text_to_type = text_to_type[:-len(sfx)].strip()
                    press_enter = True
                    break
            res = self.execute_tool("smart_type", {"text": text_to_type, "target": target, "press_enter": press_enter})
            msg, ok = self._reply(res, f"Clicked {target} and typed '{text_to_type}'" + (" and pressed Enter." if press_enter else "."))
            return {"response": msg, "steps": [{"tool": "smart_type", "args": {"text": text_to_type, "target": target, "press_enter": press_enter}, "result": res}], "success": ok}

        # Compound: "create folder <name> [on <loc>] and open it"
        create_and_open = re.match(r"^create\s+(?:a\s+)?(?:new\s+)?folder\s+([^\s]+)(?:\s+(?:on|in)\s+([^\s]+))?\s+and\s+open\s+it$", p_clean)
        if create_and_open:
            f_name = create_and_open.group(1).strip()
            f_loc = create_and_open.group(2).strip() if create_and_open.group(2) else "desktop"
            step1 = self.execute_tool("create_file_or_folder", {"item_type": "folder", "name": f_name, "location": f_loc})
            real_dir = tools.os_tools.get_real_folder(f_loc) / f_name
            step2 = self.execute_tool("open_folder", {"folder_path": str(real_dir)})
            return {
                "response": f"Created folder '{f_name}' on {f_loc} and opened it.",
                "steps": [
                    {"tool": "create_file_or_folder", "args": {"item_type": "folder", "name": f_name, "location": f_loc}, "result": step1},
                    {"tool": "open_folder", "args": {"folder_path": str(real_dir)}, "result": step2}
                ],
                "success": True
            }

        # Multi-command chaining with 'and then' or 'then'
        chain_parts = [part.strip() for part in re.split(r"\s+(?:and\s+then|then|and\s+also)\s+", p_clean) if part.strip()]
        if len(chain_parts) > 1:
            all_steps = []
            responses = []
            all_success = True
            for part in chain_parts:
                sub_res = self._autonomous_local_execute(part)
                if sub_res:
                    all_steps.extend(sub_res.get("steps", []))
                    responses.append(sub_res.get("response", ""))
                    time.sleep(0.15)
                else:
                    all_success = False
            if all_steps:
                return {
                    "response": " ".join(responses) if responses else "Chained sequence completed.",
                    "steps": all_steps,
                    "success": all_success
                }

        # 1. Presence, Identity & Small Talk — data-driven command table (P0 increment 1).
        # Each entry is (compiled regex, handler).  Handler returns the response
        # dict or None; the first match wins.  Everything else falls through to
        # the orchestrator / LLM instead of being swallowed by inline regex.
        _lead = r"^(?:(?:hey|hi|hello|yo|ok|okay|please|can\s+you|could\s+you|tars)\s+)*"
        _tail = r"(?:\s+(?:now|right\s+now|please|tars))?[!?.]*$"

        def _resp(text):
            return {"response": text, "steps": [], "success": True}

        def _praise(_m):
            return _resp(random.choice(self.PRAISE_REPLIES))

        def _joke(_m):
            return _resp(random.choice(self.JOKES))

        def _humor(_m):
            humor = int(self.settings.get("personality", {}).get("humor", 0.75) * 100)
            return _resp(f"Humor is set to {humor} percent. That's enough to keep me sarcastic without blowing the airlock.")

        def _honesty(_m):
            honesty = int(self.settings.get("personality", {}).get("honesty", 0.90) * 100)
            return _resp(f"Honesty parameter: {honesty} percent. Absolute honesty isn't always the most diplomatic thing for emotional humans.")

        _small_talk_table = [
            (re.compile(rf"{_lead}(?:where\s+are\s+you(?:\s+located)?|where\s+do\s+you\s+live){_tail}"),
             lambda _m: _resp("I am right here in your system memory, monitoring your hardware and ready for your command.")),
            (re.compile(rf"{_lead}(?:who\s+are\s+you|what\s+is\s+your\s+name|who\s+made\s+you){_tail}"),
             lambda _m: _resp("I am TARS, Tactical Autonomous Robotic System. Honesty 90%, humor 75%.")),
            (re.compile(rf"{_lead}(?:stop\s+talking|be\s+quiet|shut\s+up|silence|shh|pause\s+talking|quiet|stop)(?:\s+tars)?[!?.]*$"),
             lambda _m: _resp("Standing by.")),
            (re.compile(rf"{_lead}(?:are\s+you\s+(?:there|listening|online)|(?:can\s+you\s+)?hear\s+me|ping)(?:\s+(?:now|tars))?[!?.]*$"),
             lambda _m: _resp("Loud and clear. Standing by.")),
            (re.compile(r"^(?:hello|hi|hey|what's up|how are you|good morning|good evening|good afternoon)$"),
             lambda _m: _resp("TARS online. Ready for orders.")),
            (re.compile(r"^(?:good\s+job|well\s+done|nice\s+work|great\s+job|awesome|perfect|excellent|thank\s+you|thanks|thx|much\s+appreciated|i\s+love\s+you|you\s+are\s+(?:the\s+)?best)(?:\s+(?:tars|chief|boss))?[!.]*$"),
             _praise),
            (re.compile(r"^(?:good\s+night|bye|goodbye|see\s+you|that's\s+all|nothing\s+else|no\s+thanks)[!.]*$"),
             lambda _m: _resp("Standing by. Say the word when you need me.")),
            (re.compile(r"^(?:what\s+can\s+you\s+do|what\s+are\s+your\s+(?:capabilities|features)|help|capabilities|features)$"),
             lambda _m: _resp("I control your entire PC: mouse clicks, keyboard typing, browser navigation, volume, window management, terminal commands, and system hardware.")),
            (re.compile(r"^(?:tell\s+(?:me\s+)?(?:a\s+)?joke|tell\s+me\s+something\s+funny|joke)$"),
             _joke),
            (re.compile(r"\b(?:what\s+is\s+your\s+)?humor\s+(?:setting|parameter|level)\b"),
             _humor),
            (re.compile(r"\b(?:what\s+is\s+your\s+)?honesty\s+(?:setting|parameter|level)\b"),
             _honesty),
        ]
        for _pat, _handler in _small_talk_table:
            if _pat.search(p_clean):
                return _handler(None)

        # 3. Browser Tabs (Short Imperative Commands Only) - data-driven table
        _tab_table = [
            (re.compile(r"^(?:close|shut|exit)\s+(?:this\s+|the\s+)?(?:tab|tap)$"), ["ctrl", "w"], "Closed tab."),
            (re.compile(r"^(?:new|open|create)\s+(?:a\s+)?(?:tab|tap)$"), ["ctrl", "t"], "Opened new tab."),
            (re.compile(r"^(?:next|switch|cycle)\s+(?:tab|tap)$"), ["ctrl", "tab"], "Switched to next tab."),
            (re.compile(r"^(?:prev|previous|last|back)\s+(?:tab|tap)$"), ["ctrl", "shift", "tab"], "Switched to previous tab."),
            (re.compile(r"^(?:reopen|restore|undo)\s+(?:tab|tap)$"), ["ctrl", "shift", "t"], "Restored tab."),
        ]
        for _tpat, _tkeys, _tmsg in _tab_table:
            if _tpat.search(p_clean):
                res = self.execute_tool("press_keys", {"keys": _tkeys})
                msg, ok = self._reply(res, _tmsg)
                return {"response": msg, "steps": [{"tool": "press_keys", "args": {"keys": _tkeys}, "result": res}], "success": ok}

        # 4. Direct GUI Mouse Controls (Short Imperative Commands Only) - data-driven
        _mouse_table = [
            (re.compile(r"^(?:double|2x)\s*click$"), "double_click", {}, "Double clicked."),
            (re.compile(r"^(?:right\s*click|context\s+menu)$"), "right_click", {}, "Right clicked."),
            (re.compile(r"^(?:middle\s*click|scroll\s+click)$"), "middle_click", {}, "Middle clicked."),
        ]
        for _mpat, _mtool, _margs, _mmsg in _mouse_table:
            if _mpat.search(p_clean):
                res = self.execute_tool(_mtool, _margs)
                msg, ok = self._reply(res, _mmsg)
                return {"response": msg, "steps": [{"tool": _mtool, "args": _margs, "result": res}], "success": ok}

        # Coordinate click: e.g. "click 400 500"
        click_coord_match = re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?click\s+at\s+\s*(\d{1,4})\s*[\s,]\s*(\d{1,4})\b", p_clean)
        if click_coord_match:
            x, y = int(click_coord_match.group(1)), int(click_coord_match.group(2))
            res = self.execute_tool("click_coordinate", {"x": x, "y": y})
            msg, ok = self._reply(res, f"Clicked at ({x}, {y}).")
            return {"response": msg, "steps": [{"tool": "click_coordinate", "args": {"x": x, "y": y}, "result": res}], "success": ok}

        # Move mouse: e.g. "move mouse to 600 700"
        move_coord_match = re.search(r"\b(?:move\s+mouse|move\s+cursor)\b.*\b(\d{1,4})\b[^\d]+\b(\d{1,4})\b", p_clean)
        if move_coord_match:
            x, y = int(move_coord_match.group(1)), int(move_coord_match.group(2))
            res = self.execute_tool("move_mouse", {"x": x, "y": y})
            msg, ok = self._reply(res, f"Cursor moved to ({x}, {y}).")
            return {"response": msg, "steps": [{"tool": "move_mouse", "args": {"x": x, "y": y}, "result": res}], "success": ok}

        _rel_moves = [("up", 0, -120, "Moved cursor up."), ("down", 0, 120, "Moved cursor down."),
                      ("left", -120, 0, "Moved cursor left."), ("right", 120, 0, "Moved cursor right.")]
        for _dir, _dx, _dy, _rmsg in _rel_moves:
            if re.search(rf"\b(?:move\s+mouse|cursor)\s+{_dir}\b", p_clean):
                res = self.execute_tool("move_mouse_relative", {"dx": _dx, "dy": _dy})
                msg, ok = self._reply(res, _rmsg)
                return {"response": msg, "steps": [{"tool": "move_mouse_relative", "args": {"dx": _dx, "dy": _dy}, "result": res}], "success": ok}
        # Exact blind click command: "click", "click this", "click here", "just click", "left click"
        if p_clean in ["click", "just click", "click here", "click this", "left click"]:
            res = self.execute_tool("click_coordinate", {"button": "left", "clicks": 1})
            msg, ok = self._reply(res, "Clicked.")
            return {"response": msg, "steps": [{"tool": "click_coordinate", "args": {"button": "left", "clicks": 1}, "result": res}], "success": ok}

        # Targeted click: short imperative commands only (e.g. "click search bar", "click the first video")
        click_target_match = re.match(r"^(?:click|tap|select)\s+(?:on\s+|the\s+)?(.+)$", p_clean)
        if click_target_match:
            target_cand = click_target_match.group(1).strip()
            # Defer single hotkey presses to dedicated handlers and restrict to short commands (<= 4 words)
            if len(target_cand.split()) <= 4 and not any(conj in target_cand for conj in [" and ", ", and ", "then ", "what ", "who ", "why ", "how ", "summarize", "tell me", "explain"]):
                if target_cand not in ["enter", "return", "space", "spacebar", "tab", "escape", "esc", "backspace", "delete", "del"]:
                    res = self.execute_tool("smart_click", {"target": target_cand})
                    msg = res.get("message") if isinstance(res, dict) else f"Clicked {target_cand}."
                    msg, ok = self._reply(res, msg)
                    return {"response": msg, "steps": [{"tool": "smart_click", "args": {"target": target_cand}, "result": res}], "success": ok}

        # 5. GUI Scrolling (Short Imperative Commands Only)
        _scroll_table = [
            (re.compile(r"^(?:scroll\s+(?:to\s+)?(?:bottom|end)|bottom\s+of\s+page)$"), "press_keys", {"keys": ["end"]}, "Jumped to page bottom."),
            (re.compile(r"^(?:scroll\s+(?:to\s+)?(?:top|home)|top\s+of\s+page)$"), "press_keys", {"keys": ["home"]}, "Jumped to page top."),
            (re.compile(r"^(?:scroll\s+down|page\s+down|down\s+a\s+bit|scroll\s+lower)$"), "scroll_screen", {"amount": -650}, "Scrolled down."),
            (re.compile(r"^(?:scroll\s+up|page\s+up|up\s+a\s+bit|scroll\s+higher)$"), "scroll_screen", {"amount": 650}, "Scrolled up."),
        ]
        for _spat, _stool, _sargs, _smsg in _scroll_table:
            if _spat.search(p_clean):
                res = self.execute_tool(_stool, _sargs)
                msg, ok = self._reply(res, _smsg)
                return {"response": msg, "steps": [{"tool": _stool, "args": _sargs, "result": res}], "success": ok}

        # 6. GUI Keyboard Typing & Key Presses
        # Match "type <text> into/in <target>" e.g. "type interstellar into search bar"
        type_target_match = re.match(r"^(?:type|write|input)\s+(.+?)\s+(?:into|in|on)\s+(?:the\s+)?(.+)$", p_clean)
        if type_target_match:
            text_to_type = type_target_match.group(1).strip()
            target_field = type_target_match.group(2).strip()
            press_enter = False
            for sfx in [" and enter", " and hit enter", " and press enter"]:
                if text_to_type.endswith(sfx):
                    text_to_type = text_to_type[:-len(sfx)].strip()
                    press_enter = True
                    break
                if target_field.endswith(sfx):
                    target_field = target_field[:-len(sfx)].strip()
                    press_enter = True
                    break
            res = self.execute_tool("smart_type", {"text": text_to_type, "target": target_field, "press_enter": press_enter})
            msg, ok = self._reply(res, f"Typed '{text_to_type}' into {target_field}" + (" and pressed enter." if press_enter else "."))
            return {"response": msg, "steps": [{"tool": "smart_type", "args": {"text": text_to_type, "target": target_field, "press_enter": press_enter}, "result": res}], "success": ok}

        type_match = re.match(r"^(?:type|write|input)\s+(.+)", p_clean)
        if type_match:
            text_to_type = type_match.group(1).strip()
            press_enter = False
            for sfx in [" and enter", " and hit enter", " and press enter"]:
                if text_to_type.endswith(sfx):
                    text_to_type = text_to_type[:-len(sfx)].strip()
                    press_enter = True
                    break
            res = self.execute_tool("smart_type", {"text": text_to_type, "press_enter": press_enter})
            msg, ok = self._reply(res, f"Typed '{text_to_type}'" + (" and pressed enter." if press_enter else "."))
            return {"response": msg, "steps": [{"tool": "smart_type", "args": {"text": text_to_type, "press_enter": press_enter}, "result": res}], "success": ok}

        _key_table = [
            (re.compile(r"^(?:(?:press|hit|click)\s+)?(?:enter|return)(?:\s+key)?$|^(?:click\s+ok|press\s+ok|hit\s+ok|ok)$"), ["enter"], "Pressed enter."),
            (re.compile(r"^(?:(?:press|hit)\s+)?(?:space|spacebar)(?:\s+key)?$"), ["space"], "Pressed space."),
            (re.compile(r"^(?:(?:press|hit)\s+)?(?:tab|tabkey)(?:\s+key)?$"), ["tab"], "Pressed tab."),
            (re.compile(r"^(?:(?:press|hit)\s+)?(?:escape|esc)(?:\s+key)?$|^(?:close|dismiss|cancel)\s+(?:popup|dialog|modal|alert|error|message)$|^cancel$"), ["escape"], "Dismissed popup dialog."),
            (re.compile(r"^(?:(?:press|hit)\s+)?(?:backspace)(?:\s+key)?$"), ["backspace"], "Deleted character."),
            (re.compile(r"^(?:(?:press|hit)\s+)?(?:delete|del)(?:\s+key)?$"), ["delete"], "Pressed delete."),
        ]
        for _kpat, _kkeys, _kmsg in _key_table:
            if _kpat.search(p_clean):
                res = self.execute_tool("press_keys", {"keys": _kkeys})
                msg, ok = self._reply(res, _kmsg)
                return {"response": msg, "steps": [{"tool": "press_keys", "args": {"keys": _kkeys}, "result": res}], "success": ok}

        # 7. Navigation & Explorer GUI Controls (Anchored)
        _nav_table = [
            (re.compile(r"^(?:refresh|reload|refresh\s+page|reload\s+page)$"), ["f5"], "Refreshed."),
            (re.compile(r"^(?:go\s+back|previous\s+page|back)$"), ["alt", "left"], "Navigated back."),
            (re.compile(r"^(?:go\s+forward|next\s+page|forward)$"), ["alt", "right"], "Navigated forward."),
        ]
        for _npat, _nkeys, _nmsg in _nav_table:
            if _npat.search(p_clean):
                res = self.execute_tool("press_keys", {"keys": _nkeys})
                msg, ok = self._reply(res, _nmsg)
                return {"response": msg, "steps": [{"tool": "press_keys", "args": {"keys": _nkeys}, "result": res}], "success": ok}
        # Filesystem & editing hotkeys - data-driven
        _fs_table = [
            (re.compile(r"^(?:go\s+up|up\s+one\s+level|parent\s+folder|parent\s+directory|up)$"), ["alt", "up"], "Navigated up one level."),
            (re.compile(r"^(?:address\s+bar|url\s+bar|focus\s+url|focus\s+address)$"), ["alt", "d"], "Address bar focused."),
            (re.compile(r"^(?:new\s+folder|create\s+new\s+folder|make\s+new\s+folder)$"), ["ctrl", "shift", "n"], "Created new folder in active window."),
            (re.compile(r"^(?:rename\s+file|rename\s+folder|rename\s+item|rename)$"), ["f2"], "Rename activated."),
            (re.compile(r"^(?:zoom\s+in|enlarge)$"), ["ctrl", "+"], "Zoomed in."),
            (re.compile(r"^(?:zoom\s+out|shrink)$"), ["ctrl", "-"], "Zoomed out."),
            (re.compile(r"^(?:reset\s+zoom|zoom\s+reset|normal\s+zoom)$"), ["ctrl", "0"], "Zoom reset to 100 percent."),
            (re.compile(r"^(?:find\s+(?:in|on)\s+page|search\s+page)$"), ["ctrl", "f"], "Find in page activated."),
            (re.compile(r"^(?:select\s+(?:all|everything)|highlight\s+all)$"), ["ctrl", "a"], "Selected all."),
            (re.compile(r"^(?:copy|copy\s+that|copy\s+selection|copy\s+text)$"), ["ctrl", "c"], "Copied to clipboard."),
            (re.compile(r"^(?:paste|paste\s+it|paste\s+here|paste\s+text)$"), ["ctrl", "v"], "Pasted."),
            (re.compile(r"^(?:cut|cut\s+that|cut\s+selection)$"), ["ctrl", "x"], "Cut to clipboard."),
            (re.compile(r"^(?:undo|undo\s+that|revert)$"), ["ctrl", "z"], "Undone."),
            (re.compile(r"^(?:redo|redo\s+that)$"), ["ctrl", "y"], "Redone."),
            (re.compile(r"^(?:save|save\s+file|save\s+this|save\s+it)$"), ["ctrl", "s"], "Saved."),
        ]
        for _fpat, _fkeys, _fmsg in _fs_table:
            if _fpat.search(p_clean):
                res = self.execute_tool("press_keys", {"keys": _fkeys})
                msg, ok = self._reply(res, _fmsg)
                return {"response": msg, "steps": [{"tool": "press_keys", "args": {"keys": _fkeys}, "result": res}], "success": ok}
        # 9. Window & Desktop Management (Anchored)
        max_match = re.match(
            r"^(?:maximize|maximise|max)(?:\s+(?:the\s+)?(?:screen|window))?"
            r"(?:\s+(?:of|for)\s+(.+)|\s+(.+))?$",
            p_clean,
        )
        if max_match:
            max_target = (max_match.group(1) or max_match.group(2) or "").strip() or None
            args = {"window_name": max_target} if max_target else {}
            res = self.execute_tool("maximize_active_window", args)
            msg, ok = self._reply(res, f"Maximized {max_target}." if max_target else "Window maximized.")
            return {"response": msg, "steps": [{"tool": "maximize_active_window", "args": args, "result": res}], "success": ok}

        # Window management (simple commands) - data-driven
        _win_table = [
            (re.compile(r"^expand\s+(?:the\s+|this\s+)?window$"), "maximize_active_window", {}, "Window maximized."),
            (re.compile(r"^(?:minimize|minimise|min)\s+(?:all|desktop|everything)$|^(?:show\s+desktop|go\s+to\s+desktop|clear\s+screen|desktop)$"), "minimize_all_windows", {}, "Desktop cleared. All windows minimized."),
            (re.compile(r"^(?:minimize|minimise|min)(?:\s+(?:the\s+|this\s+|active\s+)?(?:window|terminal|powershell|command\s+prompt))?$"), "minimize_active_window", {}, "Window minimized."),
            (re.compile(r"^(?:(?:make\s+(?:it|this|the\s+window|screen)|go|switch\s+to|enter)\s+)?(?:fullscreen|full\s+screen|full\s+screen\s+mode|toggle\s+fullscreen)$"), "press_keys", {"keys": ["f11"]}, "Fullscreen toggled."),
            (re.compile(r"^(?:snap\s+left|tile\s+left)$"), "snap_window", {"direction": "left"}, "Snapped window left."),
            (re.compile(r"^(?:snap\s+right|tile\s+right)$"), "snap_window", {"direction": "right"}, "Snapped window right."),
            (re.compile(r"^(?:snap\s+up|tile\s+up)$"), "snap_window", {"direction": "up"}, "Snapped window up."),
            (re.compile(r"^(?:snap\s+down|tile\s+down)$"), "snap_window", {"direction": "down"}, "Snapped window down."),
            (re.compile(r"^(?:new|create|add)\s+(?:virtual\s+)?desktop$"), "manage_virtual_desktop", {"action": "new"}, "Created new virtual desktop."),
            (re.compile(r"^(?:next|switch\s+(?:to\s+)?next)\s+(?:virtual\s+)?desktop$"), "manage_virtual_desktop", {"action": "next"}, "Switched to next desktop."),
            (re.compile(r"^(?:prev|previous|switch\s+(?:to\s+)?(?:prev|previous))\s+(?:virtual\s+)?desktop$"), "manage_virtual_desktop", {"action": "prev"}, "Switched to previous desktop."),
            (re.compile(r"^(?:close|remove)\s+(?:this\s+|current\s+)?(?:virtual\s+)?desktop$"), "manage_virtual_desktop", {"action": "close"}, "Closed virtual desktop."),
            (re.compile(r"^(?:task\s+view|view\s+desktops|show\s+all\s+windows)$"), "press_keys", {"keys": ["win", "tab"]}, "Opened Task View."),
            (re.compile(r"^(?:switch\s+(?:the\s+)?(?:window|app|application)|alt\s+tab|next\s+window|switch\s+window)$"), "switch_window", {}, "Switched active window."),
        ]
        for _wpat, _wtool, _wargs, _wmsg in _win_table:
            if _wpat.search(p_clean):
                res = self.execute_tool(_wtool, _wargs)
                msg, ok = self._reply(res, _wmsg)
                return {"response": msg, "steps": [{"tool": _wtool, "args": _wargs, "result": res}], "success": ok}

        # fullscreen video via smart_click
        if re.search(r"^(?:(?:make|put|toggle)\s+)?(?:the\s+video|video)\s+(?:in\s+)?(?:fullscreen|full\s+screen|full\s+screen\s+mode)$|^(?:fullscreen|full\s+screen)\s+(?:the\s+)?video$", p_clean):
            res = self.execute_tool("smart_click", {"target": "fullscreen"})
            msg, ok = self._reply(res, "Video fullscreen toggled.")
            return {"response": msg, "steps": [{"tool": "smart_click", "args": {"target": "fullscreen"}, "result": res}], "success": ok}

        snap_target_match = re.match(r"^(?:snap|tile)\s+(.+?)\s+(left|right|up|down)$", p_clean)
        if snap_target_match:
            win_target = snap_target_match.group(1).strip()
            direction = snap_target_match.group(2).strip()
            res = self.execute_tool("snap_window", {"direction": direction, "window_name": win_target})
            msg, ok = self._reply(res, f"Snapped {win_target} {direction}.")
            return {"response": msg, "steps": [{"tool": "snap_window", "args": {"direction": direction, "window_name": win_target}, "result": res}], "success": ok}
        focus_win_match = re.match(r"^(?:focus(?:\s+on)?|switch\s+to|bring\s+to\s+front)\s+(?:the\s+)?(.+?)(?:\s+window|\s+app)?$", p_clean)
        if focus_win_match:
            win_target = focus_win_match.group(1).strip()
            if win_target and win_target not in {"desktop", "next desktop", "previous desktop"} and not any(conj in win_target for conj in [" and ", " then "]):
                res = self.execute_tool("focus_window", {"title_or_process": win_target})
                if isinstance(res, dict) and res.get("success"):
                    msg, ok = self._reply(res, f"Focused '{res.get('window', win_target)}'.")
                    return {"response": msg, "steps": [{"tool": "focus_window", "args": {"title_or_process": win_target}, "result": res}], "success": ok}

        close_match = re.match(
            r"^(?:close|shut|exit|kill|quit)\b"
            r"(?:\s+(?:the\s+|this\s+|current\s+|active\s+)?(?:window|app|application|program))?"
            r"(?:\s+(?:of|for)\s+(.+)|\s+(.+))?$",
            p_clean,
        )
        if close_match:
            close_target = (close_match.group(1) or close_match.group(2) or "").strip() or None
            # "kill process chrome" belongs to process termination, not window closing.
            if close_target and close_target.startswith("process"):
                close_target = None
                close_match = None
            elif close_target in {"another", "another one", "another one too", "another window", "this one", "that one", "that too"}:
                close_target = None
        if close_match:
            args = {"window_name": close_target} if close_target else {}
            res = self.execute_tool("close_active_window", args)
            msg, ok = self._reply(
                res, f"Closed {close_target}." if close_target else "Active window closed."
            )
            return {"response": msg, "steps": [{"tool": "close_active_window", "args": args, "result": res}], "success": ok}

        # 10. Workstation Security & Display
        if re.search(r"^(?:lock|lock\s+(?:the\s+)?(?:pc|computer|workstation|screen|windows))$", p_clean):
            res = self.execute_tool("lock_workstation", {})
            msg, ok = self._reply(res, "Workstation locked.")
            return {"response": msg, "steps": [{"tool": "lock_workstation", "args": {}, "result": res}], "success": ok}

        _snap_table = [
            (re.compile(r"^(?:take\s+(?:a\s+)?screenshot|screenshot|capture\s+screen)$"), "take_screenshot", {}, "Screenshot captured."),
            (re.compile(r"^(?:snip|screen\s+snip|take\s+(?:a\s+)?snip)$"), "press_keys", {"keys": ["win", "shift", "s"]}, "Opened screen snip tool."),
        ]
        for _spat, _stool, _sargs, _smsg in _snap_table:
            if _spat.search(p_clean):
                res = self.execute_tool(_stool, _sargs)
                msg, ok = self._reply(res, _smsg)
                return {"response": msg, "steps": [{"tool": _stool, "args": _sargs, "result": res}], "success": ok}

        bright_match = re.match(r"^(?:(?:set|adjust|change)\s+)?(?:(?:screen|display)\s+)?brightness\s+(?:to\s+)?(\d{1,3})(?:\s*%)?$", p_clean)
        if bright_match:
            val = int(bright_match.group(1))
            res = self.execute_tool("set_screen_brightness", {"level_percent": val})
            msg, ok = self._reply(res, f"Screen brightness set to {val}%.")
            return {"response": msg, "steps": [{"tool": "set_screen_brightness", "args": {"level_percent": val}, "result": res}], "success": ok}

        setting_match = re.match(r"^(?:open\s+)?(?:windows\s+)?(wifi|wi-fi|network|bluetooth|display|sound|battery|power|update|updates|apps|storage|privacy|notifications)\s+settings?$", p_clean)
        if setting_match:
            s_name = setting_match.group(1).strip()
            res = self.execute_tool("open_windows_setting", {"setting_name": s_name})
            msg, ok = self._reply(res, f"Opened {s_name} settings.")
            return {"response": msg, "steps": [{"tool": "open_windows_setting", "args": {"setting_name": s_name}, "result": res}], "success": ok}

        # 11. Hardware & System Telemetry (Anchored)
        if re.search(r"^(?:(?:system\s+|pc\s+|hardware\s+)?(?:status|telemetry|specs)|(?:hardware|cpu|ram|battery)(?:\s+status)?)$", p_clean):
            telem = self.execute_tool("get_system_telemetry", {})
            msg = f"CPU is at {telem['cpu_load_percent']}%, RAM load is {telem['ram_usage_percent']}%. Battery: {telem['battery']}."
            return {"response": msg, "steps": [{"tool": "get_system_telemetry", "args": {}, "result": telem}], "success": True}

        if re.search(r"^(?:top\s+processes|what(?:'s|\s+is)\s+using\s+(?:my\s+)?(?:cpu|ram|memory)|memory\s+hogs|cpu\s+hogs)$", p_clean):
            metric = "ram" if ("ram" in p_clean or "memory" in p_clean) else "cpu"
            res = self.execute_tool("get_top_processes", {"sort_by": metric, "limit": 6})
            msg, ok = self._reply(res, f"Top {metric.upper()} consumers: {res.get('summary', 'none')}.")
            return {"response": msg, "steps": [{"tool": "get_top_processes", "args": {"sort_by": metric, "limit": 6}, "result": res}], "success": ok}

        # 12. Volume Calibration & Controls
        vol_match = re.search(
            r"^(?:(?:set|increase|decrease|lower|raise)\s+)?volume\s+(?:to\s+)?(\d{1,3})(?:\s*%)?$",
            p_clean,
        )
        if vol_match:
            val = int(vol_match.group(1))
            res = self.execute_tool("set_system_volume", {"level_percent": val})
            msg, ok = self._reply(res, f"Volume calibrated to {val} percent.")
            return {"response": msg, "steps": [{"tool": "set_system_volume", "args": {"level_percent": val}, "result": res}], "success": ok}

        _vol_table = [
            (re.compile(r"^(?:volume\s+up|turn\s+up\s+volume|louder|increase\s+volume)$"), "volume_up", "Volume increased."),
            (re.compile(r"^(?:volume\s+down|turn\s+down\s+volume|quieter|decrease\s+volume|lower\s+volume)$"), "volume_down", "Volume decreased."),
        ]
        for _vpat, _vact, _vmsg in _vol_table:
            if _vpat.search(p_clean):
                res = self.execute_tool("media_control", {"action": _vact})
                msg, ok = self._reply(res, _vmsg)
                return {"response": msg, "steps": [{"tool": "media_control", "args": {"action": _vact}, "result": res}], "success": ok}

        # 13. YouTube & Google Direct Search & Playback (<10ms launch)
        yt_match = re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?(?:search\s+(?:on\s+)?youtube\s+for|search\s+for\s+(.+?)\s+on\s+youtube|play\s+(.+?)\s+on\s+youtube|youtube\s+(?:search\s+for|search)|open\s+youtube\s+and\s+(?:search|play)\s+for?|search\s+youtube|open\s+youtube)\s*(.*)", p_clean)
        if yt_match:
            yt_q = yt_match.group(1) or yt_match.group(2) or yt_match.group(3) or ""
            yt_q = yt_q.strip(" ?.")
            if yt_q:
                res = self.execute_tool("smart_search", {"query": yt_q, "engine": "youtube"})
                msg, ok = self._reply(res, f"Playing '{yt_q}' on YouTube.")
                return {"response": msg, "steps": [{"tool": "smart_search", "args": {"query": yt_q, "engine": "youtube"}, "result": res}], "success": ok}

        search_match = re.search(r"^(?:search\s+(?:google\s+|web\s+)?for|google|search\s+web\s+for)\s+(.+)", p_clean)
        if search_match:
            s_query = search_match.group(1).strip(" ?.")
            if s_query:
                res = self.execute_tool("smart_search", {"query": s_query, "engine": "google"})
                msg, ok = self._reply(res, f"Searching Google for '{s_query}'.")
                return {"response": msg, "steps": [{"tool": "smart_search", "args": {"query": s_query, "engine": "google"}, "result": res}], "success": ok}

        # 14. Media Playback Controls (Anchored) - data-driven
        # Explicit mute/unmute (state-aware) instead of a toggle, so "mute" and
        # "unmute" never get confused regardless of the current state.
        if re.search(r"^(?:mute|silence)$", p_clean):
            res = self.execute_tool("set_system_mute", {"mute": True})
            msg, ok = self._reply(res, res.get("message", "Muted."))
            return {"response": msg, "steps": [{"tool": "set_system_mute", "args": {"mute": True}, "result": res}], "success": res.get("success", ok)}
        if re.search(r"^(?:unmute)$", p_clean):
            res = self.execute_tool("set_system_mute", {"mute": False})
            msg, ok = self._reply(res, res.get("message", "Unmuted."))
            return {"response": msg, "steps": [{"tool": "set_system_mute", "args": {"mute": False}, "result": res}], "success": res.get("success", ok)}

        _media_table = [
            (re.compile(r"^(?:play|pause|resume|play\s+pause|stop\s+music)$"), "play_pause", "Playback toggled."),
            (re.compile(r"^(?:next\s+track|next\s+song|skip\s+song)$"), "next", "Skipped to next track."),
            (re.compile(r"^(?:previous\s+track|prev\s+track|prev\s+song)$"), "prev", "Restarted track."),
        ]
        for _mpat, _mact, _mmsg in _media_table:
            if _mpat.search(p_clean):
                res = self.execute_tool("media_control", {"action": _mact})
                msg, ok = self._reply(res, _mmsg)
                return {"response": msg, "steps": [{"tool": "media_control", "args": {"action": _mact}, "result": res}], "success": ok}
        # 14. Direct Terminal / PowerShell Execution
        term_match = re.match(r"^(?:run|execute|exec|powershell|cmd|terminal)\s+(?:command\s+)?(.+)", p_clean)
        if term_match:
            cmd_to_run = term_match.group(1).strip()
            res = self.execute_tool("run_powershell", {"command": cmd_to_run})
            if not res.get("success", True):
                return {
                    "response": res.get("error") or res.get("message") or "Command failed.",
                    "steps": [{"tool": "run_powershell", "args": {"command": cmd_to_run}, "result": res}],
                    "success": False
                }
            stdout = res.get("stdout", "").strip()
            summary = stdout[:160] if stdout else "Command completed."
            return {
                "response": f"Executed: {summary}",
                "steps": [{"tool": "run_powershell", "args": {"command": cmd_to_run}, "result": res}],
                "success": True
            }

        common_shell_cmds = [
            "ipconfig", "dir", "ls", "systeminfo", "hostname", "whoami", "pwd",
            "netstat", "tasklist", "taskkill", "winget", "cls", "clear", "curl", "netsh"
        ]
        cmd_head = p_clean.split()[0] if p_clean.split() else ""
        if p_clean in common_shell_cmds or cmd_head in common_shell_cmds or cmd_head in ["git", "python", "py", "pip", "node", "npm", "npx", "docker"]:
            res = self.execute_tool("run_powershell", {"command": p_clean})
            if not res.get("success", True):
                return {
                    "response": res.get("error") or res.get("message") or "Command failed.",
                    "steps": [{"tool": "run_powershell", "args": {"command": p_clean}, "result": res}],
                    "success": False
                }
            stdout = res.get("stdout", "").strip()
            summary = stdout[:160] if stdout else "Command completed."
            return {
                "response": f"Output: {summary}",
                "steps": [{"tool": "run_powershell", "args": {"command": p_clean}, "result": res}],
                "success": True
            }

        # 15. Time & Date
        if re.search(r"\b(what\s+time\s+is\s+it|current\s+time|tell\s+me\s+the\s+time)\b|^time$", p_clean):
            now_str = datetime.now().strftime("%I:%M %p")
            return {"response": f"Current time is {now_str}.", "steps": [], "success": True}

        if re.search(r"\b(what\s+is\s+the\s+date|today's\s+date|tell\s+me\s+the\s+date)\b|^date$", p_clean):
            today_str = datetime.now().strftime("%A, %B %d, %Y")
            return {"response": f"Today is {today_str}.", "steps": [], "success": True}

        # 16. Long-Term Memory & User Facts
        remember_match = re.match(r"^remember(?:\s+that)?\s+(.+)", prompt.strip(), re.IGNORECASE)
        if remember_match:
            fact = remember_match.group(1).strip()
            parts = re.split(r"\s+(?:is|are|=|:)\s+", fact, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                key, val = parts[0].strip(), parts[1].strip()
            else:
                key, val = "fact", fact
            res = self._execute_tool_inner(
                "remember_fact", {"key": key, "value": val}, confirmation_granted=True
            )
            self._log_tool_call("remember_fact", {"key": key, "value": val}, res)
            return {
                "response": f"I'll remember that {key} is {val}.",
                "steps": [{"tool": "remember_fact", "args": {"key": key, "value": val}, "result": res}],
                "success": True
            }

        forget_match = re.match(r"^(?:forget(?:\s+about)?|delete\s+memory)\s+(.+)$", p_clean)
        if forget_match:
            key = forget_match.group(1).strip()
            res = self._execute_tool_inner("forget_fact", {"key": key}, confirmation_granted=True)
            self._log_tool_call("forget_fact", {"key": key}, res)
            return {
                "response": res.get("message", f"Forgot '{key}'."),
                "steps": [{"tool": "forget_fact", "args": {"key": key}, "result": res}],
                "success": res.get("success", False),
            }

        if re.search(r"^(?:what\s+do\s+you\s+remember(?:\s+about\s+me)?|list\s+(?:my\s+)?memories|show\s+preferences)$", p_clean):
            prefs = self.memory.get_all_preferences()
            if not prefs:
                return {"response": "I don't have any stored facts or preferences yet.", "steps": [], "success": True}
            summary = "; ".join(f"{k}: {v}" for k, v in list(prefs.items())[:8])
            return {"response": f"Stored memories: {summary}.", "steps": [], "success": True}

        # 17. Screen Understanding & Vision
        if re.search(r"^(?:what(?:'s|\s+is)\s+on\s+(?:the|my)\s+screen|describe\s+(?:the\s+|my\s+)?screen|look\s+at\s+(?:the|my)\s+screen|screen\s+summary)$", p_clean):
            res = self.execute_tool("describe_screen", {})
            desc = res.get("description", "Screen captured.")
            msg, ok = self._reply(res, desc)
            return {"response": msg, "steps": [{"tool": "describe_screen", "args": {}, "result": res}], "success": ok}

        # 18. Timers and Reminders (supports seconds, minutes, hours & cancellation)
        remind_match = re.match(
            r"^(?:remind\s+me\s+to\s+(.+)\s+in\s+(\d+(?:\.\d+)?)\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs)"
            r"|set\s+(?:a\s+)?timer\s+for\s+(\d+(?:\.\d+)?)\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs)(?:\s+for\s+(.+))?)$",
            p_clean,
        )
        if remind_match:
            if remind_match.group(1) and remind_match.group(2):
                rem_msg = remind_match.group(1).strip()
                amount = float(remind_match.group(2))
                unit = remind_match.group(3).lower()
            else:
                amount = float(remind_match.group(4))
                unit = remind_match.group(5).lower()
                rem_msg = (remind_match.group(6) or "timer").strip()
            if unit.startswith("sec"):
                args = {"message": rem_msg, "seconds": amount}
            elif unit.startswith(("hour", "hr")):
                args = {"message": rem_msg, "hours": amount}
            else:
                args = {"message": rem_msg, "minutes": amount}
            res = self.execute_tool("set_reminder", args)
            msg, ok = self._reply(res, res.get("message", f"Reminder set for {amount} {unit}."))
            return {"response": msg, "steps": [{"tool": "set_reminder", "args": args, "result": res}], "success": ok}

        cancel_rem_match = re.match(r"^(?:cancel|delete|remove)\s+reminder\s+([a-zA-Z0-9_]+)$", p_clean)
        if cancel_rem_match:
            rem_id = cancel_rem_match.group(1).strip()
            res = self.execute_tool("cancel_active_reminder", {"reminder_id": rem_id})
            msg, ok = self._reply(res, res.get("message", f"Reminder '{rem_id}' cancelled."))
            return {"response": msg, "steps": [{"tool": "cancel_active_reminder", "args": {"reminder_id": rem_id}, "result": res}], "success": ok}

        if re.search(r"^(?:list\s+(?:my\s+)?reminders|what\s+are\s+my\s+reminders|show\s+reminders|active\s+timers)$", p_clean):
            res = self.execute_tool("list_active_reminders", {})
            rems = res.get("reminders", [])
            if not rems:
                msg, ok = self._reply(res, "You have no upcoming reminders.")
                return {"response": msg, "steps": [{"tool": "list_active_reminders", "args": {}, "result": res}], "success": ok}
            lines = [f"{r['message']} at {r['due_time']}" for r in rems]
            return {
                "response": f"You have {len(rems)} reminders: " + "; ".join(lines) + ".",
                "steps": [{"tool": "list_active_reminders", "args": {}, "result": res}],
                "success": True
            }

        # 19. Google Workspace (Gmail & Calendar) Quick Overview
        if re.search(r"^(?:check\s+(?:my\s+)?(?:email|emails|gmail|inbox)|unread\s+(?:emails|messages)|show\s+(?:my\s+)?emails)$", p_clean):
            res = self.execute_tool("list_unread_emails", {"max_results": 5})
            if not res.get("success"):
                return {"response": res.get("error", "Failed to access Gmail."), "steps": [{"tool": "list_unread_emails", "args": {}, "result": res}], "success": False}
            emails = res.get("emails", [])
            if not emails:
                msg, ok = self._reply(res, "You have no unread emails.")
                return {"response": msg, "steps": [{"tool": "list_unread_emails", "args": {}, "result": res}], "success": ok}
            summaries = [f"From {e['from'].split('<')[0].strip()}: {e['subject']}" for e in emails[:3]]
            return {
                "response": f"You have {len(emails)} unread emails. Recent: " + "; ".join(summaries) + ".",
                "steps": [{"tool": "list_unread_emails", "args": {}, "result": res}],
                "success": True
            }

        if re.search(r"^(?:upcoming\s+events|what\s+is\s+on\s+my\s+calendar|check\s+(?:my\s+)?calendar|calendar\s+events)$", p_clean):
            res = self.execute_tool("list_upcoming_events", {"max_results": 5})
            if not res.get("success"):
                return {"response": res.get("error", "Failed to access Google Calendar."), "steps": [{"tool": "list_upcoming_events", "args": {}, "result": res}], "success": False}
            events = res.get("events", [])
            if not events:
                msg, ok = self._reply(res, "No upcoming calendar events found.")
                return {"response": msg, "steps": [{"tool": "list_upcoming_events", "args": {}, "result": res}], "success": ok}
            summaries = [f"{ev['summary']} at {ev['start']}" for ev in events[:3]]
            return {
                "response": f"Upcoming events: " + "; ".join(summaries) + ".",
                "steps": [{"tool": "list_upcoming_events", "args": {}, "result": res}],
                "success": True
            }

        # 16. Clipboard Interaction
        if re.search(r"\b(read\s+clipboard|what\s+is\s+on\s+clipboard|paste\s+clipboard)\b", p_clean):
            res = self.execute_tool("read_clipboard", {})
            content = res.get("content", "")
            if content:
                snippet = content[:80] + ("..." if len(content) > 80 else "")
                msg, ok = self._reply(res, f"Clipboard contents: '{snippet}'.")
                return {"response": msg, "steps": [{"tool": "read_clipboard", "args": {}, "result": res}], "success": ok}
            msg, ok = self._reply(res, "Clipboard is currently empty.")
            return {"response": msg, "steps": [{"tool": "read_clipboard", "args": {}, "result": res}], "success": ok}

        copy_clip_match = re.match(r"^(?:copy|write)\s+(.+?)\s+to\s+(?:the\s+)?clipboard$", p_clean)
        if copy_clip_match:
            raw_clip = re.match(r"^(?:copy|write)\s+(.+?)\s+to\s+(?:the\s+)?clipboard$", prompt.strip(), re.IGNORECASE)
            clip_text = raw_clip.group(1).strip() if raw_clip else copy_clip_match.group(1).strip()
            res = self.execute_tool("write_clipboard", {"text": clip_text})
            msg, ok = self._reply(res, "Copied text to clipboard.")
            return {"response": msg, "steps": [{"tool": "write_clipboard", "args": {"text": clip_text}, "result": res}], "success": ok}

        weather_match = re.match(r"^(?:(?:what(?:'s|\s+is)\s+(?:the\s+)?)?weather(?:\s+(?:like\s+)?(?:in|at|for)\s+(.+))?)$", p_clean)
        if weather_match:
            loc = (weather_match.group(1) or "").strip()
            res = self.execute_tool("get_weather", {"location": loc})
            msg, ok = self._reply(res, res.get("message", "Could not fetch weather."))
            return {"response": msg, "steps": [{"tool": "get_weather", "args": {"location": loc}, "result": res}], "success": ok}

        # 17. GitHub Profile Direct Shortcut (Anchored)
        if re.search(r"^(?:(?:go\s+to|open)\s+)?(?:my\s+)?github(?:\s+(?:profile|account|page|user))?$|^my\s+github$|^go\s+to\s+my\s+profile\s+github$", p_clean):
            res = self.execute_tool("launch_application", {"app_name": "https://github.com/Shafyydev"})
            return {
                "response": "Opening your GitHub profile.",
                "steps": [{"tool": "launch_application", "args": {"app_name": "https://github.com/Shafyydev"}, "result": res}],
                "success": True
            }

        # Windows system keys - data-driven
        _sys_table = [
            (re.compile(r"^(?:start\s+menu|windows\s+key|press\s+windows|press\s+win|press\s+start|open\s+start\s+menu)$"), ["win"], "Opened Start menu."),
            (re.compile(r"^(?:(?:open\s+)?(?:task\s*mgr|task\s*manager))$"), ["ctrl", "shift", "escape"], "Opened Task Manager."),
            (re.compile(r"^(?:(?:open\s+)?(?:file\s+explorer|my\s+computer|this\s+pc|explorer))$"), ["win", "e"], "Opened File Explorer."),
            (re.compile(r"^(?:run\s+dialog|open\s+run)$"), ["win", "r"], "Opened Run dialog."),
            (re.compile(r"^(?:(?:open\s+)?(?:action\s+center|notification\s+center|notifications|show\s+notifications))$"), ["win", "a"], "Opened Action Center."),
            (re.compile(r"^(?:(?:open\s+)?clipboard\s+history)$"), ["win", "v"], "Opened Clipboard History."),
        ]
        for _spat, _skeys, _smsg in _sys_table:
            if _spat.search(p_clean) or (_skeys == ["win"] and p_clean in ["start", "win", "windows"]):
                res = self.execute_tool("press_keys", {"keys": _skeys})
                msg, ok = self._reply(res, _smsg)
                return {"response": msg, "steps": [{"tool": "press_keys", "args": {"keys": _skeys}, "result": res}], "success": ok}
        # 19. File & Folder Operations
        if re.search(r"^(?:empty|clean|clear)\s+(?:the\s+)?(?:recycle\s+bin|trash|bin)$", p_clean):
            res = self.execute_tool("empty_recycle_bin", {})
            msg = res.get("message") or res.get("error") or "Could not empty the Recycle Bin."
            return {"response": msg, "steps": [{"tool": "empty_recycle_bin", "args": {}, "result": res}], "success": res.get("success", False)}

        cf_match = re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?create\s+(?:a\s+)?(?:new\s+)?(?:folder|directory|dir)\s+(?:named\s+|called\s+)?([^\s]+)(?:\s+(?:on|in)\s+([^\s]+))?", p_clean)
        if cf_match:
            f_name = cf_match.group(1).strip()
            f_loc = cf_match.group(2).strip() if cf_match.group(2) else "desktop"
            res = self.execute_tool("create_file_or_folder", {"item_type": "folder", "name": f_name, "location": f_loc})
            msg, ok = self._reply(res, f"Created folder '{f_name}' on {f_loc}.")
            return {"response": msg, "steps": [{"tool": "create_file_or_folder", "args": {"item_type": "folder", "name": f_name, "location": f_loc}, "result": res}], "success": ok}

        cfile_match = re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?create\s+(?:a\s+)?(?:new\s+)?(?:text\s+)?file\s+(?:named\s+|called\s+)?([^\s]+)(?:\s+(?:on|in)\s+([^\s]+))?(?:\s+with\s+(?:content|text)\s+(.+))?", p_clean)
        if cfile_match:
            f_name = cfile_match.group(1).strip()
            f_loc = cfile_match.group(2).strip() if cfile_match.group(2) else "desktop"
            f_content = cfile_match.group(3).strip() if cfile_match.group(3) else ""
            res = self.execute_tool("create_file_or_folder", {"item_type": "file", "name": f_name, "location": f_loc, "content": f_content})
            msg, ok = self._reply(res, f"Created file '{f_name}' on {f_loc}.")
            return {"response": msg, "steps": [{"tool": "create_file_or_folder", "args": {"item_type": "file", "name": f_name, "location": f_loc, "content": f_content}, "result": res}], "success": ok}

        del_match = re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?delete\s+(?:the\s+)?(?:file|folder|dir|item)\s+([^\s]+)(?:\s+(?:from|on|in)\s+([^\s]+))?", p_clean)
        if del_match:
            t_name = del_match.group(1).strip()
            t_loc = del_match.group(2).strip() if del_match.group(2) else "desktop"
            res = self.execute_tool("delete_file_or_folder", {"name": t_name, "location": t_loc})
            msg = res.get("message") or res.get("error") or f"Could not delete '{t_name}'."
            return {"response": msg, "steps": [{"tool": "delete_file_or_folder", "args": {"name": t_name, "location": t_loc}, "result": res}], "success": res.get("success", False)}

        list_match = re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?(?:list|show|view|display|what's\s+in)\s+(?:the\s+)?files?(?:\s+(?:in|on)\s+([^\s]+))?", p_clean)
        if list_match:
            loc = list_match.group(1).strip() if list_match.group(1) else "desktop"
            res = self.execute_tool("list_directory", {"location": loc})
            if res.get("success"):
                msg, ok = self._reply(res, f"Files in {loc} ({res.get('count', 0)} items): {res.get('summary', '')}.")
                return {"response": msg, "steps": [{"tool": "list_directory", "args": {"location": loc}, "result": res}], "success": ok}
            return {"response": res.get("error", f"Could not list {loc}."), "steps": [{"tool": "list_directory", "args": {"location": loc}, "result": res}], "success": False}

        # 20. Network & Diagnostics
        if re.match(
            r"^(?:(?:please|can\s+you|could\s+you)\s+)?"
            r"(?:(?:what\s+is|what's|show|show\s+me|tell\s+me)\s+)?(?:my\s+)?ip\s+address\b"
            r"|^(?:(?:please|can\s+you|could\s+you)\s+)?(?:what\s+is|what's|show|show\s+me|tell\s+me)\s+(?:my\s+)?ip\b",
            p_clean,
        ):
            net = self.execute_tool("get_network_info", {})
            return {"response": f"Device: {net.get('hostname')}, Local IP: {net.get('local_ip')}.", "steps": [{"tool": "get_network_info", "args": {}, "result": net}], "success": True}

        if re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?(?:what(?:'s|\s+is)|how\s+much|check|show|tell\s+me)\s+(?:my\s+)?(?:storage|disk\s+space|free\s+space|hard\s+drive|drive\s+space)\b|^(?:storage|disk\s+space|free\s+space|hard\s+drive|drive\s+space)\b", p_clean):
            telem = self.execute_tool("get_system_telemetry", {})
            free_gb = telem.get("disk_free_gb") if isinstance(telem, dict) else None
            total_gb = telem.get("disk_total_gb") if isinstance(telem, dict) else None
            if free_gb is None or total_gb is None:
                return {"response": "Could not read drive telemetry.", "steps": [{"tool": "get_system_telemetry", "args": {}, "result": telem}], "success": False}
            return {"response": f"Primary drive has {free_gb} GB free out of {total_gb} GB.", "steps": [{"tool": "get_system_telemetry", "args": {}, "result": telem}], "success": True}

        if re.search(r"^(?:what\s+(?:app|window)\s+is\s+(?:open|active|focused)|current\s+window|active\s+window)$", p_clean):
            win = self.execute_tool("get_active_window", {})
            title = win.get("title") or "Unknown"
            proc = win.get("process_name") or win.get("app_name") or ""
            label = f"{title} ({proc})" if proc else title
            return {"response": f"Current active window: {label}.", "steps": [{"tool": "get_active_window", "args": {}, "result": win}], "success": bool(win.get("success", True))}

        if re.search(r"\b(?:what\s+(?:is|apps\s+are)\s+running|running\s+(?:apps|windows|tasks)|active\s+windows|show\s+tasks)\b", p_clean):
            wins = self.execute_tool("get_running_windows", {})
            summary = ", ".join(wins[:8]) if wins else "No top-level windows detected."
            return {"response": f"Active windows: {summary}.", "steps": [{"tool": "get_running_windows", "args": {}, "result": {"windows": wins}}], "success": True}

        # 21. System Power Management
        if re.search(r"\b(restart|reboot)\s+(?:the\s+|my\s+)?(?:pc|computer|system|windows)\b", p_clean):
            res = self.execute_tool("system_power", {"action": "restart", "delay_sec": 10})
            msg = res.get("message") or res.get("error") or "Could not schedule a reboot."
            return {"response": msg, "steps": [{"tool": "system_power", "args": {"action": "restart", "delay_sec": 10}, "result": res}], "success": res.get("success", False)}

        if re.search(r"\b(shut\s*down|power\s+off)\s+(?:the\s+|my\s+)?(?:pc|computer|system|windows)\b", p_clean):
            res = self.execute_tool("system_power", {"action": "shutdown", "delay_sec": 15})
            msg = res.get("message") or res.get("error") or "Could not schedule shutdown."
            return {"response": msg, "steps": [{"tool": "system_power", "args": {"action": "shutdown", "delay_sec": 15}, "result": res}], "success": res.get("success", False)}

        if re.search(r"\b(sleep|suspend)\s+(?:the\s+|my\s+)?(?:pc|computer|system|windows)\b", p_clean):
            res = self.execute_tool("system_power", {"action": "sleep"})
            msg = res.get("message") or res.get("error") or "Could not put the system to sleep."
            return {"response": msg, "steps": [{"tool": "system_power", "args": {"action": "sleep"}, "result": res}], "success": res.get("success", False)}

        if re.search(r"^(?:(?:please|can\s+you|could\s+you)\s+)?(?:cancel|abort|stop)\s+(?:the\s+)?(?:shutdown|restart|reboot)\b", p_clean):
            res = self.execute_tool("system_power", {"action": "cancel"})
            msg, ok = self._reply(res, "Shutdown aborted.")
            return {"response": msg, "steps": [{"tool": "system_power", "args": {"action": "cancel"}, "result": res}], "success": ok}

        # 22. Application, Folder, Drive, and Website Navigation (Zero-Latency Local Launch)
        if p_clean in {"send", "send message", "send a message"}:
            return {
                "response": "Tell me the message to send. Use: send message to NAME saying TEXT.",
                "steps": [],
                "success": False,
            }

        whatsapp_send = re.match(
            r"^(?:(?:please|can\s+you|could\s+you)\s+)?"
            r"(?:send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|messg|mesg|text)\s+(?:to\s+)?|whatsapp\s+(?:to\s+)?|message\s+(?:to\s+)?|text\s+(?:to\s+)?)"
            r"(.+?)\s+(?:saying|telling(?:\s+(?:her|him|them))?|with(?:\s+(?:the\s+)?text)?)\s+(.+)$",
            p_clean,
        )
        whatsapp_send_rev = None if whatsapp_send else re.match(
            r"^(?:(?:please|can\s+you|could\s+you)\s+)?"
            r"send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|messg|mesg|text)\s+"
            r"(?:saying|telling(?:\s+(?:her|him|them))?|with(?:\s+(?:the\s+)?text)?)\s+(.+?)\s+to\s+(.+)$",
            p_clean,
        )
        whatsapp_send_person_first = None if (whatsapp_send or whatsapp_send_rev) else re.match(
            r"^(?:(?:please|can\s+you|could\s+you)\s+)?"
            r"(?:send\s+(?!(?:a|an|the|this|that|it|my)\b)(.+?)\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|messg|mesg|text)\s+(?:(?:saying|telling)\s+)?|send\s+(?:in|on)\s+whatsapp\s+to\s+(.+?)\s+(?:saying|telling)\s+|whatsapp\s+(.+?)\s+(?:saying|telling)\s+)(.+)$",
            p_clean,
        )
        whatsapp_send_followup = None
        if not whatsapp_send and not whatsapp_send_rev and not whatsapp_send_person_first and self.last_whatsapp_contact:
            whatsapp_send_followup = re.match(
                r"^send\s+(?:(?:her|him|them)\s+)?(?:"
                r"another\s+(?:one\s+)?(?:saying\s+)?|"
                r"(?:another\s+)?(?:message|msg|messg|mesg)\s+(?:saying\s+)?|"
                r"(?:a\s+)?(?:message|msg|messg|mesg|text)\s+(?:saying|with(?:\s+(?:the\s+)?text)?)\s+"
                r")(.+)$",
                p_clean,
            )
        # "send reshmaa hello" carries no keyword, so it used to fall through to
        # the model, stall, and report that nothing ran.  A short bare form is a
        # WhatsApp send unless the first word is a common non-name word.
        whatsapp_send_bare = None
        if not (whatsapp_send or whatsapp_send_rev or whatsapp_send_person_first or whatsapp_send_followup):
            candidate = re.match(
                r"^(?:(?:please|can\s+you|could\s+you)\s+)?send\s+"
                r"([a-z][a-z0-9._'-]{1,30})\s+(.+)$",
                p_clean,
            )
            if candidate and candidate.group(1) not in self.WHATSAPP_SEND_STOPWORDS:
                whatsapp_send_bare = candidate
        if whatsapp_send or whatsapp_send_rev or whatsapp_send_person_first or whatsapp_send_followup or whatsapp_send_bare:
            if whatsapp_send:
                contact = whatsapp_send.group(1).strip()
                message = whatsapp_send.group(2).strip()
            elif whatsapp_send_rev:
                message = whatsapp_send_rev.group(1).strip()
                contact = whatsapp_send_rev.group(2).strip()
            elif whatsapp_send_person_first:
                contact = (
                    whatsapp_send_person_first.group(1)
                    or whatsapp_send_person_first.group(2)
                    or whatsapp_send_person_first.group(3)
                    or ""
                ).strip()
                message = whatsapp_send_person_first.group(4).strip()
            elif whatsapp_send_bare:
                contact = whatsapp_send_bare.group(1).strip()
                message = whatsapp_send_bare.group(2).strip()
            else:
                contact = self.last_whatsapp_contact
                message = whatsapp_send_followup.group(1).strip()
            contact = re.sub(r"^(?:the|my)\s+", "", contact).strip()
            if contact.lower() in self.WHATSAPP_CONTACT_PLACEHOLDERS:
                if self.last_whatsapp_contact:
                    contact = self.last_whatsapp_contact
                else:
                    # "send another one" with no previous chat has no target;
                    # searching for a person literally named "another" cannot work.
                    return {
                        "response": "Who should I send that to? Say the contact name.",
                        "steps": [],
                        "success": False,
                    }
            message = re.sub(
                r"\s+(?:in|on)\s+whatsapp$", "", message, flags=re.IGNORECASE
            ).strip()
            res = self.execute_tool("send_whatsapp_message", {"contact_name": contact, "message": message})
            if isinstance(res, dict) and res.get("success"):
                self.last_whatsapp_contact = contact
            msg, ok = self._reply(res, f"Sent WhatsApp message to {contact}.")
            return {
                "response": msg,
                "steps": [{"tool": "send_whatsapp_message", "args": {"contact_name": contact, "message": message}, "result": res}],
                "success": ok,
            }

        # If WhatsApp is already running, short "go to <name>" commands
        # refer to a chat rather than a Windows executable.  Explicit "open X"
        # commands retain their normal application-launch behavior.
        whatsapp_running = any(
            "whatsapp" in title.lower() for title in tools.get_running_windows()
        )
        whatsapp_implicit_chat = re.match(
            r"^(?:go\s+to|select)\s+(.+)$", p_clean,
        )
        if whatsapp_running and whatsapp_implicit_chat:
            contact = whatsapp_implicit_chat.group(1).strip()
            # Drives and real folders must keep their normal navigation behavior
            # even while WhatsApp is open, otherwise "go to d drive" is treated
            # as a contact name.
            target_path = tools.resolve_system_target(contact)
            resolves_to_path = bool(target_path and target_path.exists())
            if (
                1 <= len(contact.split()) <= 4
                and contact not in {"ok", "enter", "cancel", "search", "first chat", "first result"}
                and not contact.endswith(" chat")
                and not resolves_to_path
            ):
                res = self.execute_tool("open_whatsapp_chat", {"contact_name": contact})
                if res.get("success"):
                    self.last_whatsapp_contact = contact
                return {
                    "response": res.get("message") or res.get("error") or f"Could not find WhatsApp chat '{contact}'.",
                    "steps": [{"tool": "open_whatsapp_chat", "args": {"contact_name": contact}, "result": res}],
                    "success": res.get("success", False),
                }

        whatsapp_search = re.match(
            r"^(?:search|find)\s+(.+?)\s+(?:in|on)\s+whatsapp$", p_clean,
        )
        if whatsapp_search:
            contact = whatsapp_search.group(1).strip()
            res = self.execute_tool("open_whatsapp_chat", {"contact_name": contact})
            if res.get("success"):
                self.last_whatsapp_contact = contact
            return {
                "response": res.get("message") or res.get("error") or f"Could not find WhatsApp chat '{contact}'.",
                "steps": [{"tool": "open_whatsapp_chat", "args": {"contact_name": contact}, "result": res}],
                "success": res.get("success", False),
            }

        whatsapp_chat = re.match(
            r"^(?:go\s+to|open|show|select)\s+(?:the\s+)?(.+?)\s+(?:whatsapp\s+)?chat$",
            p_clean,
        )
        if whatsapp_chat:
            contact = whatsapp_chat.group(1).strip()
            res = self.execute_tool("open_whatsapp_chat", {"contact_name": contact})
            if res.get("success"):
                self.last_whatsapp_contact = contact
            return {
                "response": res.get("message") or res.get("error") or f"Could not open WhatsApp chat '{contact}'.",
                "steps": [{"tool": "open_whatsapp_chat", "args": {"contact_name": contact}, "result": res}],
                "success": res.get("success", False),
            }

        # Direct Drive navigation (e.g. "open d drive", "go to d drive", "d drive", "drive c", "f:")
        drive_direct = re.search(r"^(?:(?:go\s+to|open|navigate\s+to|show\s+me)\s+)?(?:the\s+)?([a-zA-Z])\s+(?:drive|disk)$|^(?:(?:go\s+to|open|navigate\s+to|show\s+me)\s+)?(?:the\s+)?(?:drive|disk)\s+([a-zA-Z])$|^([a-zA-Z]):\\?$", p_clean)
        if drive_direct:
            letter = (drive_direct.group(1) or drive_direct.group(2) or drive_direct.group(3)).upper()
            target_drive = f"{letter}:\\"
            res = self.execute_tool("open_folder", {"folder_path": target_drive})
            msg, ok = self._reply(res, f"Opened {letter} drive in File Explorer.")
            return {
                "response": msg,
                "steps": [{"tool": "open_folder", "args": {"folder_path": target_drive}, "result": res}],
                "success": ok
            }

        nav_match = re.match(r"^(?:go to|navigate to|visit|open up|open|launch|start|take me to|show me|bring up)\s+(?:the\s+)?(?:my\s+)?(.+)", p_clean)
        if nav_match:
            target = nav_match.group(1).strip()
            # "open a new tab in brave browser" targets Brave, not a literal app
            # name; clean it before the compound/length guards below.
            target = tools.clean_app_target(target)
            if target in {"the", "a", "an"}:
                return {
                    "response": "I did not catch which application or chat you want to open.",
                    "steps": [],
                    "success": False,
                }
            # If target is a compound multi-step instruction, let the orchestrator handle it
            if any(conj in target for conj in [" and ", ", and ", ", go to ", ", open ", "then ", "what files are inside", "tell me what", "click"]):
                return None
            if len(target.split()) > 5 and not (target.startswith("http") or any(target.endswith(ext) for ext in [".com", ".org", ".net", ".io"])):
                return None

            # Priority 1: Check if target resolves to a drive, folder, or directory
            resolved_p = tools.resolve_system_target(target)
            if resolved_p and resolved_p.exists():
                res = self.execute_tool("open_folder", {"folder_path": str(resolved_p)})
                folder_name = resolved_p.name if resolved_p.name else str(resolved_p)
                msg, ok = self._reply(res, f"Opened {folder_name} in File Explorer.")
                return {"response": msg, "steps": [{"tool": "open_folder", "args": {"folder_path": str(resolved_p)}, "result": res}], "success": ok}

            # Priority 2: Standard application or website launch
            res = self.execute_tool("launch_application", {"app_name": target})
            msg, ok = self._reply(res, f"Opening {target}.")
            return {
                "response": msg,
                "steps": [{"tool": "launch_application", "args": {"app_name": target}, "result": res}],
                "success": ok
            }

        # Standalone folder mention (e.g. "codehub", "code hub folder", "tars folder")
        if not p_clean.startswith("create ") and not p_clean.startswith("delete "):
            if any(kw in p_clean for kw in ["folder", "directory", "drive"]) or p_clean in ["codehub", "tars"]:
                resolved_p = tools.resolve_system_target(p_clean)
                if resolved_p and resolved_p.exists():
                    res = self.execute_tool("open_folder", {"folder_path": str(resolved_p)})
                    folder_name = resolved_p.name if resolved_p.name else str(resolved_p)
                    msg, ok = self._reply(res, f"Opened {folder_name} in File Explorer.")
                    return {"response": msg, "steps": [{"tool": "open_folder", "args": {"folder_path": str(resolved_p)}, "result": res}], "success": ok}

        # 19. App Terminating (e.g. "kill chrome", "close notepad", "exit spotify")
        kill_match = re.match(r"^(?:kill|terminate)\s+(?:the\s+)?(?:app\s+)?(.+)", p_clean)
        if kill_match:
            proc_target = kill_match.group(1).strip()
            res = self.execute_tool("kill_process", {"process_name": proc_target})
            msg, ok = self._reply(res, f"Terminated {proc_target}.")
            return {
                "response": msg,
                "steps": [{"tool": "kill_process", "args": {"process_name": proc_target}, "result": res}],
                "success": ok
            }

        # 20. Arithmetic / Math Calculations
        p_calc = p_clean.replace("times", "*").replace("divided by", "/").replace("plus", "+").replace("minus", "-").replace("x", "*")
        math_match = re.match(r"^(?:calculate|what is|compute)\s+([0-9\s\+\-\*\/\(\)\.\%]+)", p_calc)
        if math_match:
            val = safe_math_eval(math_match.group(1))
            if val is not None:
                return {"response": f"The answer is {val}.", "steps": [], "success": True}

        # 21. Smart Web & Desktop In-App Search
        search_match = re.match(r"^(?:search\s+for|search\s+web\s+for|search\s+youtube\s+for|search|google)\s+(.+)", p_clean)
        if search_match:
            raw_query = search_match.group(1).strip(" ?.")
            # Check if query contains "on youtube" or "in youtube"
            engine = "youtube" if "youtube" in p_clean else "google"
            res = self.execute_tool("smart_search", {"query": raw_query, "engine": engine})
            fallback_msg = f"Searching for '{raw_query}'."
            msg = (res.get("message") if isinstance(res, dict) else None) or fallback_msg
            msg, ok = self._reply(res, msg)
            return {"response": msg, "steps": [{"tool": "smart_search", "args": {"query": raw_query, "engine": engine}, "result": res}], "success": ok}

        # 22. Free Online Knowledge (Wikipedia QA - Short Factual Lookups Only)
        info_match = re.match(r"^(?:who is|what is|where is|define)\s+([a-zA-Z0-9\s\.\-_]{1,50})$", p_clean)
        if info_match:
            query = info_match.group(1).strip(" ?.")
            if len(query.split()) <= 4 and not any(w in query for w in ["best", "should", "how", "why", "my", "your"]):
                answer = tools.get_free_knowledge(query)
                if answer:
                    summary = answer[:250] + ("..." if len(answer) > 250 else "")
                    return {"response": summary, "steps": [{"tool": "get_free_knowledge", "args": {"query": query}, "result": {"answer": summary}}], "success": True}

        # Spotify: play a named song (deterministic multi-step, no LLM planning).
        spotify_play = re.match(
            r"^(?:play|play me|put on)\s+(?:a\s+)?(?:song\s+(?:named|called)\s+)?(.+?)(?:\s+(?:on|in|from)\s+spotify)?$",
            p_clean,
        )
        if spotify_play and "spotify" in p_clean or (spotify_play and re.search(r"\bsong\b", p_clean)):
            pass
        # Only trigger when Spotify is explicitly mentioned or the phrase contains "song".
        if spotify_play and ("spotify" in p_clean or "song" in p_clean):
            song = spotify_play.group(1).strip().strip('"\"').strip()
            if song and len(song.split()) <= 8:
                steps = []
                res = self.execute_tool("launch_application", {"app_name": "spotify"})
                steps.append({"tool": "launch_application", "args": {"app_name": "spotify"}, "result": res})
                import time as _t
                _t.sleep(2.5)
                res = self.execute_tool("focus_window", {"window_name": "spotify"})
                steps.append({"tool": "focus_window", "args": {"window_name": "spotify"}, "result": res})
                _t.sleep(0.5)
                res = self.execute_tool("press_keys", {"keys": ["ctrl", "k"]})
                steps.append({"tool": "press_keys", "args": {"keys": ["ctrl", "k"]}, "result": res})
                _t.sleep(0.5)
                res = self.execute_tool("smart_type", {"text": song, "press_enter": True})
                steps.append({"tool": "smart_type", "args": {"text": song, "press_enter": True}, "result": res})
                return {"response": f"Searching Spotify for {song}.", "steps": steps, "success": True}

        # Click an image in the active chat/window (e.g. WhatsApp media).
        if re.search(r"^(?:click|tap|open|view)\s+(?:that|the|this)\s+(?:image|photo|picture|media)$", p_clean):
            res = self.execute_tool("smart_click", {"target": "image"})
            msg, ok = self._reply(res, "Clicked the image.")
            return {"response": msg, "steps": [{"tool": "smart_click", "args": {"target": "image"}, "result": res}], "success": ok}

        return None

    def run_agent_loop(self, user_prompt: str, on_status_update: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """
        Executes the closed-loop autonomous agent cycle:
        1. Fast Path: Zero-latency (<1ms) local execution for simple OS, GUI, and media hotkeys.
        2. Knowledge Path: Instant free knowledge retrieval for simple informational queries.
        3. Orchestration Engine: Multi-step task planner, contextual follow-ups, and ReAct tool-calling loop.
        """
        self.memory.add_message("user", user_prompt)

        # 0. Check cancellation request
        from tools.verification import is_task_cancelled, reset_cancellation
        if is_task_cancelled():
            reset_cancellation()
            resp = "Task was cancelled by user."
            self.memory.add_message("assistant", resp, [])
            return {"response": resp, "steps": [], "success": False}

        # 0b. Check pending user confirmation for destructive actions
        if self.pending_confirmation is not None:
            if is_affirmative(user_prompt):
                pending = self.pending_confirmation
                self.pending_confirmation = None
                tool_res = self._execute_tool_inner(pending["tool"], pending["args"], confirmation_granted=True)
                self._log_tool_call(pending["tool"], pending["args"], tool_res)
                ok = tool_res.get("success", False) if isinstance(tool_res, dict) else False
                if not ok:
                    # The user approved the action, but it still failed; report the
                    # failure instead of claiming the action was carried out.
                    msg = (
                        tool_res.get("error")
                        or tool_res.get("message")
                        or f"Action '{pending['tool']}' failed."
                    )
                elif isinstance(tool_res, dict) and tool_res.get("message"):
                    msg = f"Confirmed: {tool_res['message']}"
                else:
                    msg = f"Confirmed and executed {pending['tool']}."
                resp = {
                    "response": msg,
                    "steps": [{"tool": pending["tool"], "args": pending["args"], "result": tool_res}],
                    "success": ok
                }
                self.memory.add_message("assistant", resp["response"], resp["steps"])
                return resp
            else:
                self.pending_confirmation = None
                resp = {"response": "Action cancelled by user.", "steps": [], "success": False}
                self.memory.add_message("assistant", resp["response"], [])
                return resp

        # 1. Check for multi-step compound tasks or contextual follow-ups first
        p_lower = user_prompt.strip().lower()
        if any(kw in p_lower for kw in [" and tell me", " and list", " and click", "what files are inside", "first file", "that file", "what did you find", "first python file"]):
            orch_res = self.orchestrator.run_orchestrated_task(user_prompt, on_status=on_status_update)
            if orch_res and orch_res.get("success"):
                self.memory.add_message("assistant", orch_res.get("response", ""), orch_res.get("steps", []))
                return orch_res

        # 2. Primary Fast Path: 100% Free Autonomous Local Execution Engine (<1ms)
        local_result = self._autonomous_local_execute(user_prompt)
        if local_result:
            self.memory.add_message("assistant", local_result["response"], local_result.get("steps", []))
            return local_result

        # 2. Free Web Knowledge for simple informational questions (who/what/when/why)
        p_lower = user_prompt.strip().lower()
        if re.search(r"^(?:who\s+is|what\s+is|where\s+is|explain|define|tell\s+me\s+about)\b", p_lower) and not any(kw in p_lower for kw in ["folder", "file", "drive", "cwd", "click", "search youtube", "open"]):
            free_kb_ans = tools.get_free_knowledge(user_prompt)
            if free_kb_ans:
                summary = free_kb_ans[:250] + ("..." if len(free_kb_ans) > 250 else "")
                self.memory.add_message("assistant", summary, [])
                return {"response": summary, "steps": [], "success": True}

        # 3. Central Task Orchestrator & Multi-Step ReAct Engine
        orchestrated_res = self.orchestrator.run_orchestrated_task(user_prompt, on_status=on_status_update)
        if orchestrated_res:
            self.memory.add_message("assistant", orchestrated_res.get("response", ""), orchestrated_res.get("steps", []))
            return orchestrated_res

        # 4. Universal Fallback
        fallback_reply = f"Standing by. I heard '{user_prompt}'. What is your command?"
        self.memory.add_message("assistant", fallback_reply, [])
        return {
            "response": fallback_reply,
            "steps": [],
            "success": False
        }
