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
import tools.whatsapp_desktop as _wa


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

    # Spotify-specific controls (desktop app)
    if "spotify" in (ctx.get("title") or "").lower():
        if any(kw in clean_target for kw in ["search", "find"]):
            gui.press_keys(["ctrl", "k"])
            return {"success": True, "message": "Focused Spotify search.", "method": "hotkey:ctrl+k"}
        if any(kw in clean_target for kw in ["first result", "first song", "top result", "play", "select"]):
            gui.press_keys(["enter"])
            return {"success": True, "message": "Activated first Spotify result.", "method": "keyboard:enter"}
        if clean_target in ["pause", "resume", "play pause"]:
            gui.press_keys(["space"])
            return {"success": True, "message": "Toggled Spotify playback.", "method": "hotkey:space"}

    # 2. Search Bar / Address Bar / URL Bar
    if any(kw in clean_target for kw in ["search", "address", "url", "input", "find"]):
        if cat == "browser":
            # Focus browser address/search bar (Ctrl+L)
            gui.press_keys(["ctrl", "l"])
            return {"success": True, "message": "Focused browser search/address bar.", "method": "hotkey:ctrl+l"}
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
            return {"success": True, "message": "Focused active address/search bar.", "method": "hotkey:alt+d"}

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
        return {"success": True, "message": f"Executed '{clean_target}'.", "method": "hotkey"}

    # 4b. Download / Save (common in browsers, WhatsApp, dialogs)
    if any(kw in clean_target for kw in ["download", "save image", "save file", "save as"]):
        # Try UIA button first
        dl_res = uia.click_ui_element("download")
        if dl_res.get("success"):
            return dl_res
        dl_res = uia.click_ui_element("save")
        if dl_res.get("success"):
            return dl_res
        # Fallback: common save shortcut
        gui.press_keys(["ctrl", "s"])
        return {"success": True, "message": "Saved file.", "method": "hotkey:ctrl+s"}
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
        return {"success": True, "message": f"Executed action '{clean_target}'.", "method": "hotkey"}

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

    # 7b. Image/media click (e.g. WhatsApp chat image)
    if clean_target in ["image", "photo", "picture", "media", "thumbnail"]:
        try:
            ctx2 = get_active_window_context()
            win = uia.get_active_window() if hasattr(uia, "get_active_window") else None
        except Exception:
            win = None
        # Try UIA image controls first
        try:
            from pywinauto import Desktop
            active = Desktop(backend="uia").active_window()
            imgs = []
            for child in active.descendants():
                try:
                    ct = (getattr(child.element_info, "control_type", "") or "").lower()
                    if ct in ("image", "picture"):
                        imgs.append(child)
                except Exception:
                    continue
            if not imgs:
                # Fallback: largest non-text element in the lower 60% of the window.
                try:
                    win_rect = active.rectangle()
                    win_h = win_rect.bottom - win_rect.top
                    win_w = win_rect.right - win_rect.left
                    min_top = win_rect.top + int(win_h * 0.3)
                    for child in active.descendants():
                        try:
                            ct = (getattr(child.element_info, "control_type", "") or "").lower()
                            if ct in ("text", "edit", "scrollbar", "titlebar", "menubar"):
                                continue
                            rect = child.rectangle()
                            if rect.top < min_top:
                                continue
                            area = (rect.right - rect.left) * (rect.bottom - rect.top)
                            if area < 4000:
                                continue
                            imgs.append(child)
                        except Exception:
                            continue
                except Exception:
                    pass
            if imgs:
                # Pick the largest image (likely the chat media thumbnail)
                def _area(c):
                    try:
                        r = c.rectangle()
                        return (r.right - r.left) * (r.bottom - r.top)
                    except Exception:
                        return 0
                imgs.sort(key=_area, reverse=True)
                target_img = imgs[0]
                try:
                    if target_img.iface_invoke is not None:
                        target_img.iface_invoke.Invoke()
                        return {"success": True, "message": "Opened image via Invoke.", "method": "uia:invoke"}
                except Exception:
                    pass
                try:
                    target_img.click_input()
                    return {"success": True, "message": "Clicked image via UIA.", "method": "uia:click"}
                except Exception:
                    pass
                try:
                    rect = target_img.rectangle()
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    if cx > 0 and cy > 0:
                        gui.click_coordinate(cx, cy)
                        return {"success": True, "message": f"Clicked image at ({cx},{cy}).", "method": "coord"}
                except Exception:
                    pass
        except Exception:
            pass

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



_WHATSAPP_PROCESS_NAMES = _wa._WHATSAPP_PROCESS_NAMES
_WHATSAPP_BROWSER_PROCESSES = _wa._WHATSAPP_BROWSER_PROCESSES


def _is_whatsapp_desktop_window(window: Any) -> bool:
    return _wa.is_whatsapp_desktop_window(window)


def _whatsapp_desktop_windows() -> List[Any]:
    return _wa.whatsapp_desktop_windows()


def _select_whatsapp_window(windows: List[Any]) -> Any:
    return _wa.select_whatsapp_window(windows)


def _ocr_find_element(window: Any, keyword: str) -> Any:
    return _wa._ocr_find_element(window, keyword)


def _find_whatsapp_search_edit(window: Any) -> Any:
    return _wa.find_whatsapp_search_edit(window)


def _find_whatsapp_message_composer(window: Any) -> Any:
    return _wa.find_whatsapp_message_composer(window)


def _find_whatsapp_send_button(window: Any) -> Any:
    return _wa.find_whatsapp_send_button(window)


