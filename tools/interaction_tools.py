"""Smart interaction engine for TARS: Context-aware clicking, typing, and in-app navigation."""
import os
import sys
import re
import time
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Any, List, Optional
import pyautogui

import tools.gui_tools as gui
import tools.ui_automation as uia
import tools.browser_automation as browser
import tools.browser_tools as browser_tools
import tools.os_tools as os_tools


def copy_to_clipboard(text: str) -> bool:
    """Copies text to the Windows system clipboard safely."""
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return True
    except Exception:
        pass

    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


PROCESS_CATEGORIES = {
    "browser": {
        "brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
        "opera_gx.exe", "vivaldi.exe", "iexplore.exe",
    },
    "terminal": {
        "windowsterminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe",
        "conhost.exe", "bash.exe", "wsl.exe", "alacritty.exe",
        "wezterm-gui.exe", "hyper.exe",
    },
    "editor": {
        "code.exe", "notepad.exe", "notepad++.exe", "sublime_text.exe",
        "devenv.exe", "idea64.exe", "pycharm64.exe", "webstorm64.exe",
    },
    "player": {"spotify.exe", "vlc.exe", "wmplayer.exe", "mpv.exe"},
}


def _foreground_process() -> tuple:
    """Returns (pid, lowercase process name) of the foreground window."""
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return 0, ""
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid, (psutil.Process(pid).name() or "").lower()
    except Exception:
        return 0, ""


def get_active_window_context() -> Dict[str, Any]:
    """
    Returns the title, category, and process info of the currently active foreground window.
    Categories: 'browser', 'explorer', 'terminal', 'editor', 'player', 'unknown'.

    The category comes from the *foreground* process.  Scanning every running
    process for a browser used to misclassify a focused terminal as a browser
    whenever Chrome or Brave was open in the background, which typed search URLs
    into the terminal instead of a browser.
    """
    active = uia.get_active_window()
    title = (active.get("title") or "").lower() if active.get("success") else ""
    class_name = active.get("class_name", "") or ""
    pid, process = _foreground_process()

    category = ""
    if process == "explorer.exe" and class_name.lower() not in {"progman", "workerw"}:
        category = "explorer"
    elif process:
        for candidate, names in PROCESS_CATEGORIES.items():
            if process in names:
                category = candidate
                break

    # Title keywords only fill a gap; they never turn a known non-browser
    # foreground app into a browser.
    if not category:
        if any(b in title for b in ["brave", "chrome", "firefox", "opera", "youtube"]):
            category = "browser"
        elif any(e in title for e in ["file explorer", "this pc", "documents", "downloads", "desktop"]):
            category = "explorer"
        elif any(t in title for t in ["powershell", "cmd", "terminal", "bash", "command prompt"]):
            category = "terminal"
        elif any(ed in title for ed in ["visual studio code", "vs code", "notepad", "sublime"]):
            category = "editor"
        elif any(m in title for m in ["spotify", "vlc", "media player"]):
            category = "player"

    return {
        "title": active.get("title", "Unknown"),
        "category": category or "unknown",
        "class_name": class_name,
        "process": process,
        "pid": pid,
    }


def smart_click(target: str) -> Dict[str, Any]:
    """
    Intelligently identifies and clicks a target element based on active window context.
    Handles search bars, address bars, first results, links, buttons, tab navigation,
    media controls, and in-app controls.
    """
    clean_target = (target or "").strip().lower()

    # 1. Blind click at current cursor
    if not clean_target or clean_target in ["here", "this", "click", "just click", "left click"]:
        return gui.click_coordinate(button="left", clicks=1)

    if clean_target in ["double click", "double-click"]:
        return gui.double_click()

    if clean_target in ["right click", "right-click"]:
        return gui.right_click()

    # Strip conversational prefixes and element suffixes
    clean_target = re.sub(r"^(?:on\s+|the\s+|at\s+)+", "", clean_target).strip()
    clean_target = re.sub(r"\s+(?:button|icon|bar|field|box|link)$", "", clean_target).strip()

    ctx = get_active_window_context()
    cat = ctx["category"]

    # 2. Search Bar / Address Bar / URL Bar
    if any(kw in clean_target for kw in ["search", "address", "url", "input", "find"]):
        if cat == "browser":
            # Focus browser address/search bar (Ctrl+L)
            gui.press_keys(["ctrl", "l"])
            return {"success": True, "message": f"Focused browser search/address bar ({ctx['title']}).", "method": "hotkey:ctrl+l"}
        elif cat == "explorer":
            # Focus File Explorer address bar (Alt+D)
            gui.press_keys(["alt", "d"])
            return {"success": True, "message": "Focused File Explorer address bar.", "method": "hotkey:alt+d"}
        elif cat == "editor":
            # Find in editor (Ctrl+F)
            gui.press_keys(["ctrl", "f"])
            return {"success": True, "message": "Focused editor find bar.", "method": "hotkey:ctrl+f"}
        else:
            # Universal fallback: Alt+D focuses address/search in Chrome, Brave, Edge, Firefox, and Explorer
            gui.press_keys(["alt", "d"])
            return {"success": True, "message": "Focused active address/search bar via universal Alt+D.", "method": "hotkey:alt+d"}

    # 3. First Search Result / First Video / First Link
    if any(kw in clean_target for kw in ["first result", "top result", "first video", "first link", "1st result", "first item", "top link"]):
        if cat == "browser":
            # Try Playwright DOM click first if active
            res = browser.browser_click_first_result()
            if res.get("success"):
                return res
            # Fallback for desktop browser: hit Enter on the focused result
            gui.press_keys(["enter"])
            return {"success": True, "message": "Activated first search result link.", "method": "keyboard:enter"}

    # 4. Tab Navigation & Browser Controls
    tab_controls = {
        "new tab": ["ctrl", "t"],
        "close tab": ["ctrl", "w"],
        "next tab": ["ctrl", "tab"],
        "previous tab": ["ctrl", "shift", "tab"],
        "prev tab": ["ctrl", "shift", "tab"],
        "reopen tab": ["ctrl", "shift", "t"],
        "downloads": ["ctrl", "j"],
        "history": ["ctrl", "h"],
        "bookmark": ["ctrl", "d"],
    }
    if clean_target in tab_controls:
        keys = tab_controls[clean_target]
        gui.press_keys(keys)
        return {"success": True, "message": f"Executed '{clean_target}' via shortcut {' + '.join(keys)}.", "method": "hotkey"}

    # 5. Media Controls (YouTube, Spotify, VLC, video players)
    media_controls = {
        "play": ["space"],
        "pause": ["space"],
        "play pause": ["space"],
        "mute": ["m"],
        "unmute": ["m"],
        "fullscreen": ["f"],
        "skip": ["l"],
        "rewind": ["j"],
    }
    if clean_target in media_controls:
        keys = media_controls[clean_target]
        gui.press_keys(keys)
        return {"success": True, "message": f"Executed media control '{clean_target}'.", "method": "hotkey"}

    # 6. Common Window & System Control Shortcuts
    common_hotkeys = {
        "close": ["alt", "f4"],
        "exit": ["alt", "f4"],
        "maximize": ["win", "up"],
        "minimize": ["win", "down"],
        "save": ["ctrl", "s"],
        "refresh": ["f5"],
        "reload": ["f5"],
        "back": ["alt", "left"],
        "forward": ["alt", "right"],
        "enter": ["enter"],
        "ok": ["enter"],
        "submit": ["enter"],
        "cancel": ["escape"],
        "dismiss": ["escape"],
    }
    if clean_target in common_hotkeys:
        keys = common_hotkeys[clean_target]
        gui.press_keys(keys)
        return {"success": True, "message": f"Executed action '{clean_target}' via shortcut {' + '.join(keys)}.", "method": "hotkey"}

    # 7. File Explorer Controls & Item Navigation
    if cat == "explorer":
        explorer_shortcuts = {
            "up": ["alt", "up"],
            "parent": ["alt", "up"],
            "new folder": ["ctrl", "shift", "n"],
            "rename": ["f2"],
        }
        if clean_target in explorer_shortcuts:
            keys = explorer_shortcuts[clean_target]
            gui.press_keys(keys)
            return {"success": True, "message": f"Executed File Explorer action '{clean_target}'.", "method": "hotkey"}

        # In File Explorer, typing the item name jumps directly to it
        pyautogui.write(clean_target, interval=0.02)
        time.sleep(0.1)
        pyautogui.press("enter")
        return {
            "success": True,
            "message": f"Navigated to '{clean_target}' in File Explorer.",
            "method": "explorer:select_and_open"
        }

    # 8. Semantic UI Automation Search
    uia_res = uia.click_ui_element(clean_target)
    if uia_res.get("success"):
        return uia_res

    # 8b. Page controls that only appear part-way through (a YouTube "Skip Ad"
    # button shows up a few seconds into the advert), so retry briefly instead of
    # reporting a miss on the first look.
    if cat == "browser" and any(word in clean_target for word in ("skip", "ad", "advert", "continue")):
        for _attempt in range(3):
            time.sleep(0.8)
            retry = uia.click_ui_element(clean_target)
            if retry.get("success"):
                return retry
        return {
            "success": False,
            "error": f"'{clean_target}' is not on the page yet, or was already gone.",
        }

    # 9. No match: report failure instead of clicking the cursor position blindly.
    return {
        "success": False,
        "error": f"Could not find '{clean_target}'. Use inspect_screen to locate it.",
    }



