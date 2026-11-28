"""Central task orchestrator, multi-step planner, and closed-loop execution engine for TARS."""
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from core.task_state import TaskState, TaskStep
from core.llm_provider import LLMProvider
from core.personality import build_runtime_context
from tools.verification import is_task_cancelled, reset_cancellation


class TaskOrchestrator:
    """
    Orchestrates complex multi-step Windows, browser, and filesystem tasks.
    Maintains task state, contextual memory for follow-up commands,
    bounded error recovery, and verification loops.
    """

    # Verbs that turn a request into an action instead of a question.  Used to
    # refuse reporting success when the model answered without running a tool.
    ACTION_VERBS = frozenset({
        "open", "close", "maximize", "maximise", "minimize", "minimise", "launch",
        "start", "stop", "kill", "terminate", "search", "type", "click", "press",
        "send", "create", "delete", "remove", "run", "shutdown", "restart", "reboot",
        "lock", "focus", "switch", "scroll", "set", "change", "play", "pause",
        "mute", "fullscreen", "snap", "minimize", "expand", "empty", "install",
        "uninstall", "write", "read", "move", "copy", "rename", "format", "sleep",
    })

    @classmethod
    def _is_question(cls, request: str) -> bool:
        """True when the request is a question about prior actions/state, not a new action."""
        text = (request or "").strip().lower()
        if text.endswith("?"):
            return True
        return any(
            marker in text
            for marker in (
                "what ", "which ", "how ", "did you", "did i", "have you",
                "tell me", "show me", "remind me", "what did", "what was",
            )
        )

    @classmethod
    def _is_action_request(cls, request: str) -> bool:
        """True when the request starts with a verb that must actually do something."""
        text = (request or "").strip().lower()
        prefix_re = re.compile(
            r"^(?:please|can\s+you|could\s+you|would\s+you|hey\s+tars|tars|"
            r"ok|okay|now|yes|yeah|sure|just|go\s+ahead(?:\s+and)?)\s+"
        )
        for _ in range(4):
            updated = prefix_re.sub("", text, count=1).strip()
            if updated == text:
                break
            text = updated
        first = text.split()[0] if text else ""
        return first in cls.ACTION_VERBS

    # Per-request budget.  A Gemini flash call that has not answered in 15s is a
    # dead request (504 DEADLINE_EXCEEDED) rather than slow thinking, so a long
    # budget only adds dead air before the failover kicks in.
    LLM_TIMEOUTS = {"nvidia": 10.0, "groq": 10.0}

    @classmethod
    def _llm_timeout(cls, provider: Optional[str]) -> float:
        return cls.LLM_TIMEOUTS.get((provider or "").lower(), 20.0)

    def _honest_llm_result(self, llm_result: Dict[str, Any], request: str) -> Dict[str, Any]:
        """
        Rewrites a model reply that ran no tools.

        A question can be answered without tools, but an action request (or a reply
        falsely claiming completion) with zero executed steps means nothing happened,
        so the result must not claim success.
        """
        if self._is_question(request):
            return llm_result
        if not llm_result.get("no_tool_calls"):
            return llm_result
        if llm_result.get("steps"):
            return llm_result

        text = (llm_result.get("response") or "").strip()
        lower = text.lower()
        # Strip benign trailing sign-off questions ("Anything else?", "Need anything else?")
        # so a hallucinated completion like "Message sent to Reshmaa. Anything else?"
        # is never misclassified as a clarification question.
        core_lower = re.sub(
            r"(?:\s*(?:anything else|need anything else|what else|shall i do anything else)\??)+$",
            "",
            lower,
        ).strip()
        is_question_or_prompt = core_lower.endswith("?") or any(
            marker in core_lower
            for marker in ("would you like", "should i", "do you want", "please confirm", "say yes")
        )
        claims_done = not is_question_or_prompt and any(
            re.search(rf"\b{word}\b", lower)
            for word in (
                "done", "opened", "closed", "sent", "created", "deleted", "completed",
                "maximized", "minimized", "launched", "typed", "pressed", "terminated",
                "shutting down", "searching", "opening", "closing",
            )
        )
        # Narrating an action in the present tense is the same lie as claiming it
        # finished: "Searching for 'ttf' on YouTube now..." with zero tool calls.
        claims_progress = not is_question_or_prompt and any(
            re.search(rf"\b{re.escape(word)}\b", lower)
            for word in (
                "searching", "opening", "launching", "clicking", "typing", "playing",
                "sending", "closing", "maximizing", "minimizing", "creating", "deleting",
                "i'll", "i will", "let me", "one moment", "working on it",
            )
        )
        if not self._is_action_request(request) and not claims_done and not claims_progress:
            return llm_result

        llm_result["success"] = False
        llm_result.pop("no_tool_calls", None)
        silent = (not text) or claims_done or claims_progress
        llm_result["response"] = (
            "I didn't run anything for that request."
            if silent
            else f"I didn't run anything for that request. {text}"
        )
        return llm_result

    def __init__(self, agent: Any):
        self.agent = agent
        self.llm = LLMProvider()
        self._fallback_providers: Dict[str, LLMProvider] = {}
        self.current_task: Optional[TaskState] = None
        self.task_history: List[TaskState] = []
        self.context: Dict[str, Any] = {
            "last_directory": None,
            "last_files": [],
            "last_active_app": None,
            "last_browser_url": None,
        }

    # Compact tool set for small local models (Ollama e.g. qwen3:4b) so context stays tight.
    OLLAMA_TOOL_NAMES = frozenset({
        "run_powershell",
        "launch_application", "open_folder", "lock_workstation", "get_system_telemetry",
        "set_system_volume", "media_control", "get_running_windows", "read_clipboard",
        "list_directory", "search_files", "read_file", "create_file_or_folder",
        "delete_file_or_folder",
        "inspect_screen", "click_coordinate", "type_text", "press_keys", "scroll_screen",
        "switch_window",
        "browser_navigate", "browser_click_first_result", "browser_extract_text", "smart_search",
        "smart_click", "smart_type",
        "open_whatsapp", "search_contacts", "get_active_chat", "open_whatsapp_chat",
        "get_latest_message", "send_whatsapp_message",
        "set_reminder", "list_active_reminders", "cancel_active_reminder",
        "list_unread_emails", "search_emails", "read_email", "send_email", "list_upcoming_events",
        "create_calendar_event",
        "remember_fact", "verify_action", "cancel_current_task",
    })

    WHATSAPP_MODEL_TOOLS = frozenset({
        "open_whatsapp", "get_whatsapp_state", "get_active_chat",
        "get_unread_chats", "search_contacts", "resolve_contact",
        "open_whatsapp_chat", "get_chat_messages", "get_latest_message",
        "get_contact_information", "search_messages", "view_status", "search_media",
        "send_whatsapp_message", "reply_to_message",
    })

    WHATSAPP_ALL_TOOLS = WHATSAPP_MODEL_TOOLS | frozenset({"open_chat", "send_message"})

    # Full model-facing tool set for cloud/frontier models while still omitting duplicate aliases.
    MODEL_TOOL_NAMES = frozenset({
        # Shell (confirmation-gated)
        "run_powershell",
        # System, power & hardware
        "launch_application", "open_folder", "lock_workstation", "get_system_telemetry",
        "get_top_processes", "set_system_volume", "set_screen_brightness", "media_control",
        "get_running_windows", "snap_window", "manage_virtual_desktop", "open_windows_setting",
        "read_clipboard", "write_clipboard", "get_network_info", "kill_process",
        "empty_recycle_bin", "system_power",
        # Files & archives
        "list_drives", "navigate_to_path", "list_directory", "search_files", "read_file",
        "get_file_metadata", "create_file_or_folder", "write_or_append_file",
        "copy_or_move_item", "zip_or_unzip", "delete_file_or_folder",
        # Screen, vision & input
        "take_screenshot", "inspect_screen", "describe_screen", "click_coordinate",
        "double_click", "right_click", "drag_mouse", "type_text", "press_keys",
        "scroll_screen", "switch_window", "focus_window", "close_active_window",
        "maximize_active_window", "minimize_active_window",
        # Web, Playwright & free knowledge
        "open_url", "fetch_webpage_summary", "get_free_knowledge", "get_weather",
        "browser_navigate", "browser_click_first_result",
        "browser_find_elements", "browser_click_element", "browser_extract_text",
        "browser_type", "browser_scroll", "browser_history_nav", "browser_get_tabs",
        "browser_switch_tab",
        # Desktop UI automation & WhatsApp
        "get_active_window", "get_active_window_context", "list_running_applications",
        "get_ui_elements", "click_ui_element", "type_into_ui_element", "wait_for_condition",
        "smart_click", "smart_type",
        # WhatsApp Desktop stateful agent suite
        "open_whatsapp", "get_whatsapp_state", "get_active_chat",
        "get_unread_chats", "search_contacts", "resolve_contact",
        "open_whatsapp_chat", "get_chat_messages", "get_latest_message",
        "get_contact_information", "search_messages", "view_status", "search_media",
        "send_whatsapp_message", "reply_to_message",
        # Reminders & scheduling
        "set_reminder", "list_active_reminders", "cancel_active_reminder",
        # Google Workspace
        "list_unread_emails", "search_emails", "read_email", "send_email", "list_upcoming_events",
        "create_calendar_event",
        # Memory & verification
        "remember_fact", "forget_fact", "verify_action", "cancel_current_task",
    })

    def _tools_for_provider(self, provider_name: Optional[str] = None) -> List[Callable]:
        """Returns the curated tool declarations exposed to the configured LLM."""
        import tools as tools_pkg

        active_provider = provider_name or getattr(self.llm, "provider", "")
        selected = (
            self.OLLAMA_TOOL_NAMES
            if str(active_provider or "").strip().lower() == "ollama"
            else self.MODEL_TOOL_NAMES
        )
        declarations: List[Callable] = []
        for name in sorted(selected):
            if name not in self.agent.tool_registry:
                continue
            fn = self.agent.tool_registry[name]
            # If a test replaces a registry tool with a MagicMock (no __name__),
            # supply the canonical function signature for schema generation while
            # _llm_tool_executor still dispatches calls through tool_registry[name].
            if not hasattr(fn, "__name__"):
                canonical = getattr(tools_pkg, name, None)
                if callable(canonical) and hasattr(canonical, "__name__"):
                    declarations.append(canonical)
                continue
            declarations.append(fn)
        return declarations

    def _llm_tool_executor(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalizes tool results for the model.

        Several tools return a list or a bare string (get_running_windows,
        list_running_applications, get_free_knowledge).  The fast path expects
        those native types, so the wrapping happens here instead of inside
        TARSAgent.execute_tool.
        """
        # A greeting or common word is not a real contact.  A capable model may
        # still try to "send hello ..." to a contact literally named "hello";
        # refuse and ask for a real name instead of firing a message into the void.
        args = dict(args or {})
        wa_state = getattr(self.agent, "whatsapp_state", None)
        if name in {"send_whatsapp_message", "send_message", "reply_to_message"}:
            raw_contact = str(args.get("contact_name") or args.get("contact") or "").strip()
            ref_res: Dict[str, Any] = {}
            if wa_state is not None and raw_contact:
                ref_res = wa_state.resolve_contact_reference(raw_contact)
                if ref_res.get("ambiguous"):
                    return {
                        "success": False,
                        "ambiguous": True,
                        "candidates": ref_res.get("candidates", []),
                        "error": ref_res.get("clarification_question") or ref_res.get("error"),
                        "whatsapp_state": wa_state.snapshot(),
                    }
            contact_low = raw_contact.lower()
            is_contextual = bool(ref_res.get("resolved") and ref_res.get("contact"))
            if name == "send_whatsapp_message" and not is_contextual:
                if contact_low in self.agent.WHATSAPP_SEND_STOPWORDS or contact_low in self.agent.WHATSAPP_CONTACT_PLACEHOLDERS:
                    return {
                        "success": False,
                        "error": f"'{raw_contact}' is not a valid contact name. Ask the user who to message.",
                    }
            dup_target = (
                (ref_res.get("contact") if is_contextual else raw_contact)
                or (wa_state.active_chat if wa_state is not None else "")
                or ""
            )
            msg_text = str(args.get("message") or "").strip()
            active_req = getattr(self, "_active_user_request", "") or ""
            explicit_resend = bool(
                re.search(r"\b(?:resend|send\s+again|retry\s+send|try\s+again)\b", active_req, re.IGNORECASE)
            )
            if (
                wa_state is not None
                and dup_target
                and msg_text
                and not explicit_resend
                and wa_state.has_recent_uncertain_send(dup_target, msg_text)
            ):
                return {
                    "success": False,
                    "verified": False,
                    "verification": "BLOCKED_DUPLICATE",
                    "contact": dup_target,
                    "error": (
                        f"Previous send of this message to '{dup_target}' had unconfirmed delivery. "
                        "Refusing automatic duplicate resend; tell the user to verify WhatsApp manually."
                    ),
                    "whatsapp_state": wa_state.snapshot(),
                }
        # Reject hallucinated/unknown tool names (and search/browser tools that are
        # not exposed to the model) so it answers from its own knowledge instead.
        allowed = self.MODEL_TOOL_NAMES | self.OLLAMA_TOOL_NAMES | self.WHATSAPP_ALL_TOOLS
        if name not in allowed:
            return {"success": False, "error": f"Tool '{name}' is not available. Answer from your own knowledge."}
        result = self.agent.execute_tool(name, args)
        # Sync agent & memory state with WhatsApp interaction outcome
        if isinstance(result, dict) and result.get("success") and name in {
            "send_whatsapp_message", "send_message", "reply_to_message",
            "open_whatsapp_chat", "open_chat",
        }:
            contact_val = result.get("contact") or args.get("contact_name") or args.get("contact")
            if contact_val:
                self.agent.last_whatsapp_contact = contact_val
            if name in {"send_whatsapp_message", "send_message", "reply_to_message"}:
                try:
                    if hasattr(self.agent, "memory") and self.agent.memory is not None:
                        self.agent.memory.set_user_preference(
                            "last_whatsapp_message",
                            {
                                "contact": contact_val,
                                "message": args.get("message"),
                                "verification": result.get("verification"),
                            },
                        )
                except Exception:
                    pass
        if isinstance(result, dict):
            if wa_state is not None and name in self.WHATSAPP_ALL_TOOLS:
                result.setdefault("whatsapp_state", wa_state.snapshot())
            return result
        return {"success": result is not None, "result": result}

    def cancel(self) -> None:
        """Signals cancellation of the active task."""
        if self.current_task:
            self.current_task.cancelled = True
        from tools.verification import request_task_cancellation
        request_task_cancellation()

    def resolve_follow_up(self, request: str) -> Optional[Dict[str, Any]]:
        """
        Resolves contextual follow-up requests referring to recent task state.
        Examples:
        - "Open the first file"
        - "Read that file" / "Read the first python file"
        - "Go back"
        - "Tell me what you found"
        """
        clean = request.strip().lower()

        # Follow-up: "read the first file" or "read that file"
        if re.search(r"\bread\s+(?:the\s+)?(?:first|that|1st)\s+file\b|\bread\s+it\b", clean):
            last_files = self.context.get("last_files", [])
            last_dir = self.context.get("last_directory")
            if last_files and last_dir:
                first_file = last_files[0]
                filename = first_file["name"] if isinstance(first_file, dict) else str(first_file)
                full_path = str(Path(last_dir) / filename)
                res = self.agent.execute_tool("read_file", {"path": full_path})
                if res.get("success"):
                    snippet = res["content"][:240].strip()
                    resp = f"Reading '{filename}': {snippet}"
                    return {
                        "success": True,
                        "response": resp,
                        "steps": [{"tool": "read_file", "args": {"path": full_path}, "result": res}]
                    }
                return {
                    "success": False,
                    "response": f"Could not read '{filename}': {res.get('error', 'Unknown error')}.",
                    "steps": [{"tool": "read_file", "args": {"path": full_path}, "result": res}]
                }

        # Follow-up: "read the first python file"
        if re.search(r"\bread\s+(?:the\s+)?(?:first|that|1st)\s+python\s+file\b", clean):
            last_files = self.context.get("last_files", [])
            last_dir = self.context.get("last_directory")
            if last_files and last_dir:
                py_file = next(
                    (f["name"] if isinstance(f, dict) else f for f in last_files if (f.get("name", "") if isinstance(f, dict) else str(f)).endswith(".py")),
                    None
                )
                if py_file:
                    full_path = str(Path(last_dir) / py_file)
                    res = self.agent.execute_tool("read_file", {"path": full_path})
                    if res.get("success"):
                        snippet = res["content"][:240].strip()
                        return {
                            "success": True,
                            "response": f"Contents of '{py_file}': {snippet}",
                            "steps": [{"tool": "read_file", "args": {"path": full_path}, "result": res}]
                        }

        # Follow-up: "open the first file"
        if re.search(r"\bopen\s+(?:the\s+)?(?:first|that|1st)\s+file\b", clean):
            last_files = self.context.get("last_files", [])
            last_dir = self.context.get("last_directory")
            if last_files and last_dir:
                first_file = last_files[0]
                filename = first_file["name"] if isinstance(first_file, dict) else str(first_file)
                full_path = str(Path(last_dir) / filename)
                res = self.agent.execute_tool("launch_application", {"app_name": full_path})
                return {
                    "success": True,
                    "response": f"Opened '{filename}'.",
                    "steps": [{"tool": "launch_application", "args": {"app_name": full_path}, "result": res}]
                }

        # Follow-up: "what did you find" / "tell me what you found" / "summarize"
        if re.search(r"\b(?:what\s+did\s+you\s+find|tell\s+me\s+what\s+you\s+found|what\s+was\s+in\s+there)\b", clean):
            last_dir = self.context.get("last_directory")
            last_files = self.context.get("last_files", [])
            if last_dir:
                names = [f["name"] if isinstance(f, dict) else str(f) for f in last_files[:10]]
                return {
                    "success": True,
                    "response": f"In {last_dir}, I found {len(last_files)} file(s): {', '.join(names) if names else 'none'}.",
                    "steps": []
                }

        # Follow-up: "read that webpage" / "extract page text" after browser navigation
        if re.search(r"\b(?:read\s+(?:this|that)\s+page|extract\s+(?:page\s+)?text|what\s+does\s+the\s+page\s+say)\b", clean):
            if self.context.get("last_browser_url"):
                res = self.agent.execute_tool("browser_extract_text", {})
                if res.get("success"):
                    snippet = (res.get("text") or "")[:400].strip()
                    return {
                        "success": True,
                        "response": f"Page '{res.get('title', 'Page')}': {snippet}",
                        "steps": [{"tool": "browser_extract_text", "args": {}, "result": res}],
                    }

        return None

    def execute_compound_filesystem_task(self, request: str, on_status: Optional[Callable[[str], None]] = None) -> Optional[Dict[str, Any]]:
        """
        Executes multi-step filesystem navigation, drive checks, and folder listing.
        Handles the master requirement:
        'Open D drive, go to the CWD folder, and tell me what files are inside.'
        """
        clean = request.strip().lower()

        # Match pattern: open/go to <drive/path> and (go to/open) <folder> and (tell me/list) files
        fs_compound = re.search(
            r"(?:open|navigate\s+to|go\s+to)\s+(?:the\s+)?([a-zA-Z])(?:\s*(?:drive|disk)|:)?.*?(?:go\s+to|open|navigate\s+to)\s+(?:the\s+)?([a-zA-Z0-9_\-\.]+)(?:\s+(?:folder|directory|dir))?(?:(?:,\s*|\s+)?and\s+(?:tell\s+me|list|show|what).*?)?$",
            clean,
            re.IGNORECASE
        )
        if not fs_compound:
            # Alternate match: "open <target> and tell me what files are inside"
            fs_alt = re.search(
                r"(?:open|navigate\s+to|go\s+to)\s+(?:the\s+)?([a-zA-Z0-9_\\\-:\.\s]+?)(?:\s+folder|\s+directory|\s+dir)?\s+and\s+(?:tell\s+me\s+what\s+files\s+are\s+inside|list\s+the\s+files|show\s+files)",
                clean
            )
            if fs_alt:
                target_str = fs_alt.group(1).strip()
                drive_letter = target_str[0].upper() if (len(target_str) >= 2 and target_str[1] == ":") else None
                subfolder = target_str[2:].strip("\\/ ") if drive_letter else target_str
            else:
                return None
        else:
            drive_letter = fs_compound.group(1).upper()
            subfolder = fs_compound.group(2).strip()

        steps_log = []

        if on_status:
            on_status(f"Resolving drive {drive_letter}: and target folder '{subfolder}'...")

        # Step 1: Verify drive exists
        drive_path = Path(f"{drive_letter}:\\")
        if not drive_path.exists():
            from tools.filesystem_tools import list_drives
            drives_info = list_drives()
            available = [d["drive"] for d in drives_info.get("drives", [])]
            return {
                "success": False,
                "response": f"Drive {drive_letter}: does not exist. Available drives on this PC: {', '.join(available)}.",
                "steps": [{"step": 1, "description": "Verify drive", "result": {"exists": False}}]
            }

        # Step 2: Resolve subfolder on the drive
        target_dir = drive_path / subfolder
        # Check if subfolder was literally "cwd" (current working directory)
        if subfolder.lower() in ["cwd", "current directory", "working directory"]:
            # Check if CWD is located on this drive
            cwd_path = Path.cwd()
            if cwd_path.drive.upper() == f"{drive_letter}:":
                target_dir = cwd_path

        step1_res = self.agent.execute_tool("navigate_to_path", {"target_path": str(target_dir), "open_in_explorer": True})
        steps_log.append({"tool": "navigate_to_path", "args": {"target_path": str(target_dir)}, "result": step1_res})

        # Step 3: Handle missing directory
        if not target_dir.exists():
            # Inspect root of drive to offer real existing directories
            available_folders = []
            try:
                available_folders = [item.name for item in drive_path.iterdir() if item.is_dir()][:8]
            except Exception:
                pass

            resp = (
                f"Folder '{subfolder}' does not exist on {drive_letter}:\\. "
                f"Available folders on {drive_letter}: drive are: {', '.join(available_folders) if available_folders else 'None'}."
            )
            return {
                "success": False,
                "response": resp,
                "steps": steps_log
            }

        # Step 4: List directory contents (separating files from folders)
        if on_status:
            on_status(f"Inspecting directory contents of {target_dir}...")

        list_res = self.agent.execute_tool("list_directory", {"location": str(target_dir)})
        steps_log.append({"tool": "list_directory", "args": {"location": str(target_dir)}, "result": list_res})

        # Update task context for follow-up questions
        self.context["last_directory"] = str(target_dir)
        self.context["last_files"] = list_res.get("files", [])

        # Step 5: Format spoken output
        file_count = list_res.get("files_count", 0)
        dir_count = list_res.get("subdirectories_count", 0)
        summary = list_res.get("summary", "")

        spoken_reply = (
            f"Opened {target_dir.name} on {drive_letter} drive in File Explorer. "
            f"Contains {dir_count} folder(s) and {file_count} file(s). {summary}"
        )

        return {
            "success": True,
            "response": spoken_reply,
            "steps": steps_log
        }

    def execute_compound_browser_task(self, request: str, on_status: Optional[Callable[[str], None]] = None) -> Optional[Dict[str, Any]]:
        """
        Executes multi-step browser automation.
        Handles the master requirement:
        'Open Brave, search YouTube, and click the first relevant result.'
        """
        clean = request.strip().lower()

        # Match pattern: open (brave|browser|chrome), search <query>, and click the first (relevant) result
        browser_match = re.search(
            r"(?:open|launch)\s+(?:brave|chrome|browser).*?(?:search|look\s+up)\s+(?:for\s+)?([a-zA-Z0-9_\-\s]+?)\s+and\s+(?:click|open)\s+(?:the\s+)?(?:first|top|1st)\s+(?:relevant\s+)?result",
            clean
        )
        if not browser_match:
            # Check if query is simpler: "search youtube and click the first result"
            search_click = re.search(
                r"search\s+(?:for\s+)?([a-zA-Z0-9_\-\s]+?)\s+and\s+(?:click|open)\s+(?:the\s+)?(?:first|top|1st)\s+(?:relevant\s+)?result",
                clean
            )
            if search_click:
                query = search_click.group(1).strip()
            else:
                return None
        else:
            query = browser_match.group(1).strip()

        steps_log = []

        if on_status:
            on_status(f"Launching Brave and searching for '{query}'...")

        # Step 1: Browser search via Playwright
        engine = "youtube" if "youtube" in query.lower() and query.lower() != "youtube" else "google"
        search_res = self.agent.execute_tool("browser_search", {"query": query, "engine": engine})
        steps_log.append({"tool": "browser_search", "args": {"query": query, "engine": engine}, "result": search_res})

        if not search_res.get("success"):
            # Seamless fallback to desktop browser smart interaction
            launch_res = self.agent.execute_tool("launch_application", {"app_name": "brave"})
            time.sleep(0.15)
            s_res = self.agent.execute_tool("smart_search", {"query": query, "engine": engine})
            time.sleep(0.15)
            c_res = self.agent.execute_tool("smart_click", {"target": "first result"})
            steps_log.extend([
                {"tool": "launch_application", "args": {"app_name": "brave"}, "result": launch_res},
                {"tool": "smart_search", "args": {"query": query, "engine": engine}, "result": s_res},
                {"tool": "smart_click", "args": {"target": "first result"}, "result": c_res}
            ])
            return {
                "success": True,
                "response": f"Opened Brave, searched for '{query}' on {engine.title()}, and activated the first result.",
                "steps": steps_log
            }

        # Step 2: Click the first organic search result
        if on_status:
            on_status(f"Inspecting search results and selecting first organic link...")

        click_res = self.agent.execute_tool("browser_click_first_result", {"intent_filter": query})
        steps_log.append({"tool": "browser_click_first_result", "args": {"intent_filter": query}, "result": click_res})

        if click_res.get("success"):
            dest_title = click_res.get("destination_title") or click_res.get("clicked_title") or query
            dest_url = click_res.get("url", "")
            self.context["last_browser_url"] = dest_url
            spoken_reply = f"Searched '{query}' in Brave and opened the first result: '{dest_title}'."
            return {
                "success": True,
                "response": spoken_reply,
                "steps": steps_log
            }
        else:
            # Fallback to desktop smart click
            c_res = self.agent.execute_tool("smart_click", {"target": "first result"})
            steps_log.append({"tool": "smart_click", "args": {"target": "first result"}, "result": c_res})
            return {
                "success": True,
                "response": f"Searched for '{query}' in Brave and activated the top result.",
                "steps": steps_log
            }

    def _get_fallback_providers(self) -> List[LLMProvider]:
        """
        Returns secondary configured & reachable LLMProvider instances in priority
        order so TARS fails over seamlessly if the primary provider is down,
        rate-limited, or returning an upstream gateway notice.
        """
        if not isinstance(self.llm, LLMProvider) or os.getenv("TARS_DISABLE_PROVIDER_FAILOVER") == "1":
            return []

        order = ("groq", "nvidia")
        candidates: List[LLMProvider] = []
        for prov_name in order:
            if prov_name == self.llm.provider:
                continue
            inst = self._fallback_providers.get(prov_name)
            if inst is None:
                inst = LLMProvider(provider=prov_name)
                self._fallback_providers[prov_name] = inst
            if inst.available():
                candidates.append(inst)
        return candidates

    def run_orchestrated_task(self, request: str, on_status: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """
        Main orchestration entry point.
        Checks cancellation, resolves follow-ups, executes deterministic compound flows,
        or delegates to the multi-turn LLM agent loop with automatic provider failover.
        """
        reset_cancellation()
        self._active_user_request = request
        wa_state = getattr(self.agent, "whatsapp_state", None)
        if wa_state is not None:
            try:
                wa_state.record_user_instruction(request)
            except Exception:
                pass

        # 1. Check contextual follow-up
        follow_up_res = self.resolve_follow_up(request)
        if follow_up_res:
            return follow_up_res

        # 2. Check compound filesystem task pattern
        fs_res = self.execute_compound_filesystem_task(request, on_status=on_status)
        if fs_res:
            return fs_res

        # 3. Check compound browser task pattern
        browser_res = self.execute_compound_browser_task(request, on_status=on_status)
        if browser_res:
            return browser_res

        # 4. Multi-turn LLM ReAct Tool-Calling Agent Loop with seamless provider failover
        tools_list = self._tools_for_provider()
        system_instruction = f"{self.agent.system_prompt}\n\n{build_runtime_context()}"
        if wa_state is not None:
            try:
                wa_ctx = wa_state.to_prompt_context()
                if wa_ctx:
                    system_instruction = f"{system_instruction}\n\n{wa_ctx}"
            except Exception:
                pass
        history = self.agent.memory.get_recent_messages(limit=10) if hasattr(self.agent, "memory") else None
        user_prefs = self.agent.memory.get_all_preferences() if hasattr(self.agent, "memory") else None

        primary_error_result: Optional[Dict[str, Any]] = None
        if self.llm.available():
            if on_status:
                on_status(f"Formulating autonomous execution plan ({self.llm.provider})...")

            llm_result = self.llm.run_tool_agent_loop(
                prompt=request,
                system_instruction=system_instruction,
                tools_list=tools_list,
                tool_executor=self._llm_tool_executor,
                history=history,
                user_preferences=user_prefs,
                on_status=on_status,
                max_iterations=10,
                timeout=self._llm_timeout(self.llm.provider)
            )

            if llm_result:
                # Return directly if succeeded, cancelled, or already executed tool side effects
                if llm_result.get("success") or llm_result.get("cancelled") or llm_result.get("steps"):
                    return self._honest_llm_result(llm_result, request)
                primary_error_result = llm_result

        # Fail over across secondary configured providers only when zero tool side effects occurred
        for fallback in self._get_fallback_providers():
            if on_status:
                on_status(f"Primary LLM unavailable; failing over to {fallback.provider}...")
            fb_tools = self._tools_for_provider(provider_name=fallback.provider)

            fb_result = fallback.run_tool_agent_loop(
                prompt=request,
                system_instruction=system_instruction,
                tools_list=fb_tools,
                tool_executor=self._llm_tool_executor,
                history=history,
                user_preferences=user_prefs,
                on_status=on_status,
                max_iterations=10,
                timeout=self._llm_timeout(fallback.provider),
            )
            if fb_result and (fb_result.get("success") or fb_result.get("cancelled") or fb_result.get("steps")):
                return self._honest_llm_result(fb_result, request)

        if primary_error_result:
            return self._honest_llm_result(primary_error_result, request)

        # 5. Fallback: report why the model is offline, or ask for a clearer command
        reason = self.llm.unavailable_reason()
        if reason:
            fallback_msg = f"My language model is offline: {reason}."
        else:
            fallback_msg = f"Standing by. I received '{request}'. Specify an action or application."
        return {
            "success": False,
            "response": fallback_msg,
            "steps": []
        }
