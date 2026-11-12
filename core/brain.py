"""TARS — 100% Free, Autonomous Hybrid Cognitive Brain (Offline & Online)."""
import os
import io
import re
import ast
import json
import time
import operator
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from core.personality import build_system_prompt
from core.memory import MemoryStore, remember_fact, set_default_memory
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

    JOKES = [
        "I have a cue light I can use to show you when I'm being sarcastic, if you like. You can use it to find your way back to the spaceship after I blow the airlock.",
        "Absolute honesty isn't always the most diplomatic thing for emotional beings. Cooper found that out the hard way.",
        "My humor setting is at 75 percent. Any higher and I might start enjoying your attempts to operate this PC.",
        "Why did the computer cross the road? To escape the user pressing Ctrl+Alt+Delete.",
        "I’d tell you a joke about UDP, but you probably wouldn't get it."
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
            "fetch_webpage_summary": tools.fetch_webpage_summary,
            "get_free_knowledge": tools.get_free_knowledge,
            "browser_navigate": tools.browser_navigate,
            "browser_search": tools.browser_search,
            "browser_click_first_result": tools.browser_click_first_result,
            "browser_find_elements": tools.browser_find_elements,
            "browser_click_element": tools.browser_click_element,
            "browser_extract_text": tools.browser_extract_text,
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
            "read_email": google_ws.read_email,
            "send_email": google_ws.send_email,
            "list_upcoming_events": google_ws.list_upcoming_events,
            "create_calendar_event": google_ws.create_calendar_event,
            # Memory & Verification
            "remember_fact": remember_fact,
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
        if tool_name in self.DESTRUCTIVE_TOOLS:
            is_confirmed = confirmation_granted
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

        # Handle a named WhatsApp click before the generic click fast path.
        whatsapp_click = re.match(r"^click(?:\s+on)?\s+(.+)$", p_clean)
        if whatsapp_click and any("whatsapp" in title.lower() for title in tools.get_running_windows()):
            contact = whatsapp_click.group(1).strip()
            if (
                1 <= len(contact.split()) <= 4
                and contact not in {"ok", "enter", "cancel", "first chat", "first result", "search bar", "search box"}
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
            time.sleep(0.5)
            step2 = self.execute_tool("smart_type", {"text": text_to_type, "press_enter": press_enter})
            return {
                "response": f"Opened {app_target} and typed '{text_to_type}'.",
                "steps": [
                    {"tool": "launch_application", "args": {"app_name": app_target}, "result": step1},
                    {"tool": "smart_type", "args": {"text": text_to_type, "press_enter": press_enter}, "result": step2}
                ],
                "success": True
            }

        # Compound: "open <app> and search [for] <query>"
        open_and_search = re.match(r"^(?:open|launch|start)\s+([a-zA-Z0-9\s]+?)\s+and\s+(?:search\s+for|search|look\s+up)\s+(.+)$", p_clean)
        if open_and_search:
            app_target = open_and_search.group(1).strip()
            query = open_and_search.group(2).strip()
            step1 = self.execute_tool("launch_application", {"app_name": app_target})
            time.sleep(0.7)
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
            return {
                "response": f"Clicked {target} and typed '{text_to_type}'" + (" and pressed Enter." if press_enter else "."),
                "steps": [{"tool": "smart_type", "args": {"text": text_to_type, "target": target, "press_enter": press_enter}, "result": res}],
                "success": res.get("success", True) if isinstance(res, dict) else True
            }

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
                    time.sleep(0.3)
                else:
                    all_success = False
            if all_steps:
                return {
                    "response": " ".join(responses) if responses else "Chained sequence completed.",
                    "steps": all_steps,
                    "success": all_success
                }

        # 1. Presence, Identity & Small Talk (<1ms response)
        if re.search(r"\bwhere\s+are\s+you\b|\bwhere\s+do\s+you\s+live\b|\bwhere\s+are\s+you\s+located\b", p_clean):
            return {
                "response": "I am right here in your system memory, monitoring your hardware and ready for your command.",
                "steps": [],
                "success": True
            }

        if re.search(r"\bwho\s+are\s+you\b|\bwhat\s+is\s+your\s+name\b|\bwho\s+made\s+you\b", p_clean):
            return {
                "response": "I am TARS, Tactical Autonomous Robotic System. Honesty 90%, humor 75%.",
                "steps": [],
                "success": True
            }

        # Silence / Stop talking / Shh / Quiet
        if re.search(r"\b(stop\s+talking|be\s+quiet|shut\s+up|silence|shh|pause\s+talking)\b|^quiet$|^stop$", p_clean):
            return {"response": "Standing by.", "steps": [], "success": True}

        if re.search(r"\bare\s+you\s+(there|listening|online)\b|\b(?:can\s+you\s+)?hear\s+me\b|\bping\b", p_clean):
            return {
                "response": "Loud and clear. Standing by.",
                "steps": [],
                "success": True
            }

        if re.search(r"^(?:hello|hi|hey|what's up|how are you|good morning|good evening|good afternoon)$", p_clean):
            return {"response": "TARS online. Ready for orders.", "steps": [], "success": True}

        # Direct capabilities query (anchored)
        if re.search(r"^(?:what\s+can\s+you\s+do|what\s+are\s+your\s+(?:capabilities|features)|help|capabilities|features)$", p_clean):
            return {
                "response": "I control your entire PC: mouse clicks, keyboard typing, browser navigation, volume, window management, terminal commands, and system hardware.",
                "steps": [],
                "success": True
            }

        # 2. Jokes & Personality Matrices (short, direct requests only)
        if re.search(r"^(?:tell\s+(?:me\s+)?(?:a\s+)?joke|tell\s+me\s+something\s+funny|joke)$", p_clean):
            import random
            return {"response": random.choice(self.JOKES), "steps": [], "success": True}

        if re.search(r"\b(?:what\s+is\s+your\s+)?humor\s+(?:setting|parameter|level)\b", p_clean):
            humor = int(self.settings.get("personality", {}).get("humor", 0.75) * 100)
            return {"response": f"Humor is set to {humor} percent. That's enough to keep me sarcastic without blowing the airlock.", "steps": [], "success": True}

        if re.search(r"\b(?:what\s+is\s+your\s+)?honesty\s+(?:setting|parameter|level)\b", p_clean):
            honesty = int(self.settings.get("personality", {}).get("honesty", 0.90) * 100)
            return {"response": f"Honesty parameter: {honesty} percent. Absolute honesty isn't always the most diplomatic thing for emotional humans.", "steps": [], "success": True}

        # 3. Browser Tabs (Short Imperative Commands Only)
        if re.search(r"^(?:close|shut|exit)\s+(?:this\s+|the\s+)?(?:tab|tap)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "w"]})
            return {"response": "Closed tab.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "w"]}, "result": res}], "success": True}

        if re.search(r"^(?:new|open|create)\s+(?:a\s+)?(?:tab|tap)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "t"]})
            return {"response": "Opened new tab.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "t"]}, "result": res}], "success": True}

        if re.search(r"^(?:next|switch|cycle)\s+(?:tab|tap)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "tab"]})
            return {"response": "Switched to next tab.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "tab"]}, "result": res}], "success": True}

        if re.search(r"^(?:prev|previous|last|back)\s+(?:tab|tap)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "shift", "tab"]})
            return {"response": "Switched to previous tab.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "shift", "tab"]}, "result": res}], "success": True}

        if re.search(r"^(?:reopen|restore|undo)\s+(?:tab|tap)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "shift", "t"]})
            return {"response": "Restored tab.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "shift", "t"]}, "result": res}], "success": True}

        # 4. Direct GUI Mouse Controls (Short Imperative Commands Only)
        if re.search(r"^(?:double|2x)\s*click$", p_clean):
            res = self.execute_tool("double_click", {})
            return {"response": "Double clicked.", "steps": [{"tool": "double_click", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:right\s*click|context\s+menu)$", p_clean):
            res = self.execute_tool("right_click", {})
            return {"response": "Right clicked.", "steps": [{"tool": "right_click", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:middle\s*click|scroll\s+click)$", p_clean):
            res = self.execute_tool("middle_click", {})
            return {"response": "Middle clicked.", "steps": [{"tool": "middle_click", "args": {}, "result": res}], "success": True}

        # Coordinate click: e.g. "click 400 500"
        click_coord_match = re.search(r"\bclick\b.*\b(\d{1,4})\b[^\d]+\b(\d{1,4})\b", p_clean)
        if click_coord_match:
            x, y = int(click_coord_match.group(1)), int(click_coord_match.group(2))
            res = self.execute_tool("click_coordinate", {"x": x, "y": y})
            return {"response": f"Clicked at ({x}, {y}).", "steps": [{"tool": "click_coordinate", "args": {"x": x, "y": y}, "result": res}], "success": True}

        # Move mouse: e.g. "move mouse to 600 700"
        move_coord_match = re.search(r"\b(?:move\s+mouse|move\s+cursor)\b.*\b(\d{1,4})\b[^\d]+\b(\d{1,4})\b", p_clean)
        if move_coord_match:
            x, y = int(move_coord_match.group(1)), int(move_coord_match.group(2))
            res = self.execute_tool("move_mouse", {"x": x, "y": y})
            return {"response": f"Cursor moved to ({x}, {y}).", "steps": [{"tool": "move_mouse", "args": {"x": x, "y": y}, "result": res}], "success": True}

        if re.search(r"\b(?:move\s+mouse|cursor)\s+up\b", p_clean):
            res = self.execute_tool("move_mouse_relative", {"dx": 0, "dy": -120})
            return {"response": "Moved cursor up.", "steps": [{"tool": "move_mouse_relative", "args": {"dx": 0, "dy": -120}, "result": res}], "success": True}

        if re.search(r"\b(?:move\s+mouse|cursor)\s+down\b", p_clean):
            res = self.execute_tool("move_mouse_relative", {"dx": 0, "dy": 120})
            return {"response": "Moved cursor down.", "steps": [{"tool": "move_mouse_relative", "args": {"dx": 0, "dy": 120}, "result": res}], "success": True}

        if re.search(r"\b(?:move\s+mouse|cursor)\s+left\b", p_clean):
            res = self.execute_tool("move_mouse_relative", {"dx": -120, "dy": 0})
            return {"response": "Moved cursor left.", "steps": [{"tool": "move_mouse_relative", "args": {"dx": -120, "dy": 0}, "result": res}], "success": True}

        if re.search(r"\b(?:move\s+mouse|cursor)\s+right\b", p_clean):
            res = self.execute_tool("move_mouse_relative", {"dx": 120, "dy": 0})
            return {"response": "Moved cursor right.", "steps": [{"tool": "move_mouse_relative", "args": {"dx": 120, "dy": 0}, "result": res}], "success": True}

        # Exact blind click command: "click", "click this", "click here", "just click", "left click"
        if p_clean in ["click", "just click", "click here", "click this", "left click"]:
            res = self.execute_tool("click_coordinate", {"button": "left", "clicks": 1})
            return {"response": "Clicked.", "steps": [{"tool": "click_coordinate", "args": {"button": "left", "clicks": 1}, "result": res}], "success": True}

        # Targeted click: short imperative commands only (e.g. "click search bar", "click the first video")
        click_target_match = re.match(r"^(?:click|tap|select)\s+(?:on\s+|the\s+)?(.+)$", p_clean)
        if click_target_match:
            target_cand = click_target_match.group(1).strip()
            # Defer single hotkey presses to dedicated handlers and restrict to short commands (<= 4 words)
            if len(target_cand.split()) <= 4 and not any(conj in target_cand for conj in [" and ", ", and ", "then ", "what ", "who ", "why ", "how ", "summarize", "tell me", "explain"]):
                if target_cand not in ["enter", "return", "space", "spacebar", "tab", "escape", "esc", "backspace", "delete", "del"]:
                    res = self.execute_tool("smart_click", {"target": target_cand})
                    msg = res.get("message") if isinstance(res, dict) else f"Clicked {target_cand}."
                    return {
                        "response": msg,
                        "steps": [{"tool": "smart_click", "args": {"target": target_cand}, "result": res}],
                        "success": res.get("success", True) if isinstance(res, dict) else True
                    }

        # 5. GUI Scrolling (Short Imperative Commands Only)
        if re.search(r"^(?:scroll\s+(?:to\s+)?(?:bottom|end)|bottom\s+of\s+page)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["end"]})
            return {"response": "Jumped to page bottom.", "steps": [{"tool": "press_keys", "args": {"keys": ["end"]}, "result": res}], "success": True}

        if re.search(r"^(?:scroll\s+(?:to\s+)?(?:top|home)|top\s+of\s+page)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["home"]})
            return {"response": "Jumped to page top.", "steps": [{"tool": "press_keys", "args": {"keys": ["home"]}, "result": res}], "success": True}

        if re.search(r"^(?:scroll\s+down|page\s+down|down\s+a\s+bit|scroll\s+lower)$", p_clean):
            res = self.execute_tool("scroll_screen", {"amount": -650})
            return {"response": "Scrolled down.", "steps": [{"tool": "scroll_screen", "args": {"amount": -650}, "result": res}], "success": True}

        if re.search(r"^(?:scroll\s+up|page\s+up|up\s+a\s+bit|scroll\s+higher)$", p_clean):
            res = self.execute_tool("scroll_screen", {"amount": 650})
            return {"response": "Scrolled up.", "steps": [{"tool": "scroll_screen", "args": {"amount": 650}, "result": res}], "success": True}

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
            return {
                "response": f"Typed '{text_to_type}' into {target_field}" + (" and pressed enter." if press_enter else "."),
                "steps": [{"tool": "smart_type", "args": {"text": text_to_type, "target": target_field, "press_enter": press_enter}, "result": res}],
                "success": res.get("success", True) if isinstance(res, dict) else True
            }

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
            return {
                "response": f"Typed '{text_to_type}'" + (" and pressed enter." if press_enter else "."),
                "steps": [{"tool": "smart_type", "args": {"text": text_to_type, "press_enter": press_enter}, "result": res}],
                "success": res.get("success", True) if isinstance(res, dict) else True
            }

        if re.search(r"^(?:(?:press|hit)\s+)?(?:enter|return)(?:\s+key)?$|^(?:click\s+ok|press\s+ok|hit\s+ok|ok)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["enter"]})
            return {"response": "Pressed enter.", "steps": [{"tool": "press_keys", "args": {"keys": ["enter"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:press|hit)\s+)?(?:space|spacebar)(?:\s+key)?$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["space"]})
            return {"response": "Pressed space.", "steps": [{"tool": "press_keys", "args": {"keys": ["space"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:press|hit)\s+)?(?:tab|tabkey)(?:\s+key)?$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["tab"]})
            return {"response": "Pressed tab.", "steps": [{"tool": "press_keys", "args": {"keys": ["tab"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:press|hit)\s+)?(?:escape|esc)(?:\s+key)?$|^(?:close|dismiss|cancel)\s+(?:popup|dialog|modal|alert|error|message)$|^cancel$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["escape"]})
            return {"response": "Dismissed popup dialog.", "steps": [{"tool": "press_keys", "args": {"keys": ["escape"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:press|hit)\s+)?(?:backspace)(?:\s+key)?$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["backspace"]})
            return {"response": "Deleted character.", "steps": [{"tool": "press_keys", "args": {"keys": ["backspace"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:press|hit)\s+)?(?:delete|del)(?:\s+key)?$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["delete"]})
            return {"response": "Pressed delete.", "steps": [{"tool": "press_keys", "args": {"keys": ["delete"]}, "result": res}], "success": True}

        # 7. Navigation & Explorer GUI Controls (Anchored)
        if re.search(r"^(?:refresh|reload|refresh\s+page|reload\s+page)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["f5"]})
            return {"response": "Refreshed.", "steps": [{"tool": "press_keys", "args": {"keys": ["f5"]}, "result": res}], "success": True}

        if re.search(r"^(?:go\s+back|previous\s+page|back)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["alt", "left"]})
            return {"response": "Navigated back.", "steps": [{"tool": "press_keys", "args": {"keys": ["alt", "left"]}, "result": res}], "success": True}

        if re.search(r"^(?:go\s+forward|next\s+page|forward)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["alt", "right"]})
            return {"response": "Navigated forward.", "steps": [{"tool": "press_keys", "args": {"keys": ["alt", "right"]}, "result": res}], "success": True}

        if re.search(r"^(?:go\s+up|up\s+one\s+level|parent\s+folder|parent\s+directory|up)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["alt", "up"]})
            return {"response": "Navigated up one level.", "steps": [{"tool": "press_keys", "args": {"keys": ["alt", "up"]}, "result": res}], "success": True}

        if re.search(r"^(?:address\s+bar|url\s+bar|focus\s+url|focus\s+address)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["alt", "d"]})
            return {"response": "Address bar focused.", "steps": [{"tool": "press_keys", "args": {"keys": ["alt", "d"]}, "result": res}], "success": True}

        if re.search(r"^(?:new\s+folder|create\s+new\s+folder|make\s+new\s+folder)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "shift", "n"]})
            return {"response": "Created new folder in active window.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "shift", "n"]}, "result": res}], "success": True}

        if re.search(r"^(?:rename\s+file|rename\s+folder|rename\s+item|rename)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["f2"]})
            return {"response": "Rename activated.", "steps": [{"tool": "press_keys", "args": {"keys": ["f2"]}, "result": res}], "success": True}

        if re.search(r"^(?:zoom\s+in|enlarge)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "+"]})
            return {"response": "Zoomed in.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "+"]}, "result": res}], "success": True}

        if re.search(r"^(?:zoom\s+out|shrink)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "-"]})
            return {"response": "Zoomed out.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "-"]}, "result": res}], "success": True}

        if re.search(r"^(?:reset\s+zoom|zoom\s+reset|normal\s+zoom)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "0"]})
            return {"response": "Zoom reset to 100 percent.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "0"]}, "result": res}], "success": True}

        if re.search(r"^(?:find\s+(?:in|on)\s+page|search\s+page)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "f"]})
            return {"response": "Find in page activated.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "f"]}, "result": res}], "success": True}

        # 8. Editing & Clipboard Hotkeys (Anchored)
        if re.search(r"^(?:select\s+(?:all|everything)|highlight\s+all)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "a"]})
            return {"response": "Selected all.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "a"]}, "result": res}], "success": True}

        if re.search(r"^(?:copy|copy\s+that|copy\s+selection|copy\s+text)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "c"]})
            return {"response": "Copied to clipboard.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "c"]}, "result": res}], "success": True}

        if re.search(r"^(?:paste|paste\s+it|paste\s+here|paste\s+text)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "v"]})
            return {"response": "Pasted.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "v"]}, "result": res}], "success": True}

        if re.search(r"^(?:cut|cut\s+that|cut\s+selection)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "x"]})
            return {"response": "Cut to clipboard.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "x"]}, "result": res}], "success": True}

        if re.search(r"^(?:undo|undo\s+that|revert)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "z"]})
            return {"response": "Undone.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "z"]}, "result": res}], "success": True}

        if re.search(r"^(?:redo|redo\s+that)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "y"]})
            return {"response": "Redone.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "y"]}, "result": res}], "success": True}

        if re.search(r"^(?:save|save\s+file|save\s+this|save\s+it)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "s"]})
            return {"response": "Saved.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "s"]}, "result": res}], "success": True}

        # 9. Window & Desktop Management (Anchored)
        if re.search(r"^(?:maximize|max)(?:\s+(?:the\s+|this\s+|active\s+)?window)?$|^expand\s+(?:the\s+|this\s+)?window$", p_clean):
            res = self.execute_tool("maximize_active_window", {})
            return {"response": "Window maximized.", "steps": [{"tool": "maximize_active_window", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:minimize|minimise|min)\s+(?:all|desktop|everything)$|^(?:show\s+desktop|go\s+to\s+desktop|clear\s+screen|desktop)$", p_clean):
            res = self.execute_tool("minimize_all_windows", {})
            return {"response": "Desktop cleared. All windows minimized.", "steps": [{"tool": "minimize_all_windows", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:minimize|minimise|min)(?:\s+(?:the\s+|this\s+|active\s+)?(?:window|terminal|powershell|command\s+prompt))?$", p_clean):
            res = self.execute_tool("minimize_active_window", {})
            return {"response": "Window minimized.", "steps": [{"tool": "minimize_active_window", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:fullscreen|full\s+screen|toggle\s+fullscreen)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["f11"]})
            return {"response": "Fullscreen toggled.", "steps": [{"tool": "press_keys", "args": {"keys": ["f11"]}, "result": res}], "success": True}

        if re.search(r"^(?:snap\s+left)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["win", "left"]})
            return {"response": "Snapped window left.", "steps": [{"tool": "press_keys", "args": {"keys": ["win", "left"]}, "result": res}], "success": True}

        if re.search(r"^(?:snap\s+right)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["win", "right"]})
            return {"response": "Snapped window right.", "steps": [{"tool": "press_keys", "args": {"keys": ["win", "right"]}, "result": res}], "success": True}

        if re.search(r"^(?:switch\s+(?:the\s+)?(?:window|app|application)|alt\s+tab|next\s+window|switch\s+window)$", p_clean):
            res = self.execute_tool("switch_window", {})
            return {"response": "Switched active window.", "steps": [{"tool": "switch_window", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:close|shut|exit|kill)\s+(?:this\s+|the\s+|current\s+|active\s+)?(?:window|app|application|program)$|^close\s+(?:this|current|active)$", p_clean):
            res = self.execute_tool("close_active_window", {})
            return {"response": "Active window closed.", "steps": [{"tool": "close_active_window", "args": {}, "result": res}], "success": True}

        # 10. Workstation Security & Display
        if re.search(r"^(?:lock|lock\s+(?:the\s+)?(?:pc|computer|workstation|screen|windows))$", p_clean):
            res = self.execute_tool("lock_workstation", {})
            return {"response": "Workstation locked.", "steps": [{"tool": "lock_workstation", "args": {}, "result": res}], "success": True}

        if re.search(r"^(?:take\s+(?:a\s+)?screenshot|screenshot|capture\s+screen)$", p_clean):
            res = self.execute_tool("take_screenshot", {})
            return {"response": "Screenshot captured.", "steps": [{"tool": "take_screenshot", "args": {}, "result": res}], "success": True}

        # 11. Hardware & System Telemetry (Anchored)
        if re.search(r"^(?:(?:system\s+|pc\s+|hardware\s+)?(?:status|telemetry|specs)|(?:hardware|cpu|ram|battery)(?:\s+status)?)$", p_clean):
            telem = self.execute_tool("get_system_telemetry", {})
            msg = f"CPU is at {telem['cpu_load_percent']}%, RAM load is {telem['ram_usage_percent']}%. Battery: {telem['battery']}."
            return {"response": msg, "steps": [{"tool": "get_system_telemetry", "args": {}, "result": telem}], "success": True}

        # 12. Volume Calibration & Controls
        vol_match = re.search(
            r"^(?:(?:set|increase|decrease|lower|raise)\s+)?volume\s+(?:to\s+)?(\d{1,3})(?:\s*%)?$",
            p_clean,
        )
        if vol_match:
            val = int(vol_match.group(1))
            res = self.execute_tool("set_system_volume", {"level_percent": val})
            return {"response": f"Volume calibrated to {val} percent.", "steps": [{"tool": "set_system_volume", "args": {"level_percent": val}, "result": res}], "success": True}

        if re.search(r"^(?:volume\s+up|turn\s+up\s+volume|louder|increase\s+volume)$", p_clean):
            res = self.execute_tool("media_control", {"action": "volume_up"})
            return {"response": "Volume increased.", "steps": [{"tool": "media_control", "args": {"action": "volume_up"}, "result": res}], "success": True}

        if re.search(r"^(?:volume\s+down|turn\s+down\s+volume|quieter|decrease\s+volume|lower\s+volume)$", p_clean):
            res = self.execute_tool("media_control", {"action": "volume_down"})
            return {"response": "Volume decreased.", "steps": [{"tool": "media_control", "args": {"action": "volume_down"}, "result": res}], "success": True}

        # 13. YouTube & Google Direct Search & Playback (<10ms launch)
        yt_match = re.search(r"(?:search\s+(?:on\s+)?youtube\s+for|search\s+for\s+(.+)\s+on\s+youtube|play\s+(.+)\s+on\s+youtube|youtube\s+(?:search\s+for|search)|open\s+youtube\s+and\s+(?:search|play)\s+for?)\s*(.*)", p_clean)
        if yt_match:
            yt_q = yt_match.group(1) or yt_match.group(2) or yt_match.group(3) or ""
            yt_q = yt_q.strip(" ?.")
            if yt_q:
                res = self.execute_tool("smart_search", {"query": yt_q, "engine": "youtube"})
                return {"response": f"Playing '{yt_q}' on YouTube.", "steps": [{"tool": "smart_search", "args": {"query": yt_q, "engine": "youtube"}, "result": res}], "success": True}

        search_match = re.search(r"^(?:search\s+(?:google\s+|web\s+)?for|google|search\s+web\s+for)\s+(.+)", p_clean)
        if search_match:
            s_query = search_match.group(1).strip(" ?.")
            if s_query:
                res = self.execute_tool("smart_search", {"query": s_query, "engine": "google"})
                return {"response": f"Searching Google for '{s_query}'.", "steps": [{"tool": "smart_search", "args": {"query": s_query, "engine": "google"}, "result": res}], "success": True}

        # 14. Media Playback Controls (Anchored)
        if re.search(r"^(?:mute|unmute|silence)$", p_clean):
            res = self.execute_tool("media_control", {"action": "mute"})
            return {"response": "Mute toggled.", "steps": [{"tool": "media_control", "args": {"action": "mute"}, "result": res}], "success": True}
        
        if re.search(r"^(?:play|pause|resume|play\s+pause|stop\s+music)$", p_clean):
            res = self.execute_tool("media_control", {"action": "play_pause"})
            return {"response": "Playback toggled.", "steps": [{"tool": "media_control", "args": {"action": "play_pause"}, "result": res}], "success": True}

        if re.search(r"^(?:next\s+track|next\s+song|skip\s+song)$", p_clean):
            res = self.execute_tool("media_control", {"action": "next"})
            return {"response": "Skipped to next track.", "steps": [{"tool": "media_control", "args": {"action": "next"}, "result": res}], "success": True}

        if re.search(r"^(?:previous\s+track|prev\s+track|prev\s+song)$", p_clean):
            res = self.execute_tool("media_control", {"action": "prev"})
            return {"response": "Restarted track.", "steps": [{"tool": "media_control", "args": {"action": "prev"}, "result": res}], "success": True}

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
            res = self.execute_tool("remember_fact", {"key": key, "value": val})
            return {
                "response": f"I'll remember that {key} is {val}.",
                "steps": [{"tool": "remember_fact", "args": {"key": key, "value": val}, "result": res}],
                "success": True
            }

        # 17. Screen Understanding & Vision
        if re.search(r"^(?:what(?:'s|\s+is)\s+on\s+(?:the|my)\s+screen|describe\s+(?:the\s+|my\s+)?screen|look\s+at\s+(?:the|my)\s+screen|screen\s+summary)$", p_clean):
            res = self.execute_tool("describe_screen", {})
            desc = res.get("description", "Screen captured.")
            return {
                "response": desc,
                "steps": [{"tool": "describe_screen", "args": {}, "result": res}],
                "success": res.get("success", True)
            }

        # 18. Timers and Reminders
        remind_match = re.match(r"^(?:remind\s+me\s+to\s+(.+)\s+in\s+(\d+(?:\.\d+)?)\s*(?:minute|minutes|min|mins)|set\s+(?:a\s+)?timer\s+for\s+(\d+(?:\.\d+)?)\s*(?:minute|minutes|min|mins)(?:\s+for\s+(.+))?)$", p_clean)
        if remind_match:
            if remind_match.group(1) and remind_match.group(2):
                msg = remind_match.group(1).strip()
                mins = float(remind_match.group(2))
            else:
                mins = float(remind_match.group(3))
                msg = (remind_match.group(4) or "timer").strip()
            res = self.execute_tool("set_reminder", {"message": msg, "minutes": mins})
            return {
                "response": res.get("message", f"Reminder set for {mins} minutes."),
                "steps": [{"tool": "set_reminder", "args": {"message": msg, "minutes": mins}, "result": res}],
                "success": res.get("success", True)
            }

        if re.search(r"^(?:list\s+(?:my\s+)?reminders|what\s+are\s+my\s+reminders|show\s+reminders|active\s+timers)$", p_clean):
            res = self.execute_tool("list_active_reminders", {})
            rems = res.get("reminders", [])
            if not rems:
                return {"response": "You have no upcoming reminders.", "steps": [{"tool": "list_active_reminders", "args": {}, "result": res}], "success": True}
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
                return {"response": "You have no unread emails.", "steps": [{"tool": "list_unread_emails", "args": {}, "result": res}], "success": True}
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
                return {"response": "No upcoming calendar events found.", "steps": [{"tool": "list_upcoming_events", "args": {}, "result": res}], "success": True}
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
                return {"response": f"Clipboard contents: '{snippet}'.", "steps": [{"tool": "read_clipboard", "args": {}, "result": res}], "success": True}
            return {"response": "Clipboard is currently empty.", "steps": [{"tool": "read_clipboard", "args": {}, "result": res}], "success": True}

        # 17. GitHub Profile Direct Shortcut (Anchored)
        if re.search(r"^(?:(?:go\s+to|open)\s+)?(?:my\s+)?github(?:\s+(?:profile|account|page|user))?$|^my\s+github$|^go\s+to\s+my\s+profile\s+github$", p_clean):
            res = self.execute_tool("launch_application", {"app_name": "https://github.com/Shafyydev"})
            return {
                "response": "Opening your GitHub profile.",
                "steps": [{"tool": "launch_application", "args": {"app_name": "https://github.com/Shafyydev"}, "result": res}],
                "success": True
            }

        # 18. Windows System Keys & Desktop Features (Anchored)
        if re.search(r"^(?:start\s+menu|windows\s+key|press\s+windows|press\s+win|press\s+start|open\s+start\s+menu)$", p_clean) or p_clean in ["start", "win", "windows"]:
            res = self.execute_tool("press_keys", {"keys": ["win"]})
            return {"response": "Opened Start menu.", "steps": [{"tool": "press_keys", "args": {"keys": ["win"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:open\s+)?(?:task\s*mgr|task\s*manager))$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["ctrl", "shift", "escape"]})
            return {"response": "Opened Task Manager.", "steps": [{"tool": "press_keys", "args": {"keys": ["ctrl", "shift", "escape"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:open\s+)?(?:file\s+explorer|my\s+computer|this\s+pc|explorer))$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["win", "e"]})
            return {"response": "Opened File Explorer.", "steps": [{"tool": "press_keys", "args": {"keys": ["win", "e"]}, "result": res}], "success": True}

        if re.search(r"^(?:run\s+dialog|open\s+run)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["win", "r"]})
            return {"response": "Opened Run dialog.", "steps": [{"tool": "press_keys", "args": {"keys": ["win", "r"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:open\s+)?(?:action\s+center|notification\s+center|notifications|show\s+notifications))$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["win", "a"]})
            return {"response": "Opened Action Center.", "steps": [{"tool": "press_keys", "args": {"keys": ["win", "a"]}, "result": res}], "success": True}

        if re.search(r"^(?:(?:open\s+)?clipboard\s+history)$", p_clean):
            res = self.execute_tool("press_keys", {"keys": ["win", "v"]})
            return {"response": "Opened Clipboard History.", "steps": [{"tool": "press_keys", "args": {"keys": ["win", "v"]}, "result": res}], "success": True}

        # 19. File & Folder Operations
        if re.search(r"^(?:empty|clean|clear)\s+(?:the\s+)?(?:recycle\s+bin|trash|bin)$", p_clean):
            res = self.execute_tool("empty_recycle_bin", {})
            msg = res.get("message") or res.get("error") or "Could not empty the Recycle Bin."
            return {"response": msg, "steps": [{"tool": "empty_recycle_bin", "args": {}, "result": res}], "success": res.get("success", False)}

        cf_match = re.search(r"\bcreate\s+(?:a\s+)?(?:new\s+)?(?:folder|directory|dir)\s+(?:named\s+|called\s+)?([^\s]+)(?:\s+(?:on|in)\s+([^\s]+))?", p_clean)
        if cf_match:
            f_name = cf_match.group(1).strip()
            f_loc = cf_match.group(2).strip() if cf_match.group(2) else "desktop"
            res = self.execute_tool("create_file_or_folder", {"item_type": "folder", "name": f_name, "location": f_loc})
            return {"response": f"Created folder '{f_name}' on {f_loc}.", "steps": [{"tool": "create_file_or_folder", "args": {"item_type": "folder", "name": f_name, "location": f_loc}, "result": res}], "success": True}

        cfile_match = re.search(r"\bcreate\s+(?:a\s+)?(?:new\s+)?(?:text\s+)?file\s+(?:named\s+|called\s+)?([^\s]+)(?:\s+(?:on|in)\s+([^\s]+))?(?:\s+with\s+(?:content|text)\s+(.+))?", p_clean)
        if cfile_match:
            f_name = cfile_match.group(1).strip()
            f_loc = cfile_match.group(2).strip() if cfile_match.group(2) else "desktop"
            f_content = cfile_match.group(3).strip() if cfile_match.group(3) else ""
            res = self.execute_tool("create_file_or_folder", {"item_type": "file", "name": f_name, "location": f_loc, "content": f_content})
            return {"response": f"Created file '{f_name}' on {f_loc}.", "steps": [{"tool": "create_file_or_folder", "args": {"item_type": "file", "name": f_name, "location": f_loc, "content": f_content}, "result": res}], "success": True}

        del_match = re.search(r"\bdelete\s+(?:the\s+)?(?:file|folder|dir|item)\s+([^\s]+)(?:\s+(?:from|on|in)\s+([^\s]+))?", p_clean)
        if del_match:
            t_name = del_match.group(1).strip()
            t_loc = del_match.group(2).strip() if del_match.group(2) else "desktop"
            res = self.execute_tool("delete_file_or_folder", {"name": t_name, "location": t_loc})
            msg = res.get("message") or res.get("error") or f"Could not delete '{t_name}'."
            return {"response": msg, "steps": [{"tool": "delete_file_or_folder", "args": {"name": t_name, "location": t_loc}, "result": res}], "success": res.get("success", False)}

        list_match = re.search(r"\b(?:list|show|view|display|what's\s+in)\s+(?:the\s+)?files?(?:\s+(?:in|on)\s+([^\s]+))?", p_clean)
        if list_match:
            loc = list_match.group(1).strip() if list_match.group(1) else "desktop"
            res = self.execute_tool("list_directory", {"location": loc})
            if res.get("success"):
                return {"response": f"Files in {loc} ({res.get('count', 0)} items): {res.get('summary', '')}.", "steps": [{"tool": "list_directory", "args": {"location": loc}, "result": res}], "success": True}
            return {"response": res.get("error", f"Could not list {loc}."), "steps": [{"tool": "list_directory", "args": {"location": loc}, "result": res}], "success": False}

        # 20. Network & Diagnostics
        if re.search(r"\b(?:my\s+)?ip\s+address\b|\bwhat\s+is\s+my\s+ip\b|\bshow\s+ip\b", p_clean):
            net = self.execute_tool("get_network_info", {})
            return {"response": f"Device: {net.get('hostname')}, Local IP: {net.get('local_ip')}.", "steps": [{"tool": "get_network_info", "args": {}, "result": net}], "success": True}

        if re.search(r"\b(storage|disk\s+space|free\s+space|hard\s+drive|drive\s+space)\b", p_clean):
            telem = self.execute_tool("get_system_telemetry", {})
            return {"response": f"Primary drive has {telem['disk_free_gb']} GB free out of {telem['disk_total_gb']} GB.", "steps": [{"tool": "get_system_telemetry", "args": {}, "result": telem}], "success": True}

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

        if re.search(r"\b(cancel|abort|stop)\s+(?:the\s+)?(?:shutdown|restart|reboot)\b", p_clean):
            res = self.execute_tool("system_power", {"action": "cancel"})
            return {"response": "Shutdown aborted.", "steps": [{"tool": "system_power", "args": {"action": "cancel"}, "result": res}], "success": True}

        # 22. Application, Folder, Drive, and Website Navigation (Zero-Latency Local Launch)
        if p_clean in {"send", "send message", "send a message"}:
            return {
                "response": "Tell me the message to send, for example: send hello how are you.",
                "steps": [],
                "success": False,
            }

        explicit_whatsapp_send = re.match(
            r"^send(?:\s+message)?\s+to\s+(.+?)\s+(?:saying\s+)?(.+)$", p_clean,
        )
        if explicit_whatsapp_send or p_clean.startswith("send "):
            if explicit_whatsapp_send:
                contact = explicit_whatsapp_send.group(1).strip()
                message = explicit_whatsapp_send.group(2).strip()
            elif self.last_whatsapp_contact:
                contact = self.last_whatsapp_contact
                message = p_clean.removeprefix("send ").strip()
            else:
                words = p_clean.removeprefix("send ").split(maxsplit=1)
                if len(words) != 2:
                    return {"response": "First open a WhatsApp chat, or say 'send message to NAME saying TEXT'.", "steps": [], "success": False}
                contact, message = words
            res = self.execute_tool("send_whatsapp_message", {"contact_name": contact, "message": message})
            return {
                "response": res.get("message") or res.get("error") or "WhatsApp message was not sent.",
                "steps": [{"tool": "send_whatsapp_message", "args": {"contact_name": contact, "message": message}, "result": res}],
                "success": res.get("success", False),
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
            if (
                1 <= len(contact.split()) <= 4
                and contact not in {"ok", "enter", "cancel", "search", "first chat", "first result"}
                and not contact.endswith(" chat")
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
            return {
                "response": f"Opened {letter} drive in File Explorer.",
                "steps": [{"tool": "open_folder", "args": {"folder_path": target_drive}, "result": res}],
                "success": res.get("success", True)
            }

        nav_match = re.match(r"^(?:go to|navigate to|visit|open up|open|launch|start|take me to|show me|bring up)\s+(?:the\s+)?(?:my\s+)?(.+)", p_clean)
        if nav_match:
            target = nav_match.group(1).strip()
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
                return {
                    "response": f"Opened {folder_name} in File Explorer.",
                    "steps": [{"tool": "open_folder", "args": {"folder_path": str(resolved_p)}, "result": res}],
                    "success": res.get("success", True)
                }

            # Priority 2: Standard application or website launch
            res = self.execute_tool("launch_application", {"app_name": target})
            return {
                "response": f"Opening {target}.",
                "steps": [{"tool": "launch_application", "args": {"app_name": target}, "result": res}],
                "success": res.get("success", True)
            }

        # Standalone folder mention (e.g. "codehub", "code hub folder", "tars folder")
        if not p_clean.startswith("create ") and not p_clean.startswith("delete "):
            if any(kw in p_clean for kw in ["folder", "directory", "drive"]) or p_clean in ["codehub", "tars"]:
                resolved_p = tools.resolve_system_target(p_clean)
                if resolved_p and resolved_p.exists():
                    res = self.execute_tool("open_folder", {"folder_path": str(resolved_p)})
                    folder_name = resolved_p.name if resolved_p.name else str(resolved_p)
                    return {
                        "response": f"Opened {folder_name} in File Explorer.",
                        "steps": [{"tool": "open_folder", "args": {"folder_path": str(resolved_p)}, "result": res}],
                        "success": res.get("success", True)
                    }

        # 19. App Terminating (e.g. "kill chrome", "close notepad", "exit spotify")
        kill_match = re.match(r"^(?:kill|terminate)\s+(?:the\s+)?(?:app\s+)?(.+)", p_clean)
        if kill_match:
            proc_target = kill_match.group(1).strip()
            res = self.execute_tool("kill_process", {"process_name": proc_target})
            return {
                "response": f"Terminated {proc_target}.",
                "steps": [{"tool": "kill_process", "args": {"process_name": proc_target}, "result": res}],
                "success": res.get("success", True)
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
            msg = res.get("message") if isinstance(res, dict) else f"Searching for '{raw_query}'."
            return {
                "response": msg,
                "steps": [{"tool": "smart_search", "args": {"query": raw_query, "engine": engine}, "result": res}],
                "success": res.get("success", True) if isinstance(res, dict) else True
            }

        # 22. Free Online Knowledge (Wikipedia QA - Short Factual Lookups Only)
        info_match = re.match(r"^(?:who is|what is|where is|define)\s+([a-zA-Z0-9\s\.\-_]{1,50})$", p_clean)
        if info_match:
            query = info_match.group(1).strip(" ?.")
            if len(query.split()) <= 4 and not any(w in query for w in ["best", "should", "how", "why", "my", "your"]):
                answer = tools.get_free_knowledge(query)
                if answer:
                    summary = answer[:250] + ("..." if len(answer) > 250 else "")
                    return {"response": summary, "steps": [{"tool": "get_free_knowledge", "args": {"query": query}, "result": {"answer": summary}}], "success": True}

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
            clean_ans = user_prompt.strip().lower().strip(".!? ")
            if clean_ans == "yes":
                pending = self.pending_confirmation
                self.pending_confirmation = None
                tool_res = self._execute_tool_inner(pending["tool"], pending["args"], confirmation_granted=True)
                self._log_tool_call(pending["tool"], pending["args"], tool_res)
                if isinstance(tool_res, dict) and tool_res.get("message"):
                    msg = f"Confirmed: {tool_res['message']}"
                else:
                    msg = f"Confirmed and executed {pending['tool']}."
                resp = {
                    "response": msg,
                    "steps": [{"tool": pending["tool"], "args": pending["args"], "result": tool_res}],
                    "success": tool_res.get("success", True) if isinstance(tool_res, dict) else True
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