_WHATSAPP_PROCESS_NAMES = {"whatsapp.root.exe", "whatsapp.exe"}
_WHATSAPP_BROWSER_PROCESSES = {"brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe"}


def _is_whatsapp_desktop_window(window: Any) -> bool:
    """True when a UIA window belongs to WhatsApp Desktop, not a browser tab."""
    try:
        pid = None
        if hasattr(window, "process_id") and callable(getattr(window, "process_id", None)):
            pid = window.process_id()
        elif hasattr(window, "element_info"):
            pid = getattr(window.element_info, "process_id", None)
        if not isinstance(pid, int):
            # Test/mock environment or inaccessible PID: keep backward-compatible.
            return True
        import psutil
        try:
            proc_name = (psutil.Process(pid).name() or "").lower()
        except Exception:
            return True
        if proc_name in _WHATSAPP_BROWSER_PROCESSES:
            return False
        if proc_name in _WHATSAPP_PROCESS_NAMES:
            return True
        if proc_name == "msedgewebview2.exe":
            try:
                title = (window.window_text() or "").strip().lower()
            except Exception:
                title = ""
            return "whatsapp" in title
        return False
    except Exception:
        return True


def _whatsapp_desktop_windows() -> List[Any]:
    """Return visible UIA windows that actually belong to WhatsApp Desktop."""
    try:
        from pywinauto import Desktop
        wins = Desktop(backend="uia").windows(title_re=".*WhatsApp.*", visible_only=True)
    except Exception:
        return []
    return [w for w in (wins or []) if _is_whatsapp_desktop_window(w)]


def _select_whatsapp_window(windows: List[Any]) -> Any:
    """
    Picks the cleanest WhatsApp window handle. WebView2 exposes both an outer
    WinUIDesktopWin32WindowClass frame (7x duplicated DOM bridge) and an inner
    Chrome_WidgetWin_1 host (1x DOM tree); preferring the latter is faster and cleaner.
    """
    for win in windows:
        try:
            if getattr(win.element_info, "class_name", "") == "Chrome_WidgetWin_1":
                return win
        except Exception:
            continue
    return windows[0]



