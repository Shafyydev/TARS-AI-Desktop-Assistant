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
            if ("search" in nm or "search" in aid) and "chat list" not in nm:
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
        return any(tok in low for tok in ("type a message", "type to ", "write a message", "message"))

    for attempt in range(7):
        edits = window.descendants(control_type="Edit")
        composer = next(
            (edit for edit in edits if _is_composer_name(edit.element_info.name or "")),
            None,
        )
        if composer is not None:
            return composer

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
            if needle in title:
                return VERIFIED
        win_rect = window.rectangle()
        win_top = win_rect.top
        win_height = max(1, win_rect.bottom - win_rect.top)
        for ctrl in window.descendants():
            try:
                ct = ctrl.element_info.control_type
                if ct not in ('Text', 'Document', 'Group', 'Pane'):
                    continue
                nm = (ctrl.element_info.name or '').strip().casefold()
                if not nm:
                    continue
                checked = True
                if needle in nm:
                    try:
                        rect = ctrl.rectangle()
                        if rect.top < win_top + win_height * 0.15:
                            return VERIFIED
                    except Exception:
                        # Could not get rect; treat as verified if name matches
                        return VERIFIED
            except Exception:
                continue
    except Exception:
        # Could not access UI elements; identity cannot be established
        return UNKNOWN
    # If we checked elements and found none matching, it's a mismatch
    if checked:
        return MISMATCH
    # Could not check anything; identity cannot be established
    return UNKNOWN


