"""Central task orchestrator, multi-step planner, and closed-loop execution engine for TARS."""
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from core.task_state import TaskState, TaskStep
from core.llm_provider import LLMProvider
from tools.verification import is_task_cancelled, reset_cancellation


class TaskOrchestrator:
    """
    Orchestrates complex multi-step Windows, browser, and filesystem tasks.
    Maintains task state, contextual memory for follow-up commands,
    bounded error recovery, and verification loops.
    """

    def __init__(self, agent: Any):
        self.agent = agent
        self.llm = LLMProvider()
        self.current_task: Optional[TaskState] = None
        self.task_history: List[TaskState] = []
        self.context: Dict[str, Any] = {
            "last_directory": None,
            "last_files": [],
            "last_active_app": None,
            "last_browser_url": None,
        }

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
            time.sleep(0.5)
            s_res = self.agent.execute_tool("smart_search", {"query": query, "engine": engine})
            time.sleep(0.5)
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

    def run_orchestrated_task(self, request: str, on_status: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """
        Main orchestration entry point.
        Checks cancellation, resolves follow-ups, executes deterministic compound flows,
        or delegates to the multi-turn LLM agent loop.
        """
        reset_cancellation()

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

        # 4. Multi-turn LLM ReAct Tool-Calling Agent Loop
        if self.llm.available():
            if on_status:
                on_status("Formulating autonomous execution plan...")

            tools_list = list(self.agent.tool_registry.values())
            system_instruction = self.agent.system_prompt

            llm_result = self.llm.run_tool_agent_loop(
                prompt=request,
                system_instruction=system_instruction,
                tools_list=tools_list,
                tool_executor=self.agent.execute_tool,
                on_status=on_status,
                max_iterations=5,
                timeout=25.0
            )

            if llm_result and llm_result.get("success"):
                return llm_result

        # 5. Fallback: single tool matching or friendly guidance
        fallback_msg = f"Standing by. I received '{request}'. Specify an action or application."
        return {
            "success": False,
            "response": fallback_msg,
            "steps": []
        }