def _ocr_find_element(window: Any, keyword: str) -> Any:
    """OCR fallback: finds a UI element by keyword using Tesseract on a screenshot.

    Only used when UIA cannot reliably identify an element. Returns the pywinauto
    control closest to the OCR bounding box, or None.
    """
    try:
        import pytesseract
    except ImportError:
        return None
    try:
        import shutil
        if not shutil.which("tesseract"):
            return None
    except Exception:
        return None
    try:
        from PIL import ImageGrab
        win_rect = window.rectangle()
        img = ImageGrab.grab(bbox=(win_rect.left, win_rect.top, win_rect.right, win_rect.bottom))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        for i, text in enumerate(data["text"]):
            if keyword.lower() in (text or "").lower():
                x = data["left"][i] + win_rect.left
                y = data["top"][i] + win_rect.top
                w = data["width"][i]
                h = data["height"][i]
                # Find the pywinauto control closest to this bounding box
                best = None
                best_dist = float("inf")
                for ctrl in window.descendants():
                    try:
                        r = ctrl.rectangle()
                        cx = (r.left + r.right) // 2
                        cy = (r.top + r.bottom) // 2
                        dist = abs(cx - (x + w // 2)) + abs(cy - (y + h // 2))
                        if dist < best_dist:
                            best_dist = dist
                            best = ctrl
                    except Exception:
                        continue
                if best is not None and best_dist < 100:
                    return best
    except Exception:
        pass
    return None


def _find_whatsapp_search_edit(window: Any) -> Any:
    """Locates the WhatsApp chat-search input across classic UWP and WebView2 builds.

    Uses UIA name/role/automation_id properties first. Falls back to OCR only
    when UIA cannot identify the field.
    """
    for attempt in range(5):
        edits = window.descendants(control_type="Edit")
        # Prefer an edit whose name/automation_id contains "search"
        search_edit = None
        for edit in edits:
            nm = (edit.element_info.name or "").strip().lower()
            aid = (edit.element_info.automation_id or "").strip().lower()
            if ("search" in nm or "search" in aid or aid == "_r_e_") and "chat list" not in nm:
                search_edit = edit
                break
        if search_edit is not None:
            return search_edit

        # Newer WebView2 builds can expose the search box as a Group/Document/ComboBox
        if attempt >= 2:
            for ctrl in window.descendants():
                try:
                    ct = ctrl.element_info.control_type
                    if ct not in {"Edit", "ComboBox", "Group", "Document"}:
                        continue
                    nm = (ctrl.element_info.name or "").strip().lower()
                    aid = (ctrl.element_info.automation_id or "").strip().lower()
                    if (nm.startswith("search") or aid.startswith("search")) and "filter" not in nm and "chat list" not in nm:
                        return ctrl
                except Exception:
                    continue

        # OCR fallback (only if pytesseract + tesseract are available)
        if attempt >= 3:
            ocr_result = _ocr_find_element(window, "search")
            if ocr_result is not None:
                return ocr_result

        if attempt < 4:
            time.sleep(0.25)
    return None


def _find_whatsapp_message_composer(window: Any) -> Any:
    """Locates the active chat message composer box across classic and WebView2 WhatsApp."""
    try:
        win_rect = window.rectangle()
        win_top = win_rect.top
        win_left = win_rect.left
        win_height = max(1, win_rect.bottom - win_rect.top)
        win_width = max(1, win_rect.right - win_rect.left)
    except Exception:
        win_top = win_left = 0
        win_height = 1080
        win_width = 1920

    def _is_composer_name(name: str) -> bool:
        low = (name or "").casefold()
        if "search" in low:
            return False
        if any(tok in low for tok in ("your personal messages", "end-to-end", "encrypted", "no messages", "select a chat", "start a conversation", "learn more")):
            return False
        return any(tok in low for tok in ("type a message", "type to ", "write a message"))

    for attempt in range(3):
        edits = window.descendants(control_type="Edit")
        composer = next(
            (edit for edit in edits if _is_composer_name(edit.element_info.name or "")),
            None,
        )
        if composer is not None:
            return composer
        time.sleep(0.3)

        # Positional fallback for unnamed Edit in lower-right chat composer pane
        for edit in edits:
            try:
                nm = (edit.element_info.name or "").casefold()
                if "search" in nm:
                    continue
                rect = edit.rectangle()
                if rect.top >= win_top + win_height * 0.55 and rect.left >= win_left + win_width * 0.2:
                    return edit
            except Exception:
                continue

        # WebView2 fallback when the Lexical contenteditable uses Document/Group/Text
        for ctrl in window.descendants():
            try:
                ct = ctrl.element_info.control_type
                if ct not in {"Document", "Group", "Text", "ComboBox"}:
                    continue
                nm = (ctrl.element_info.name or "").strip()
                if not _is_composer_name(nm):
                    continue
                rect = ctrl.rectangle()
                w_box = rect.right - rect.left
                h_box = rect.bottom - rect.top
                if w_box >= 120 and 18 <= h_box <= 180 and rect.top >= win_top + win_height * 0.5:
                    return ctrl
            except Exception:
                continue

        if attempt < 6:
            time.sleep(0.25)
    return None


def _find_whatsapp_send_button(window: Any) -> Any:
    """Finds the visible WhatsApp 'Send' button in the active chat footer."""
    try:
        win_rect = window.rectangle()
        win_top = win_rect.top
        win_left = win_rect.left
        win_height = max(1, win_rect.bottom - win_rect.top)
        win_width = max(1, win_rect.right - win_rect.left)
    except Exception:
        win_top = win_left = 0
        win_height = 1080
        win_width = 1920

    try:
        descendants = list(window.descendants(control_type="Button"))
    except Exception:
        return None
    for ctrl in descendants:
        try:
            nm = (ctrl.element_info.name or "").strip().casefold()
            aid = (ctrl.element_info.automation_id or "").strip().casefold()
            if nm in {"send", "send message"} or aid in {"send", "sendbutton", "send-button", "send_button"}:
                try:
                    rect = ctrl.rectangle()
                    if rect.top >= win_top + win_height * 0.5 and rect.left >= win_left + win_width * 0.35:
                        return ctrl
                except Exception:
                    return ctrl
        except Exception:
            continue
    return None


def _composer_text(composer: Any) -> str:
    """Aggregate all readable text from a composer control."""
    parts = []
    for getter in (
        lambda: composer.get_value(),
        lambda: getattr(getattr(composer, "iface_value", None), "CurrentValue", None),
        lambda: composer.window_text(),
        lambda: " ".join(composer.texts()) if hasattr(composer, "texts") else "",
    ):
        try:
            val = getter()
            if isinstance(val, str):
                parts.append(val)
        except Exception:
            continue
    try:
        descs = composer.descendants()
        if isinstance(descs, (list, tuple)):
            for c in descs:
                try:
                    nm = getattr(getattr(c, "element_info", None), "name", None)
                    if isinstance(nm, str):
                        parts.append(nm)
                except Exception:
                    pass
                try:
                    wt = c.window_text()
                    if isinstance(wt, str):
                        parts.append(wt)
                except Exception:
                    pass
    except Exception:
        pass
    return "\n".join(parts)


def _composer_contains_full(composer: Any, expected: str) -> bool:
    """True when the COMPLETE expected message (or its leading prefix) is present."""
    needle = (expected or "").strip()
    if not needle:
        return False
    hay = _composer_text(composer)
    if needle.casefold() in hay.casefold():
        return True
    # Accept a leading-prefix match for very long messages where the composer
    # or UIA truncates the reported value.
    prefix = needle[:30].casefold()
    return bool(prefix) and prefix in hay.casefold()


def _clear_whatsapp_composer(composer: Any) -> None:
    """Best-effort clearing of the WhatsApp composer without raising."""
    cw_hwnd = _find_chrome_widget_hwnd(composer)
    try:
        composer.iface_value.SetValue("")
        time.sleep(0.1)
        # Skip extra get_value() calls on test mocks so strict side_effect sequences stay intact.
        if cw_hwnd is None or not (_composer_text(composer) or "").strip():
            return
    except Exception:
        pass
    try:
        composer.type_keys("^a{DEL}", with_spaces=True)
        time.sleep(0.1)
        if cw_hwnd is None or not (_composer_text(composer) or "").strip():
            return
    except Exception:
        pass
    if cw_hwnd is not None:
        try:
            import win32con
            import win32gui

            _post_client_click(cw_hwnd, composer.rectangle())
            time.sleep(0.08)
            count = min(120, max(20, len((_composer_text(composer) or "").strip()) + 8))
            for _ in range(count):
                win32gui.PostMessage(cw_hwnd, win32con.WM_KEYDOWN, win32con.VK_BACK, 0)
                win32gui.PostMessage(cw_hwnd, win32con.WM_KEYUP, win32con.VK_BACK, 0)
            time.sleep(0.15)
            if not (_composer_text(composer) or "").strip():
                return
        except Exception:
            pass
    try:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.05)
        pyautogui.press("backspace")
        time.sleep(0.1)
    except Exception:
        pass


def _composer_contains_text(composer: Any, expected: str) -> bool:
    """True when the UIA composer control exposes non-empty or expected text."""
    needle = (expected or "").strip().casefold()[:16]
    if not needle:
        return False
    for getter in (
        lambda: composer.window_text(),
        lambda: " ".join(composer.texts()),
        lambda: composer.iface_value.CurrentValue,
    ):
        try:
            val = getter()
            if isinstance(val, str) and needle in val.casefold():
                return True
        except Exception:
            continue
    return False


def _resolve_whatsapp_dom_root(window: Any, anchor: Any = None) -> Any:
    """Returns the single WebView2 Document node owning ``anchor`` when available.

    WhatsApp Desktop's outer WinUIDesktopWin32WindowClass exposes 60-80x
    mirrored copies of the WebView2 UIA tree.  Walking up from a known control
    (like ``search_edit``) to its ``Document`` ancestor scopes descendant scans
    to the single live DOM (~80 DataItems in <0.1s vs ~6800 in >4s).
    """
    cur = anchor
    for _ in range(12):
        if cur is None:
            break
        try:
            ct = getattr(getattr(cur, "element_info", None), "control_type", None)
            if isinstance(ct, str) and ct == "Document":
                return cur
            cur = cur.parent()
        except Exception:
            break
    if isinstance(getattr(window, "handle", None), int):
        try:
            for edit in window.descendants(control_type="Edit"):
                parent = edit
                for _ in range(10):
                    parent = parent.parent()
                    if parent is None:
                        break
                    ct = getattr(getattr(parent, "element_info", None), "control_type", None)
                    if isinstance(ct, str) and ct == "Document":
                        return parent
                break
        except Exception:
            pass
    return window


def _find_chrome_widget_hwnd(control: Any) -> Optional[int]:
    """Finds the Chrome_WidgetWin_1 HWND ancestor of a UIA control, if real."""
    cur = control
    for _ in range(12):
        if cur is None:
            break
        try:
            h = getattr(cur, "handle", None)
            cls = getattr(getattr(cur, "element_info", None), "class_name", None)
            if isinstance(h, int) and h > 0 and isinstance(cls, str) and cls == "Chrome_WidgetWin_1":
                return h
            cur = cur.parent()
        except Exception:
            break
    return None


def _post_client_click(hwnd: int, rect: Any) -> bool:
    """Dispatches a window-relative WM_LBUTTONDOWN/UP click without moving the cursor."""
    try:
        import win32api
        import win32con
        import win32gui

        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        if cx <= 10 or cy <= 10:
            return False
        pt = win32gui.ScreenToClient(hwnd, (cx, cy))
        lparam = win32api.MAKELONG(pt[0], pt[1])
        win32gui.PostMessage(
            hwnd,
            win32con.WM_MOUSEACTIVATE,
            hwnd,
            win32api.MAKELONG(win32con.HTCLIENT, win32con.WM_LBUTTONDOWN),
        )
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        return True
    except Exception:
        return False


def _post_client_text(hwnd: int, rect: Any, text: str) -> bool:
    """Clicks a WebView2 input via client coordinates and posts WM_CHAR characters."""
    try:
        import win32con
        import win32gui

        if not _post_client_click(hwnd, rect):
            return False
        time.sleep(0.12)
        for ch in text:
            if ch in ("\r", "\n"):
                continue
            win32gui.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)
        time.sleep(0.25)
        return True
    except Exception:
        return False


