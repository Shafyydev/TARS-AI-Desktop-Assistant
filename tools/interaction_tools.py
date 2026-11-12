"""Smart interaction engine for TARS: Context-aware clicking, typing, and in-app navigation."""
import os
import sys
import re
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional
import pyautogui

import tools.gui_tools as gui
import tools.ui_automation as uia
import tools.browser_automation as browser
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


def get_active_window_context() -> Dict[str, Any]:
    """
    Returns the title, category, and process info of the currently active foreground window.
    Categories: 'browser', 'explorer', 'terminal', 'editor', 'player', 'unknown'.
    """
    active = uia.get_active_window()
    title = active.get("title", "").lower() if active.get("success") else ""
    class_name = active.get("class_name", "")

    category = "unknown"
    if any(b in title for b in ["brave", "chrome", "firefox", "edge", "opera", "youtube", "browser"]):
        category = "browser"
    elif any(e in title for e in ["file explorer", "explorer", "this pc", "documents", "downloads", "desktop"]):
        category = "explorer"
    elif any(t in title for t in ["powershell", "cmd", "terminal", "bash", "command prompt"]):
        category = "terminal"
    elif any(ed in title for ed in ["code", "visual studio", "notepad", "sublime"]):
        category = "editor"
    elif any(m in title for m in ["spotify", "vlc", "media player", "netflix", "prime"]):
        category = "player"

    # If title is unknown or empty, inspect running processes to identify active app context
    if category == "unknown":
        try:
            import psutil
            for p in psutil.process_iter(['name']):
                try:
                    n = (p.info['name'] or '').lower()
                    if n in ["brave.exe", "chrome.exe", "msedge.exe", "firefox.exe"]:
                        category = "browser"
                        break
                    elif n in ["explorer.exe"] and category == "unknown":
                        category = "explorer"
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "title": active.get("title", "Unknown"),
        "category": category,
        "class_name": class_name
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

    # 9. Fallback: Click at current cursor position
    return gui.click_coordinate(button="left", clicks=1)


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
            return {
                "success": False,
                "error": "WhatsApp Desktop is not open. Open WhatsApp first, then ask again."
            }

        window = windows[0]
        window.set_focus()

        # WhatsApp exposes its chat-search field as an Edit control. Prefer an
        # explicitly named search field, then fall back to the first visible Edit.
        edits = window.descendants(control_type="Edit")
        search_edit = next(
            (edit for edit in edits if "search" in (edit.element_info.name or "").lower()),
            edits[0] if edits else None,
        )
        if search_edit is None:
            return {
                "success": False,
                "error": "Could not find WhatsApp's chat search field."
            }

        search_edit.set_focus()
        search_edit.type_keys("^a{BACKSPACE}")
        # WhatsApp Desktop is Chromium-based; direct UIA typing is unreliable in
        # its search field. Clipboard paste delivers the exact contact text.
        if copy_to_clipboard(contact):
            gui.press_keys(["ctrl", "v"])
        else:
            search_edit.type_keys(contact, with_spaces=True)
        time.sleep(1.0)

        normalized_contact = contact.casefold()
        # WhatsApp Desktop renders search results as DataItem/Button controls,
        # not ListItem controls. Buttons are preferred because they are the
        # directly actionable contact row.
        candidates = (
            window.descendants(control_type="Button")
            + window.descendants(control_type="DataItem")
        )
        named_candidates = [
            item for item in candidates
            if (item.element_info.name or "").strip()
            and (item.element_info.name or "").strip().casefold()
            not in {"chats", "contacts", "messages", "search results."}
        ]
        match = next(
            (item for item in named_candidates
             if normalized_contact == (item.element_info.name or "").strip().casefold()),
            None,
        )
        if match is None:
            match = next(
                (item for item in named_candidates
                 if (item.element_info.name or "").strip().casefold().startswith(normalized_contact)),
                None,
            )
        if match is None:
            match = next(
                (item for item in named_candidates
                 if normalized_contact in (item.element_info.name or "").casefold()),
                None,
            )
        if match is None:
            return {
                "success": False,
                "error": f"No WhatsApp chat matching '{contact}' was found."
            }

        match.click_input()
        return {
            "success": True,
            "contact": contact,
            "message": f"Opened WhatsApp chat with {contact}."
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

    opened = open_whatsapp_chat(contact_name)
    if not opened.get("success"):
        return opened

    try:
        from pywinauto import Desktop

        windows = Desktop(backend="uia").windows(title_re=".*WhatsApp.*", visible_only=True)
        window = windows[0]
        edits = window.descendants(control_type="Edit")
        message_edit = next(
            (
                edit for edit in edits
                if any(token in (edit.element_info.name or "").casefold()
                       for token in ("type a message", "message"))
                and "search" not in (edit.element_info.name or "").casefold()
            ),
            None,
        )
        if message_edit is None:
            return {
                "success": False,
                "error": "WhatsApp chat opened, but its message field was not found."
            }

        message_edit.set_focus()
        if copy_to_clipboard(clean_message):
            gui.press_keys(["ctrl", "v"])
        else:
            message_edit.type_keys(clean_message, with_spaces=True)
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


def smart_search(query: str, engine: str = "google") -> Dict[str, Any]:
    """
    Searches for a query. If a browser is already active, focuses the search/URL bar
    and runs the search in-place; otherwise launches a browser search.
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return {"success": False, "error": "No query provided to search."}

    # Detect YouTube in query
    if "youtube" in clean_q.lower() or "youtube" in engine.lower():
        engine = "youtube"
        # Clean phrases like "on youtube" or "in youtube"
        clean_q = re.sub(r"\b(?:on|in)\s+youtube\b", "", clean_q, flags=re.IGNORECASE).strip()

    ctx = get_active_window_context()

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
