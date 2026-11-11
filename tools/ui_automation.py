"""Windows UI Automation helpers for TARS using pywinauto UIA backend with robust fallbacks."""
import os
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import psutil


def get_active_window() -> Dict[str, Any]:
    """
    Returns information about the currently active foreground window on Windows.
    Uses pywinauto UIA backend with win32gui fallback.
    """
    try:
        from pywinauto import Desktop
        win = Desktop(backend="uia").active_window()
        return {
            "success": True,
            "title": win.window_text(),
            "class_name": win.class_name(),
            "control_type": win.control_type()
        }
    except Exception:
        pass

    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            title = win32gui.GetWindowText(hwnd)
            cls_name = win32gui.GetClassName(hwnd)
            return {
                "success": True,
                "title": title or "Desktop",
                "class_name": cls_name or "Unknown",
                "control_type": "Window"
            }
    except Exception:
        pass

    return {
        "success": False,
        "error": "Unable to identify active foreground window (no desktop interactive window detected)."
    }


def list_running_applications() -> List[str]:
    """
    Returns a deduplicated list of titles of all visible, top-level application windows.
    """
    titles = []

    # 1. Try pywinauto UIA backend
    try:
        from pywinauto import Desktop
        wins = Desktop(backend="uia").windows()
        for w in wins:
            try:
                t = w.window_text()
                if t and t.strip() and t.strip() not in titles:
                    titles.append(t.strip())
            except Exception:
                continue
    except Exception:
        pass

    # 2. Try win32gui enumeration
    if not titles:
        try:
            import win32gui
            def enum_handler(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    text = win32gui.GetWindowText(hwnd)
                    if text and text.strip() and text.strip() not in titles:
                        titles.append(text.strip())
            win32gui.EnumWindows(enum_handler, None)
        except Exception:
            pass

    # 3. Fallback to running process names
    if not titles:
        try:
            for proc in psutil.process_iter(['name']):
                name = proc.info.get('name')
                if name and name.endswith('.exe') and name not in titles:
                    titles.append(name)
        except Exception:
            pass

    return titles[:50]


def get_ui_elements(window_title: Optional[str] = None, control_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Inspects accessible UI controls (buttons, edit boxes, menus) in the specified or active window.
    """
    try:
        from pywinauto import Desktop
        if window_title:
            win = Desktop(backend="uia").window(title_re=f".*{window_title}.*")
        else:
            win = Desktop(backend="uia").active_window()

        win.wait("exists", timeout=3)
        elems = []
        for c in win.descendants():
            try:
                name = c.window_text()
                ctype = c.control_type()
                auto_id = c.automation_id()
                if name and name.strip():
                    elems.append({
                        "name": name.strip(),
                        "type": ctype,
                        "automation_id": auto_id
                    })
                    if len(elems) >= 80:
                        break
            except Exception:
                continue

        if control_type:
            elems = [e for e in elems if control_type.lower() in e["type"].lower()]

        return {"success": True, "count": len(elems), "elements": elems}
    except Exception as e:
        return {"success": False, "error": f"Failed to retrieve UI elements: {str(e)}"}


def click_ui_element(name: str, window_title: Optional[str] = None) -> Dict[str, Any]:
    """
    Clicks an accessible UI control by name or label using Windows UI Automation.
    """
    try:
        from pywinauto import Desktop
        if window_title:
            win = Desktop(backend="uia").window(title_re=f".*{window_title}.*")
        else:
            win = Desktop(backend="uia").active_window()

        win.wait("exists", timeout=4)
        
        # Try direct child match first
        try:
            ctrl = win.child_window(title=name, found_index=0)
            ctrl.click_input()
            return {"success": True, "message": f"Clicked UI element '{name}' via direct match."}
        except Exception:
            pass

        # Try partial match across descendants
        for child in win.descendants():
            try:
                child_text = child.window_text()
                if name.lower() in child_text.lower():
                    child.click_input()
                    return {"success": True, "message": f"Clicked UI element matching '{name}' ('{child_text}')."}
            except Exception:
                continue

        return {"success": False, "error": f"UI element matching '{name}' was not found in the window."}
    except Exception as e:
        return {"success": False, "error": f"Failed to click UI element: {str(e)}"}


def type_into_ui_element(name: str, text: str, window_title: Optional[str] = None) -> Dict[str, Any]:
    """
    Types text into an accessible edit control or text field.
    """
    try:
        from pywinauto import Desktop
        if window_title:
            win = Desktop(backend="uia").window(title_re=f".*{window_title}.*")
        else:
            win = Desktop(backend="uia").active_window()

        win.wait("exists", timeout=4)
        ctrl = win.child_window(title_re=f".*{name}.*", found_index=0)
        ctrl.set_focus()
        ctrl.type_keys(text, with_spaces=True)
        return {"success": True, "message": f"Typed text into '{name}' successfully."}
    except Exception as e:
        return {"success": False, "error": f"Failed to type into UI element '{name}': {str(e)}"}


def wait_for_condition(condition: str, timeout: int = 10) -> Dict[str, Any]:
    """
    Waits until a specified condition is met or until timeout occurs.
    Supported condition types:
    - 'window:<Title>' or 'title:<Title>': Waits for window title to appear
    - 'process:<Name>': Waits for process to be running (e.g. 'process:brave.exe')
    - 'path:<Path>' or 'file:<Path>': Waits for file/folder to exist
    - 'seconds:<N>' or numeric: Simply sleeps for N seconds
    """
    start_time = time.time()
    clean_cond = condition.strip()
    timeout = max(1, min(60, timeout))

    # Numeric seconds condition (e.g. "3" or "seconds:3")
    if clean_cond.isdigit():
        time.sleep(float(clean_cond))
        return {"success": True, "condition": condition, "elapsed_seconds": float(clean_cond), "message": f"Waited {clean_cond} seconds."}

    if clean_cond.lower().startswith("seconds:") or clean_cond.lower().startswith("time:"):
        sec_val = float(clean_cond.split(":", 1)[1].strip())
        time.sleep(sec_val)
        return {"success": True, "condition": condition, "elapsed_seconds": sec_val, "message": f"Waited {sec_val} seconds."}

    # Polling loop
    poll_interval = 0.5
    while (time.time() - start_time) < timeout:
        # 1. Process check
        if clean_cond.lower().startswith("process:"):
            proc_target = clean_cond[8:].strip().lower()
            if not proc_target.endswith(".exe"):
                proc_target += ".exe"
            for p in psutil.process_iter(['name']):
                if p.info.get('name', '').lower() == proc_target:
                    return {
                        "success": True,
                        "condition": condition,
                        "met": True,
                        "elapsed_seconds": round(time.time() - start_time, 2),
                        "message": f"Process '{proc_target}' is active."
                    }

        # 2. Path / File existence check
        elif clean_cond.lower().startswith("path:") or clean_cond.lower().startswith("file:"):
            p_target = Path(clean_cond.split(":", 1)[1].strip())
            if p_target.exists():
                return {
                    "success": True,
                    "condition": condition,
                    "met": True,
                    "elapsed_seconds": round(time.time() - start_time, 2),
                    "message": f"Path '{p_target}' exists."
                }

        # 3. Window title check
        elif clean_cond.lower().startswith("window:") or clean_cond.lower().startswith("title:"):
            title_q = clean_cond.split(":", 1)[1].strip().lower()
            wins = list_running_applications()
            for w in wins:
                if title_q in w.lower():
                    return {
                        "success": True,
                        "condition": condition,
                        "met": True,
                        "elapsed_seconds": round(time.time() - start_time, 2),
                        "message": f"Found matching window: '{w}'."
                    }

        # 4. General search across active windows
        else:
            wins = list_running_applications()
            for w in wins:
                if clean_cond.lower() in w.lower():
                    return {
                        "success": True,
                        "condition": condition,
                        "met": True,
                        "elapsed_seconds": round(time.time() - start_time, 2),
                        "message": f"Condition met: window '{w}' active."
                    }

        time.sleep(poll_interval)

    return {
        "success": False,
        "condition": condition,
        "met": False,
        "elapsed_seconds": round(time.time() - start_time, 2),
        "error": f"Timed out after {timeout} seconds waiting for condition: '{condition}'."
    }