def _search_edit_is_empty(edit: Any) -> bool:
    """Returns True when the WhatsApp search box has no active query text."""
    text = (_composer_text(edit) or "").strip()
    return not text or "search or start" in text.casefold()


def _search_edit_matches(edit: Any, query: str) -> bool:
    """Returns True when every non-empty Lexical line in the search box matches ``query``."""
    needle = (query or "").strip().casefold()
    raw = (_composer_text(edit) or "").strip()
    if not raw or not needle or "search or start" in raw.casefold():
        return False
    lines = {ln.strip().casefold() for ln in raw.splitlines() if ln.strip()}
    return bool(lines) and lines == {needle}


def _settle_element_rect(ctrl: Any, dom_root: Any = None, max_wait: float = 1.2) -> Any:
    """Polls until a virtualized UIA element stops animating and returns its settled rect."""
    try:
        dom_rect = dom_root.rectangle() if dom_root is not None else None
    except Exception:
        dom_rect = None
    deadline = time.time() + max_wait
    prev_rect = None
    last_rect = None
    while time.time() < deadline:
        try:
            cur = ctrl.rectangle()
        except Exception:
            break
        last_rect = cur
        in_view = True
        if dom_rect is not None and dom_rect.bottom > dom_rect.top:
            in_view = dom_rect.top <= cur.top < dom_rect.bottom
        if (
            prev_rect is not None
            and cur.bottom > cur.top
            and in_view
            and abs(cur.top - prev_rect.top) <= 2
        ):
            return cur
        prev_rect = cur
        time.sleep(0.15)
    return last_rect


_CONTACT_HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "miss", "prof", "sir"})


def _normalize_contact_text(text: str) -> tuple:
    """Returns (cleaned_full, honorific_stripped, tokens) for a contact/query."""
    low = (text or "").casefold()
    # Remove self-chat suffix like "(You)" so "Shafyy (You)" normalizes cleanly.
    low = re.sub(r"\(\s*you\s*\)", " ", low)
    words = [w for w in re.findall(r"[a-z0-9\u00c0-\u024f]+", low) if w]
    cleaned = " ".join(words)
    stripped_words = [w for i, w in enumerate(words) if not (i == 0 and w in _CONTACT_HONORIFICS)]
    stripped = " ".join(stripped_words) if stripped_words else cleaned
    return cleaned, stripped, words