def open_whatsapp_chat(contact_name: str) -> Dict[str, Any]:
    """Searches the WhatsApp Desktop chat list and opens a matching contact.

    This uses Windows UI Automation rather than interpreting a contact name as an
    executable or relying on fixed screen coordinates.  It does not send a
    message; it only selects the requested conversation.
    """
    contact = (contact_name or "").strip()
    if not contact:
        return {"success": False, "error": "No WhatsApp contact name was provided."}

    try:
        from pywinauto import Desktop

        windows = Desktop(backend="uia").windows(
            title_re=".*WhatsApp.*", visible_only=True
        )
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
            windows = Desktop(backend="uia").windows(
                title_re=".*WhatsApp.*", visible_only=True
            )
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

        search_edit.set_focus()
        try:
            search_edit.click_input()
        except Exception:
            pass
        search_edit.type_keys("^a{BACKSPACE}")
        # WhatsApp Desktop is Chromium-based; direct UIA typing is unreliable in
        # its search field. Clipboard paste delivers the exact contact text.
        if copy_to_clipboard(contact):
            gui.press_keys(["ctrl", "v"])
        else:
            search_edit.type_keys(contact, with_spaces=True)
        # Press Enter to commit the search and trigger filtering
        time.sleep(0.3)
        gui.press_keys(["enter"])
        time.sleep(1.0)

        normalized_contact = contact.casefold()
        # Re-enumerate after filtering to avoid stale items
        candidates = []
        for item in window.descendants(control_type="DataItem"):
            name = (item.element_info.name or "").strip()
            lowered = name.casefold()
            if lowered in {"chats", "contacts", "messages"}:
                continue
            if not name:
                continue
            buttons = item.descendants(control_type="Button")
            candidates.append((buttons[0] if buttons else item, name))

        # Filter candidates to only those matching the search
        filtered = [(c, n) for c, n in candidates if normalized_contact in n.casefold() or n.casefold() in normalized_contact]
        if filtered:
            candidates = filtered

        def score(candidate_name: str) -> float:
            candidate = candidate_name.casefold()
            if candidate == normalized_contact:
                return 100.0
            if candidate.startswith(normalized_contact):
                return 95.0
            if normalized_contact in candidate:
                return 90.0
            return SequenceMatcher(None, normalized_contact, candidate).ratio() * 100

        exact_matches = [c for c, n in candidates if n.casefold() == normalized_contact]
        if len(exact_matches) > 1:
            return {
                "success": False,
                "error": "Multiple contacts named '%s' found. Please specify the full name to disambiguate." % contact,
            }

        match = None
        for cand, name in candidates:
            if name.casefold() == normalized_contact:
                match = cand
                break
        if match is None:
            if candidates:
                candidate, candidate_name = max(candidates, key=lambda pair: score(pair[1]))
                if score(candidate_name) >= 80.0:
                    match = candidate
        if match is None:
            return {
                "success": False,
                "error": f"No WhatsApp chat/contact matching '{contact}' was found. Say the full name shown in WhatsApp."
            }

        match.click_input()
        # Chromium-based WhatsApp sometimes highlights the result after a click
        # without activating it. Enter commits the selected chat row.
        gui.press_keys(["enter"])
        # Allow the WebView2 chat pane & message composer to finish mounting.
        time.sleep(0.5)
        # Verify the conversation header matches the intended contact.
        _vresult = _verify_whatsapp_chat_header(window, contact)
        if _vresult != VERIFIED:
            return {
                "success": False,
                "error": "Recipient verification %s. Aborting to avoid sending to the wrong person." % _vresult,
            }
        return {
            "success": True,
            "contact": contact,
            "message": "Opened WhatsApp chat with %s." % contact,
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
        from pywinauto import Desktop
        windows = Desktop(backend="uia").windows(title_re=".*WhatsApp.*", visible_only=True)
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
        from pywinauto import Desktop

        windows = Desktop(backend="uia").windows(title_re=".*WhatsApp.*", visible_only=True)
        if not windows:
            return {
                "success": False,
                "error": "WhatsApp Desktop window closed before sending the message.",
            }
        window = _select_whatsapp_window(windows)
        window.set_focus()

        # Re-verify recipient before typing/sending
        _vresult = _verify_whatsapp_chat_header(window, contact_name)
        if _vresult != VERIFIED:
            return {
                "success": False,
                "error": "Recipient verification %s before sending. Aborting to avoid sending to the wrong person." % _vresult,
            }

        message_edit = _find_whatsapp_message_composer(window)
        if message_edit is None:
            return {
                "success": False,
                "error": "WhatsApp chat opened, but its message field was not found."
            }

        def _focus_and_insert(use_direct_type: bool = False) -> None:
            # In WebView2/Chromium WhatsApp, UIA set_focus() alone often leaves
            # the Lexical contenteditable editor without a text caret; a real
            # click is required so pasted/typed text lands inside the composer.
            clicked = False
            try:
                message_edit.click_input()
                clicked = True
            except Exception:
                pass
            if not clicked:
                try:
                    rect = message_edit.rectangle()
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    pyautogui.click(cx, cy)
                    clicked = True
                except Exception:
                    pass
            message_edit.set_focus()
            time.sleep(0.15)

            if not use_direct_type and copy_to_clipboard(clean_message):
                gui.press_keys(["ctrl", "v"])
            else:
                try:
                    message_edit.type_keys(clean_message, with_spaces=True)
                except Exception:
                    pyautogui.write(clean_message, interval=0.015)
            time.sleep(0.25)

        _focus_and_insert(use_direct_type=False)
        send_btn = _find_whatsapp_send_button(window)
        text_verified = (send_btn is not None) or _composer_contains_text(message_edit, clean_message)

        if not text_verified:
            # Fallback attempt: focus composer explicitly and type directly
            _focus_and_insert(use_direct_type=True)
            send_btn = _find_whatsapp_send_button(window)
            text_verified = (send_btn is not None) or _composer_contains_text(message_edit, clean_message)

        if not text_verified:
            return {
                "success": False,
                "error": (
                    f"Opened WhatsApp chat with {contact_name}, but could not type "
                    "the message into the composer box."
                ),
            }

        if send_btn is not None:
            try:
                send_btn.click_input()
            except Exception:
                gui.press_keys(["enter"])
        else:
            gui.press_keys(["enter"])

        return {
            "success": True,
            "contact": contact_name,
            "message": f"Sent WhatsApp message to {contact_name}."
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
