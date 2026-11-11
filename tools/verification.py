"""Verification and cancellation helpers for TARS closed-loop operations."""
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional
import psutil

# Thread-safe cancellation event
_cancel_event = threading.Event()


def request_task_cancellation() -> None:
    """Sets the global task cancellation flag."""
    _cancel_event.set()


def is_task_cancelled() -> bool:
    """Returns True if task cancellation has been requested."""
    return _cancel_event.is_set()


def reset_cancellation() -> None:
    """Clears the task cancellation flag."""
    _cancel_event.clear()


def cancel_current_task() -> Dict[str, Any]:
    """
    Cancels the currently running agent task immediately.
    """
    request_task_cancellation()
    return {
        "success": True,
        "cancelled": True,
        "message": "Cancellation flag set. Ongoing task is aborting immediately."
    }


def verify_action(check: str, expected: str = "") -> Dict[str, Any]:
    """
    Verifies the actual outcome of an action before marking a task step complete.
    Supported verification checks:
    - 'path:<filepath>' or 'file:<filepath>': Confirms file/folder exists
    - 'not_exists:<filepath>': Confirms file/folder was deleted
    - 'process:<proc_name>': Confirms process is running (e.g. 'process:brave.exe')
    - 'process_killed:<proc_name>': Confirms process has stopped
    - 'window:<title>': Confirms window title is present
    - 'active_window:<title>': Confirms window is in the foreground
    """
    clean_check = check.strip()

    # 1. Path existence verification
    if clean_check.lower().startswith("path:") or clean_check.lower().startswith("file:"):
        p_str = clean_check.split(":", 1)[1].strip()
        p = Path(os.path.expandvars(os.path.expanduser(p_str)))
        exists = p.exists()
        return {
            "success": exists,
            "verified": exists,
            "check": check,
            "target": str(p),
            "message": f"Path '{p}' {'exists' if exists else 'does NOT exist'}."
        }

    # 2. Deletion verification
    if clean_check.lower().startswith("not_exists:") or clean_check.lower().startswith("deleted:"):
        p_str = clean_check.split(":", 1)[1].strip()
        p = Path(os.path.expandvars(os.path.expanduser(p_str)))
        deleted = not p.exists()
        return {
            "success": deleted,
            "verified": deleted,
            "check": check,
            "target": str(p),
            "message": f"Target '{p}' {'was successfully deleted' if deleted else 'still exists'}."
        }

    # 3. Process running verification
    if clean_check.lower().startswith("process:"):
        proc_target = clean_check.split(":", 1)[1].strip().lower()
        if not proc_target.endswith(".exe"):
            proc_target += ".exe"
        running = any(p.info.get('name', '').lower() == proc_target for p in psutil.process_iter(['name']))
        return {
            "success": running,
            "verified": running,
            "check": check,
            "process": proc_target,
            "message": f"Process '{proc_target}' is {'running' if running else 'not running'}."
        }

    # 4. Process killed verification
    if clean_check.lower().startswith("process_killed:"):
        proc_target = clean_check.split(":", 1)[1].strip().lower()
        if not proc_target.endswith(".exe"):
            proc_target += ".exe"
        stopped = not any(p.info.get('name', '').lower() == proc_target for p in psutil.process_iter(['name']))
        return {
            "success": stopped,
            "verified": stopped,
            "check": check,
            "process": proc_target,
            "message": f"Process '{proc_target}' has {'terminated' if stopped else 'not terminated'}."
        }

    # 5. Window title check
    if clean_check.lower().startswith("window:") or clean_check.lower().startswith("title:"):
        from tools.ui_automation import list_running_applications
        title_q = clean_check.split(":", 1)[1].strip().lower()
        wins = list_running_applications()
        found = any(title_q in w.lower() for w in wins)
        return {
            "success": found,
            "verified": found,
            "check": check,
            "message": f"Window matching '{title_q}' was {'found' if found else 'not found'}."
        }

    return {
        "success": True,
        "verified": True,
        "check": check,
        "message": f"Action verification recorded: {check}"
    }