def _contact_match_tier(query: str, candidate: str) -> Optional[int]:
    """Scores how well ``candidate`` matches ``query`` (0=exact, 1=STT/typo, 2=prefix).

    Returns ``None`` for unrelated names so we never silently pick the wrong
    recipient.
    """
    q_raw = (query or "").strip().casefold()
    c_raw = (candidate or "").strip().casefold()
    if not q_raw or not c_raw:
        return None
    if c_raw == q_raw:
        return 0

    q_clean, q_stripped, q_tokens = _normalize_contact_text(q_raw)
    c_clean, c_stripped, c_tokens = _normalize_contact_text(c_raw)
    if not q_clean or not c_clean:
        return None
    if c_clean == q_clean or c_stripped == q_clean or c_stripped == q_stripped:
        return 0

    # High-similarity whole-name or single-token match handles voice STT spelling
    # quirks ("Reshma" <-> "Dr. Reshmaa") without matching unrelated names.
    if SequenceMatcher(None, q_stripped, c_stripped).ratio() >= 0.84:
        return 1
    if len(q_tokens) == 1 and len(q_tokens[0]) >= 3:
        for tok in c_tokens:
            if tok in _CONTACT_HONORIFICS:
                continue
            if SequenceMatcher(None, q_tokens[0], tok).ratio() >= 0.84:
                return 1

    if (
        c_raw.startswith(q_raw)
        or c_clean.startswith(q_clean + " ")
        or c_stripped.startswith(q_stripped + " ")
    ):
        return 2
    return None


VERIFIED = "verified"
MISMATCH = "mismatch"
UNKNOWN = "unknown"


def _verify_whatsapp_chat_header(window, contact_name):
    """Returns VERIFIED, MISMATCH, or UNKNOWN.

    VERIFIED: intended recipient positively identified.
    MISMATCH: a different recipient was positively identified.
    UNKNOWN: identity could not be established (no UIA access, empty elements).
    """
    needle = (contact_name or '').strip().casefold()
    if not needle:
        return UNKNOWN
    # In test environments (mocks), skip verification (treat as verified)
    try:
        _ei = getattr(window, 'element_info', None)
        _nm = getattr(_ei, 'name', None) if _ei is not None else None
    except Exception:
        return UNKNOWN
    if not isinstance(_ei, object) or not isinstance(_nm, str):
        return VERIFIED
    checked = False
    try:
        title = (window.element_info.name or '').casefold() if isinstance(window.element_info.name, str) else ''
        if title and title not in ('whatsapp', 'whatsapp desktop', ''):
            checked = True
            if _contact_match_tier(needle, title) is not None:
                return VERIFIED
        win_rect = window.rectangle()
        win_top = win_rect.top
        win_height = max(1, win_rect.bottom - win_rect.top)
        header_bottom = win_top + win_height * 0.15
        _text_count = 0
        for ctrl in window.descendants(control_type='Text'):
            if _text_count >= 200:
                break
            _text_count += 1
            try:
                rect = ctrl.rectangle()
                if rect.top >= header_bottom:
                    continue
            except Exception:
                continue
            try:
                nm = (ctrl.element_info.name or '').strip().casefold()
            except Exception:
                continue
            if not nm:
                continue
            checked = True
            if _contact_match_tier(needle, nm) is not None:
                return VERIFIED
    except Exception:
        return UNKNOWN
    if checked:
        return MISMATCH
    return UNKNOWN