def _composer_text(composer: Any) -> str:
    return _wa._composer_text(composer)


def _composer_contains_full(composer: Any, expected: str) -> bool:
    return _wa._composer_contains_full(composer, expected)


def _clear_whatsapp_composer(composer: Any) -> None:
    return _wa._clear_whatsapp_composer(composer)


def _composer_contains_text(composer: Any, expected: str) -> bool:
    return _composer_contains_full(composer, expected)


def _resolve_whatsapp_dom_root(window: Any, anchor: Any = None) -> Any:
    return _wa._resolve_whatsapp_dom_root(window, anchor)


def _find_chrome_widget_hwnd(control: Any) -> Optional[int]:
    return _wa._find_chrome_widget_hwnd(control)


def _post_client_click(hwnd: int, rect: Any) -> bool:
    return _wa._post_client_click(hwnd, rect)


def _post_client_text(hwnd: int, rect: Any, text: str) -> bool:
    return _wa._post_client_text(hwnd, rect, text)


def _search_edit_is_empty(edit: Any) -> bool:
    return _wa._search_edit_is_empty(edit)


def _search_edit_matches(edit: Any, query: str) -> bool:
    return _wa._search_edit_matches(edit, query)


def _settle_element_rect(ctrl: Any, dom_root: Any = None, max_wait: float = 1.2) -> Any:
    return _wa._settle_element_rect(ctrl, dom_root, max_wait)


def _normalize_contact_text(text: str) -> tuple:
    return _wa._normalize_contact_text(text)


def _contact_match_tier(query: str, candidate: str) -> Optional[int]:
    return _wa.contact_match_tier(query, candidate)


VERIFIED = _wa.VERIFIED
MISMATCH = _wa.MISMATCH
UNKNOWN = _wa.UNKNOWN


def _verify_whatsapp_chat_header(window, contact_name):
    return _wa.verify_whatsapp_chat_header(window, contact_name)


def _resolve_whatsapp_alias(contact_name: str) -> str:
    return _wa._resolve_whatsapp_alias(contact_name)


def _extract_chat_name(chat_item_name: str) -> str:
    return _wa._extract_chat_name(chat_item_name)


def open_whatsapp() -> Dict[str, Any]:
    """Launches or focuses the real WhatsApp Desktop application and verifies window readiness."""
    return _wa.open_whatsapp()


def get_whatsapp_state() -> Dict[str, Any]:
    """Inspects live WhatsApp Desktop UI and returns both observed UI state and task context."""
    return _wa.get_whatsapp_state()


def get_active_chat() -> Dict[str, Any]:
    """Returns the verified currently active conversation in WhatsApp Desktop."""
    return _wa.get_active_chat()


def get_unread_chats(limit: int = 15) -> Dict[str, Any]:
    """Lists unread WhatsApp Desktop conversations."""
    return _wa.get_unread_chats(limit=limit)


def search_contacts(query: str) -> Dict[str, Any]:
    """Searches WhatsApp Desktop contacts/chats and stores ordered results in task state."""
    return _wa.search_contacts(query)


def resolve_contact(reference: str) -> Dict[str, Any]:
    """Resolves contextual contact references ('first chat', 'other Shafi', 'his') using task state."""
    return _wa.resolve_contact(reference)


def open_whatsapp_chat(contact_name: str) -> Dict[str, Any]:
    """Opens and verifies a WhatsApp Desktop chat conversation."""
    return _wa.open_whatsapp_chat(contact_name)


def open_chat(contact: str) -> Dict[str, Any]:
    """Alias for open_whatsapp_chat."""
    return _wa.open_whatsapp_chat(contact)


def get_chat_messages(contact: Optional[str] = None, limit: int = 15) -> Dict[str, Any]:
    """Reads accessible messages from the specified or currently active WhatsApp conversation."""
    return _wa.get_chat_messages(contact=contact, limit=limit)


def get_latest_message(contact: Optional[str] = None, direction: str = "any") -> Dict[str, Any]:
    """Reads the latest accessible message from an active or named WhatsApp conversation."""
    return _wa.get_latest_message(contact=contact, direction=direction)


def get_contact_information(contact: Optional[str] = None) -> Dict[str, Any]:
    """Opens Contact Info and reads accessible details for the active or requested WhatsApp contact."""
    return _wa.get_contact_information(contact=contact)


def search_messages(query: str, limit: int = 10) -> Dict[str, Any]:
    """Searches WhatsApp messages for a keyword and stores matching conversations."""
    return _wa.search_messages(query, limit=limit)


def view_status(contact: Optional[str] = None) -> Dict[str, Any]:
    """Opens WhatsApp Status tab and inspects accessible updates."""
    return _wa.view_status(contact=contact)


def search_media(query: Optional[str] = None) -> Dict[str, Any]:
    """Inspects accessible media attachments in the active WhatsApp Desktop view."""
    return _wa.search_media(query=query)


def send_whatsapp_message(contact_name: str, message: str) -> Dict[str, Any]:
    """Opens/verifies a WhatsApp Desktop conversation and sends the verified message."""
    return _wa.send_whatsapp_message(contact_name, message)


def send_message(contact: str, message: str) -> Dict[str, Any]:
    """Alias for send_whatsapp_message."""
    return _wa.send_whatsapp_message(contact, message)


def reply_to_message(contact: Optional[str] = None, message: str = "") -> Dict[str, Any]:
    """Replies to the active or resolved WhatsApp conversation using verified delivery."""
    return _wa.reply_to_message(contact=contact, message=message)


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