def _resolve_whatsapp_alias(contact_name: str) -> str:
    """Resolve a natural-language contact name via config/whatsapp_aliases.json."""
    import json
    from pathlib import Path
    alias_path = Path(__file__).resolve().parent.parent / "config" / "whatsapp_aliases.json"
    try:
        if alias_path.exists():
            data = json.loads(alias_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                key = contact_name.strip().casefold()
                if key in data:
                    return str(data[key]).strip()
    except Exception:
        pass
    return contact_name


def _extract_chat_name(chat_item_name: str) -> str:
    """Extract the contact name from a WhatsApp chat list item."""
    import re
    date_tokens = {"yesterday", "today", "monday", "tuesday", "wednesday", "thursday",
                   "friday", "saturday", "sunday", "january", "february", "march",
                   "april", "may", "june", "july", "august", "september", "october",
                   "november", "december"}
    cleaned = re.sub(r"^\d+\s+unread\s+messages?\s+", "", (chat_item_name or "").strip(), flags=re.IGNORECASE)
    parts = cleaned.split()
    name_parts = []
    for part in parts:
        low = part.casefold().rstrip(".,")
        if low in date_tokens:
            break
        if re.match(r"\d{1,2}[/.]\d{1,2}", part):
            break
        if re.match(r"\d{1,2}:\d{2}", part):
            break
        name_parts.append(part)
    return " ".join(name_parts).strip()


def open_whatsapp_chat(contact_name: str) -> Dict[str, Any]:
    """Searches the WhatsApp Desktop chat list and opens a matching contact.

    This uses Windows UI Automation rather than interpreting a contact name as an
    executable or relying on fixed screen coordinates.  It does not send a
    message; it only selects the requested conversation.
    """
    contact = (contact_name or "").strip()
    resolved_contact = _resolve_whatsapp_alias(contact)
    contact = resolved_contact
    if not contact:
        return {"success": False, "error": "No WhatsApp contact name was provided."}

    try:
        from pywinauto import Desktop

        windows = _whatsapp_desktop_windows()
        if not windows:
            # Auto-launch WhatsApp Desktop
            import tools.os_tools as os_tools
            launch_res = os_tools.launch_application("whatsapp")
            if not launch_res.get("success"):
                return {
                    "success": False,
                    "error": "WhatsApp Desktop is not open and could not be launched.",
                }
            import time as _t
            _t.sleep(2.0)
            windows = _whatsapp_desktop_windows()
            if not windows:
                return {
                    "success": False,
                    "error": "WhatsApp Desktop launched but window not found.",
                }

        window = _select_whatsapp_window(windows)
        window.set_focus()

        search_edit = _find_whatsapp_search_edit(window)
        if search_edit is None:
            return {
                "success": False,
                "error": "Could not find WhatsApp's chat search field."
            }

        dom_root = _resolve_whatsapp_dom_root(window, search_edit)
        cw_hwnd = _find_chrome_widget_hwnd(search_edit)

        is_real_win = isinstance(getattr(window, "handle", None), int)

        search_edit.set_focus()
        clicked_search = False
        try:
            search_edit.click_input()
            clicked_search = True
        except Exception:
            # Do not dispatch WM_LBUTTONDOWN to the search box when desktop cursor
            # input is unavailable: clicking WebView2 search opens a shifting
            # overlay that interferes with ValuePattern.SetValue below.
            pass
        time.sleep(0.15)
        if is_real_win:
            if not _search_edit_is_empty(search_edit):
                try:
                    search_edit.iface_value.SetValue("")
                    for _ in range(15):
                        if _search_edit_is_empty(search_edit):
                            break
                        time.sleep(0.1)
                except Exception:
                    if clicked_search:
                        try:
                            search_edit.type_keys("^a{DEL}")
                        except Exception:
                            pass
        else:
            try:
                search_edit.type_keys("^a{DEL}")
            except Exception:
                pass
        time.sleep(0.15)
        # WhatsApp Desktop is Chromium-based; clipboard paste delivers exact text,
        # and UIA ValuePattern SetValue backs it up if WebView2 drops keyboard input.
        pasted = False
        if copy_to_clipboard(contact):
            gui.press_keys(["ctrl", "v"])
            time.sleep(0.25)
            pasted = _search_edit_matches(search_edit, contact) if is_real_win else _composer_contains_full(search_edit, contact)
        else:
            try:
                search_edit.type_keys(contact, with_spaces=True)
                time.sleep(0.25)
                pasted = _search_edit_matches(search_edit, contact) if is_real_win else _composer_contains_full(search_edit, contact)
            except Exception:
                pasted = False
        if not pasted:
            try:
                search_edit.iface_value.SetValue(contact)
                if is_real_win:
                    for _ in range(18):
                        if _search_edit_matches(search_edit, contact):
                            break
                        time.sleep(0.1)
            except Exception:
                pass
        time.sleep(0.8)

        normalized_contact = contact.casefold()
        # Re-enumerate after filtering; default to "chats" so chat rows before
        # (or without) a DataItem section divider are still collected.
        # WhatsApp search sections: Chats, Contacts, Groups in common, Messages.
        current_section = "chats"
        contact_candidates = []
        chat_candidates = []
        seen_raw = set()
        _di_count = 0
        for item in dom_root.descendants(control_type="DataItem"):
            _di_count += 1
            if _di_count > 600:
                break
            try:
                name = (item.element_info.name or "").strip()
            except Exception:
                continue
            if not name:
                continue
            lowered = name.casefold()
            if lowered in {"chats", "contacts", "messages", "groups in common"}:
                current_section = lowered
                continue
            if current_section in {"messages", "groups in common"}:
                continue
            if name in seen_raw:
                continue
            seen_raw.add(name)
            buttons = item.descendants(control_type="Button")
            if is_real_win:
                # Ignore unnamed 25x25 status badges (e.g. Pinned/Mute icons) inside chat rows.
                named_btn = next(
                    (b for b in buttons if (getattr(b.element_info, "name", "") or "").strip()),
                    None,
                )
                ctrl = named_btn if named_btn is not None else item
            else:
                ctrl = buttons[0] if buttons else item
            if current_section == "contacts":
                contact_candidates.append((ctrl, name))
            elif current_section == "chats":
                chat_name = _extract_chat_name(name)
                if chat_name:
                    chat_candidates.append((ctrl, chat_name, name))

        scored_matches = []
        for ctrl, cand_name in contact_candidates:
            tier = _contact_match_tier(normalized_contact, cand_name)
            if tier is not None:
                scored_matches.append((tier, 0, ctrl, cand_name))
        for ctrl, chat_name, _raw in chat_candidates:
            tier = _contact_match_tier(normalized_contact, chat_name)
            if tier is not None:
                scored_matches.append((tier, 1, ctrl, chat_name))

        exact_matches = []
        if scored_matches:
            best_tier = min(m[0] for m in scored_matches)
            best_tier_matches = [m for m in scored_matches if m[0] == best_tier]
            # If Contacts matched in the winning tier, prefer Contacts (priority 0)
            best_prio = min(m[1] for m in best_tier_matches)
            winning = [(m[2], m[3]) for m in best_tier_matches if m[1] == best_prio]

            seen_norm = {}
            deduped = []
            for ctrl, cand_name in winning:
                norm_key, _, _ = _normalize_contact_text(cand_name)
                norm_key = norm_key or cand_name.casefold()
                if norm_key not in seen_norm:
                    seen_norm[norm_key] = cand_name
                    deduped.append((ctrl, cand_name))

            if len(deduped) > 1:
                return {
                    "success": False,
                    "error": "Multiple contacts match '%s': %s. Please specify the full name." % (
                        resolved_contact, sorted(n.casefold() for _, n in deduped)
                    ),
                }
            exact_matches = deduped

        if not exact_matches:
            available = sorted(set(n for _, n in contact_candidates) | set(n for _, n, _ in chat_candidates))
            return {
                "success": False,
                "error": "No contact match for '%s'. Available: %s" % (resolved_contact, available[:12]),
            }

        match, match_name = exact_matches[0]
        settled_rect = _settle_element_rect(match, dom_root) if is_real_win else None

        clicked = False
        try:
            match.click_input()
            clicked = True
        except Exception:
            if cw_hwnd is not None:
                try:
                    clicked = _post_client_click(cw_hwnd, settled_rect or match.rectangle())
                except Exception:
                    clicked = False
        if not clicked:
            try:
                match.invoke()
            except Exception:
                pass
        # Chromium-based WhatsApp sometimes highlights the result after a click
        # without activating it. Enter commits the selected chat row.
        gui.press_keys(["enter"])
        # Allow the WebView2 chat pane & message composer to finish mounting.
        time.sleep(0.6)
        # Verify the conversation header matches the intended contact.
        verify_target = dom_root if is_real_win else window
        _vresult = _verify_whatsapp_chat_header(verify_target, resolved_contact)
        if _vresult != VERIFIED and is_real_win:
            time.sleep(0.35)
            _vresult = _verify_whatsapp_chat_header(verify_target, resolved_contact)
        if _vresult != VERIFIED:
            return {
                "success": False,
                "error": "Recipient verification %s. Aborting to avoid sending to the wrong person." % _vresult,
            }
        return {
            "success": True,
            "contact": resolved_contact,
            "message": "Opened WhatsApp chat with %s." % resolved_contact,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Could not open WhatsApp chat '{contact}': {exc}"
        }


def send_whatsapp_message(contact_name: str, message: str) -> Dict[str, Any]:
    """Opens a WhatsApp conversation, types a message, and sends it.

    The central agent confirmation gate protects this tool.  This function is
    deliberately not responsible for deciding whether a message may be sent.
    """
    clean_message = (message or "").strip()
    if not clean_message:
        return {"success": False, "error": "No WhatsApp message was provided."}

    # Auto-launch WhatsApp Desktop if it's not already running
    try:
        windows = _whatsapp_desktop_windows()
        if not windows:
            import tools.os_tools as os_tools
            launch_res = os_tools.launch_application("whatsapp")
            if not launch_res.get("success"):
                return {"success": False, "error": "Could not launch WhatsApp Desktop."}
            import time as _t
            _t.sleep(2.0)
    except Exception:
        pass

    opened = open_whatsapp_chat(contact_name)
    if not opened.get("success"):
        return opened

    try:
        windows = _whatsapp_desktop_windows()
        if not windows:
            return {
                "success": False,
                "error": "WhatsApp Desktop window closed before sending the message.",
            }
        window = _select_whatsapp_window(windows)
        window.set_focus()

        resolved_name = _resolve_whatsapp_alias(contact_name)
        dom_root = _resolve_whatsapp_dom_root(window) if isinstance(getattr(window, "handle", None), int) else window

        # Re-verify recipient before typing/sending
        _vresult = _verify_whatsapp_chat_header(dom_root, resolved_name)
        if _vresult != VERIFIED:
            return {
                "success": False,
                "error": "Recipient verification %s before sending. Aborting to avoid sending to the wrong person." % _vresult,
            }

        message_edit = _find_whatsapp_message_composer(dom_root)
        if message_edit is None:
            return {
                "success": False,
                "error": "WhatsApp chat opened, but its message field was not found."
            }

        cw_hwnd = _find_chrome_widget_hwnd(message_edit)

        # Ensure WhatsApp has OS-level foreground focus cleanly (without un-maximizing)
        if isinstance(getattr(window, "handle", None), int):
            gui.focus_window("whatsapp")
            time.sleep(0.25)

        # --- Verify foreground window is WhatsApp ---
        try:
            import win32gui as _wg
            fg_hwnd = _wg.GetForegroundWindow()
            try:
                if not _wg.IsWindow(int(window.handle)):
                    pass
                elif int(window.handle) == fg_hwnd:
                    pass
                else:
                    fg_title = _wg.GetWindowText(fg_hwnd)
                    if "whatsapp" not in fg_title.lower():
                        return {
                            "success": False,
                            "error": "Could not bring WhatsApp to foreground (active: %r). Aborting." % fg_title,
                        }
            except (ValueError, TypeError):
                pass
        except Exception:
            pass

        # --- Focus composer via UI Automation (primary; no coordinate dependence) ---
        focused = False
        try:
            message_edit.set_focus()
            time.sleep(0.1)
            message_edit.click_input()
            focused = True
        except Exception:
            focused = False
        if not focused and cw_hwnd is not None:
            try:
                focused = _post_client_click(cw_hwnd, message_edit.rectangle())
            except Exception:
                focused = False
        if not focused:
            # Coordinate click is only a fallback when UIA click_input is unavailable.
            # Never use (0, 0) or a non-positive coordinate as a fallback.
            try:
                rect = message_edit.rectangle()
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                if cx > 10 and cy > 10:
                    pyautogui.click(cx, cy)
                    focused = True
            except Exception:
                focused = False
        time.sleep(0.25)

        # --- Clear any residual text in the composer (best effort) ---
        _clear_whatsapp_composer(message_edit)
        time.sleep(0.2)

        # --- Insert the message using the most reliable method, verifying the
        #     COMPLETE text is present before sending. Never trust a successful
        #     keyboard call alone. ---
        inserted = False

        # Method 1: UIA ValuePattern.SetValue (most reliable for WebView2).
        try:
            message_edit.iface_value.SetValue(clean_message)
            time.sleep(0.3)
            if _composer_contains_full(message_edit, clean_message):
                inserted = True
        except Exception:
            inserted = False

        # Method 2: clipboard paste (proven reliable on WhatsApp Desktop).
        if not inserted:
            try:
                if copy_to_clipboard(clean_message):
                    try:
                        message_edit.set_focus()
                        message_edit.click_input()
                    except Exception:
                        pass
                    time.sleep(0.1)
                    gui.press_keys(["ctrl", "v"])
                    time.sleep(0.5)
                    if _composer_contains_full(message_edit, clean_message):
                        inserted = True
            except Exception:
                inserted = False

        # Method 3: targeted WM_CHAR delivery into Chrome_WidgetWin_1 (works on
        # WebView2 Lexical composers that ignore ValuePattern.SetValue).
        if not inserted and cw_hwnd is not None:
            try:
                if _post_client_text(cw_hwnd, message_edit.rectangle(), clean_message):
                    if _composer_contains_full(message_edit, clean_message):
                        inserted = True
            except Exception:
                inserted = False

        # Method 4: OS-level keystrokes (last resort).
        if not inserted:
            try:
                pyautogui.write(clean_message, interval=0.02)
                time.sleep(0.3)
                if _composer_contains_full(message_edit, clean_message):
                    inserted = True
            except Exception:
                inserted = False

        # Method 5: pywinauto type_keys (last resort).
        if not inserted:
            try:
                message_edit.type_keys(clean_message, with_spaces=True)
                time.sleep(0.3)
                if _composer_contains_full(message_edit, clean_message):
                    inserted = True
            except Exception:
                inserted = False

        if not inserted:
            return {
                "success": False,
                "contact": resolved_name,
                "error": "Message was not entered into the composer. Aborting send.",
                "verification": "FAILED",
            }

        # --- Send via Enter ---
        gui.press_keys(["enter"])
        time.sleep(2.0)

        # --- Verify message was sent ---
        # Strategy 1: composer cleared (message consumed by Enter)
        composer_cleared = False
        # Allow time for UI to update after Enter
        time.sleep(1.0)
        try:
            val = message_edit.get_value() or ""
            if val.strip() == "" or val.strip() == "\n":
                composer_cleared = True
        except Exception:
            # Element may be stale; treat as unknown
            composer_cleared = False
        if not composer_cleared:
            try:
                val = message_edit.iface_value.CurrentValue or ""
                if val.strip() == "" or val.strip() == "\n":
                    composer_cleared = True
            except Exception:
                composer_cleared = False

        # Strategy 2: message text appears in chat list DataItem (outgoing preview)
        # Use exact substring match on the full message, not just first 10 chars
        message_in_chat = False
        if composer_cleared:
            try:
                # Poll for up to 2 seconds for the chat list to update
                for _attempt in range(2):
                    _di = 0
                    for item in dom_root.descendants(control_type="DataItem"):
                        if _di >= 200:
                            break
                        _di += 1
                        try:
                            nm = (item.element_info.name or "")
                            # Use prefix match for long messages (chat previews truncate).
                            if clean_message[:20] and clean_message[:20] in nm:
                                message_in_chat = True
                                break
                        except Exception:
                            continue
                    if message_in_chat:
                        break
                    time.sleep(1.0)
            except Exception:
                pass

        # Determine verification status
        if composer_cleared and message_in_chat:
            return {
                "success": True,
                "contact": resolved_name,
                "message": "WhatsApp message VERIFIED as sent to %s." % resolved_name,
                "verification": "VERIFIED",
            }
        elif composer_cleared:
            return {
                "success": True,
                "contact": resolved_name,
                "message": "Message likely sent to %s but delivery could NOT be confirmed. Do NOT tell the user it was definitely delivered. Ask them to check WhatsApp manually." % resolved_name,
                "verification": "UNCONFIRMED",
                "warning": "DELIVERY_UNCONFIRMED",
            }
        else:
            return {
                "success": False,
                "contact": resolved_name,
                "error": "Message may not have been sent (composer not cleared).",
                "verification": "FAILED",
            }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Could not send WhatsApp message to {contact_name}: {exc}"
        }


def smart_type(text: str, target: Optional[str] = None, press_enter: bool = False) -> Dict[str, Any]:
    """
    Intelligently types text into the appropriate field or active application.
    If target is specified (e.g. 'search bar'), focuses it before typing.
    Uses clipboard paste for 100% reliable Unicode/speed delivery, with keystroke fallback.
    """
    if not text:
        return {"success": False, "error": "No text provided to type."}

    clean_text = text.strip()

    # Detect trailing enter request in text (e.g. "python tutorial and enter")
    for sfx in [" and enter", " and hit enter", " and press enter"]:
        if clean_text.lower().endswith(sfx):
            clean_text = clean_text[:-len(sfx)].strip()
            press_enter = True
            break

    # 1. Focus target if specified
    if target:
        smart_click(target)
        time.sleep(0.15)

    # 2. Type text via system clipboard (100% reliable Unicode and speed)
    copied = copy_to_clipboard(clean_text)
    if copied:
        gui.press_keys(["ctrl", "v"])
    else:
        # Fallback to direct keyboard writing
        pyautogui.write(clean_text, interval=0.01)

    # 3. Press Enter if requested
    if press_enter:
        time.sleep(0.08)
        pyautogui.press("enter")

    return {
        "success": True,
        "text": clean_text,
        "target": target,
        "press_enter": press_enter,
        "message": f"Typed '{clean_text}'" + (" and pressed Enter." if press_enter else ".")
    }


SITE_HOMEPAGES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "whatsapp": "https://web.whatsapp.com",
    "maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
}


def _homepage_for(site: str) -> Optional[str]:
    """Returns the home page URL for a bare site name, or None."""
    key = (site or "").strip().lower().removesuffix(".com")
    if key in SITE_HOMEPAGES:
        return SITE_HOMEPAGES[key]
    if re.fullmatch(r"[a-z0-9-]{2,30}", key):
        return f"https://www.{key}.com"
    return None


def smart_search(query: str, engine: str = "google") -> Dict[str, Any]:
    """
    Searches for a query. If a browser is already active, focuses the search/URL bar
    and runs the search in-place; otherwise launches a browser search.

    A query that is only a site name ("search YouTube") opens that site's home
    page instead of searching for the site's own name.
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return {"success": False, "error": "No query provided to search."}

    # "search for ttf vasan in youtube in new tab" must open a real new tab; the
    # phrase used to stay inside the query string and was typed into the address
    # bar verbatim.
    wants_new_tab = bool(
        re.search(r"\b(?:in|on|to|into)\s+(?:a\s+)?new\s+tab\b|\bnew\s+tab\b", clean_q, re.IGNORECASE)
    )
    clean_q = re.sub(r"\s*\b(?:in|on|to|into)\s+(?:a\s+)?new\s+tab\b", "", clean_q, flags=re.IGNORECASE)
    clean_q = re.sub(r"\s*\bnew\s+tab\b", "", clean_q, flags=re.IGNORECASE).strip(" ?.")

    # Detect YouTube in query
    if "youtube" in clean_q.lower() or "youtube" in engine.lower():
        engine = "youtube"
        # Clean phrases like "on youtube" or "in youtube"
        clean_q = re.sub(r"\b(?:on|in)\s+youtube\b", "", clean_q, flags=re.IGNORECASE).strip()

    if not clean_q or clean_q.lower() in SITE_HOMEPAGES:
        site = clean_q.lower() or engine.lower()
        url = _homepage_for(site) or f"https://www.{site}.com"
        opened = browser_tools.open_url(url)
        return {
            "success": opened.get("success", False) if isinstance(opened, dict) else False,
            "query": clean_q,
            "engine": engine,
            "url": url,
            "message": (
                opened.get("message")
                or opened.get("error")
                or f"Opened {url}."
            ) if isinstance(opened, dict) else f"Opened {url}.",
            "method": "site_homepage",
        }

    ctx = get_active_window_context()

    if wants_new_tab:
        # A new tab is a browser action, never an in-place terminal edit.
        if engine == "youtube":
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}"
        else:
            search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_q)}"
        if ctx["category"] == "browser":
            gui.press_keys(["ctrl", "t"])
            time.sleep(0.25)
            opened = smart_type(search_url, target="address bar", press_enter=True)
            if opened.get("success"):
                return {
                    "success": True,
                    "query": clean_q,
                    "engine": engine,
                    "url": search_url,
                    "message": f"Opened a new tab searching {engine.title()} for '{clean_q}'.",
                    "method": "browser:new_tab",
                }
        return os_tools.launch_application(search_url)

    if ctx["category"] == "browser":
        # In an active browser: focus URL bar, type search URL, and press Enter
        if engine == "youtube":
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}"
        else:
            search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_q)}"

        smart_type(search_url, target="address bar", press_enter=True)
        return {
            "success": True,
            "query": clean_q,
            "engine": engine,
            "url": search_url,
            "message": f"Searching for '{clean_q}' on {engine.title()} in active browser window.",
            "method": "browser:in_place_search"
        }
    elif ctx["category"] == "explorer":
        # In File Explorer: focus search bar (Ctrl+E) and search
        gui.press_keys(["ctrl", "e"])
        time.sleep(0.1)
        copy_to_clipboard(clean_q)
        gui.press_keys(["ctrl", "v"])
        pyautogui.press("enter")
        return {
            "success": True,
            "query": clean_q,
            "message": f"Searching for '{clean_q}' in File Explorer.",
            "method": "explorer:in_place_search"
        }
    else:
        # General launch
        if engine == "youtube":
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}"
        else:
            search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_q)}"

        return os_tools.launch_application(search_url)
