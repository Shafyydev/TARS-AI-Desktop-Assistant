"""GUI and Computer-Use automation tools for mouse, keyboard, and screen capture."""
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import pyautogui

# Disable fail-safe corner abort so automated background/corner sessions do not crash
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05


def get_screen_dimensions() -> Dict[str, int]:
    """Returns the width and height of the primary screen in pixels."""
    width, height = pyautogui.size()
    return {"width": width, "height": height}


def get_mouse_position() -> Dict[str, int]:
    """Returns current mouse cursor X and Y coordinates."""
    x, y = pyautogui.position()
    return {"x": x, "y": y}


def take_screenshot(filename: Optional[str] = None) -> Dict[str, Any]:
    """
    Captures a high-resolution screenshot of the desktop.
    Saves to the screenshots/ directory and returns the absolute filepath.
    """
    try:
        screenshots_dir = Path(__file__).resolve().parent.parent / "screenshots"
        screenshots_dir.mkdir(exist_ok=True)

        if not filename:
            timestamp = int(time.time())
            filename = f"tars_screen_{timestamp}.png"

        filepath = screenshots_dir / filename
        try:
            screenshot = pyautogui.screenshot()
        except Exception:
            # Graceful fallback for non-interactive / locked sessions
            from PIL import Image
            dims = get_screen_dimensions()
            screenshot = Image.new("RGB", (dims.get("width", 1920), dims.get("height", 1080)), color=(35, 39, 42))

        screenshot.save(filepath)

        return {
            "success": True,
            "filepath": str(filepath),
            "width": screenshot.width,
            "height": screenshot.height
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to capture screenshot: {str(e)}"}


def click_coordinate(x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    """
    Moves mouse to (x, y) and clicks, or clicks at current cursor position if no coordinates given.
    """
    try:
        if x is not None and y is not None:
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x=x, y=y, clicks=clicks, button=button)
            return {"success": True, "message": f"Clicked at ({x}, {y}) with {button} button ({clicks}x)."}
        else:
            pyautogui.click(clicks=clicks, button=button)
            cur_x, cur_y = pyautogui.position()
            return {"success": True, "message": f"Clicked at current position ({cur_x}, {cur_y}) with {button} button."}
    except Exception as e:
        return {"success": False, "error": f"Click failed: {str(e)}"}


def double_click(x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
    """Double-clicks at (x, y) or at current cursor position."""
    return click_coordinate(x, y, button="left", clicks=2)


def right_click(x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
    """Right-clicks at (x, y) or at current cursor position."""
    return click_coordinate(x, y, button="right", clicks=1)


def middle_click(x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
    """Middle-clicks at (x, y) or at current cursor position."""
    return click_coordinate(x, y, button="middle", clicks=1)


def move_mouse(x: int, y: int, duration: float = 0.15) -> Dict[str, Any]:
    """Moves mouse cursor to absolute (x, y) coordinates."""
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return {"success": True, "message": f"Moved mouse to ({x}, {y})."}
    except Exception as e:
        return {"success": False, "error": f"Move mouse failed: {str(e)}"}


def move_mouse_relative(dx: int, dy: int, duration: float = 0.15) -> Dict[str, Any]:
    """Moves mouse cursor relative to current position by (dx, dy)."""
    try:
        pyautogui.moveRel(dx, dy, duration=duration)
        cur_x, cur_y = pyautogui.position()
        return {"success": True, "message": f"Moved mouse by ({dx}, {dy}) to ({cur_x}, {cur_y})."}
    except Exception as e:
        return {"success": False, "error": f"Relative mouse move failed: {str(e)}"}


def drag_mouse(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.4) -> Dict[str, Any]:
    """Drags mouse from (start_x, start_y) to (end_x, end_y)."""
    try:
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=duration, button='left')
        return {"success": True, "message": f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})."}
    except Exception as e:
        return {"success": False, "error": f"Drag failed: {str(e)}"}


def type_text(text: str, interval: float = 0.005, press_enter: bool = False) -> Dict[str, Any]:
    """
    Types text into the active window. Supports optionally pressing Enter after typing.
    """
    try:
        pyautogui.write(text, interval=interval)
        if press_enter:
            pyautogui.press("enter")
        return {"success": True, "message": f"Typed '{text}' successfully."}
    except Exception as e:
        return {"success": False, "error": f"Typing failed: {str(e)}"}


def press_keys(keys: List[str]) -> Dict[str, Any]:
    """
    Presses a sequence or combination of keys (e.g. ['ctrl', 'c'] or ['alt', 'tab'] or ['enter']).
    """
    try:
        clean_keys = [k.strip().lower() for k in keys]
        if len(clean_keys) == 1:
            pyautogui.press(clean_keys[0])
        else:
            pyautogui.hotkey(*clean_keys)
        return {"success": True, "message": f"Pressed hotkey combo: {' + '.join(clean_keys)}"}
    except Exception as e:
        return {"success": False, "error": f"Key combination failed: {str(e)}"}


def scroll_screen(amount: int) -> Dict[str, Any]:
    """
    Scrolls vertically. Positive scrolls up, negative scrolls down.
    """
    try:
        pyautogui.scroll(amount)
        direction = "up" if amount > 0 else "down"
        return {"success": True, "message": f"Scrolled {direction} by {abs(amount)} units."}
    except Exception as e:
        return {"success": False, "error": f"Scroll failed: {str(e)}"}


def scroll_window(amount: int) -> Dict[str, Any]:
    """Alias for scroll_screen."""
    return scroll_screen(amount)


def inspect_screen(question: Optional[str] = None) -> Dict[str, Any]:
    """
    Captures screen state, resolution, mouse position, active window metadata,
    and runs multi-modal vision analysis through Gemini to describe visual contents.
    """
    shot = take_screenshot()
    dims = get_screen_dimensions()
    mpos = get_mouse_position()
    from tools.ui_automation import get_active_window
    act = get_active_window()
    active_win = act.get("title") if act.get("success") else "Desktop"

    description = f"Active window: '{active_win}'. Screen resolution: {dims['width']}x{dims['height']}."

    shot_path = shot.get("filepath")
    api_key = os.getenv("GEMINI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    ai_provider = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if not ai_provider:
        try:
            from core.llm_provider import _load_llm_settings
            ai_provider = str(_load_llm_settings().get("provider") or "gemini").strip().lower()
        except Exception:
            ai_provider = "gemini"

    prompt = question or (
        "Describe what is currently visible on this desktop screen concisely in 2 sentences. "
        "Highlight active applications, visible windows, websites, or open documents."
    )

    vision_done = False
    vision_errors: List[str] = []

    def _try_openrouter_vision() -> Optional[str]:
        if not (shot_path and openrouter_key and openrouter_key != "your_openrouter_api_key_here"):
            return None
        try:
            import base64
            from openai import OpenAI
            with open(shot_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            client = OpenAI(
                base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
                api_key=openrouter_key,
                default_headers={
                    "HTTP-Referer": "https://github.com/Shafyydev/TARS",
                    "X-Title": "TARS AI Desktop Assistant",
                }
            )
            model = (
                os.getenv("OPENROUTER_MODEL")
                or (os.getenv("AI_MODEL") if ai_provider == "openrouter" else None)
                or "google/gemini-2.5-flash"
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                        ]
                    }
                ],
                max_tokens=300,
                timeout=8.0,
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception as e:
            vision_errors.append(str(e)[:60])
        return None

    def _try_omniroute_vision() -> Optional[str]:
        if not shot_path:
            return None
        try:
            import base64
            from openai import OpenAI
            from core.llm_provider import get_omniroute_api_key
            with open(shot_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            key = get_omniroute_api_key() or "omniroute-default"
            base_url = (
                os.getenv("OMNIROUTE_URL") or os.getenv("OMNIROUTE_BASE_URL") or "http://localhost:20128/v1"
            ).rstrip("/")
            client = OpenAI(
                base_url=base_url,
                api_key=key,
                default_headers={
                    "HTTP-Referer": "https://github.com/Shafyydev/TARS",
                    "X-Title": "TARS AI Desktop Assistant",
                }
            )
            model = os.getenv("OMNIROUTE_MODEL") or os.getenv("AI_MODEL") or "auto/best-vision"
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                        ]
                    }
                ],
                max_tokens=300,
                timeout=8.0,
            )
            if resp.choices and resp.choices[0].message.content:
                from core.llm_provider import is_gateway_notice
                content = resp.choices[0].message.content.strip()
                if not is_gateway_notice(content):
                    return content
        except Exception as e:
            vision_errors.append(str(e)[:60])
        return None

    def _try_gemini_vision() -> Optional[str]:
        if not (shot_path and api_key and api_key != "your_gemini_api_key_here"):
            return None
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            with open(shot_path, "rb") as f:
                img_bytes = f.read()

            vision_models = []
            for m in (
                os.getenv("GEMINI_VISION_MODEL"),
                "gemini-3.1-flash-lite",
                "gemini-3-flash-preview",
            ):
                if m and m not in vision_models:
                    vision_models.append(m)
            cfg = None
            try:
                cfg = types.GenerateContentConfig(http_options=types.HttpOptions(timeout=6000))
            except Exception:
                pass
            for vision_model in vision_models:
                try:
                    resp = client.models.generate_content(
                        model=vision_model,
                        contents=[
                            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                            prompt
                        ],
                        config=cfg,
                    )
                    if resp and resp.text:
                        return resp.text.strip()
                except Exception as inner_err:
                    vision_errors.append(f"{vision_model}: {str(inner_err)[:50]}")
                    continue
        except Exception as e:
            vision_errors.append(str(e)[:60])
        return None

    # Order vision providers with primary first, falling back gracefully
    order = {
        "omniroute": [_try_omniroute_vision, _try_openrouter_vision, _try_gemini_vision],
        "openrouter": [_try_openrouter_vision, _try_omniroute_vision, _try_gemini_vision],
        "gemini": [_try_gemini_vision, _try_openrouter_vision, _try_omniroute_vision],
    }.get(ai_provider, [_try_gemini_vision, _try_openrouter_vision, _try_omniroute_vision])

    for fn in order:
        text = fn()
        if text:
            description = text
            vision_done = True
            break

    if not vision_done:
        # Offline fallback: enrich description with accessible UI element labels from active window
        try:
            from tools.ui_automation import get_ui_elements
            ui_res = get_ui_elements()
            if ui_res.get("success") and ui_res.get("elements"):
                labels = [e["name"] for e in ui_res["elements"][:8] if e.get("name")]
                if labels:
                    description = f"{description} Visible controls: {', '.join(labels)}."
        except Exception:
            pass
        if vision_errors and "Visible controls" not in description:
            description = f"{description} (Vision analysis unavailable: {vision_errors[0]})"

    return {
        "success": shot.get("success", True),
        "screenshot_path": shot_path,
        "dimensions": dims,
        "mouse_position": mpos,
        "active_window": active_win,
        "description": description
    }


def describe_screen(question: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyzes current screen content using Gemini multimodal vision and returns a description.
    """
    res = inspect_screen(question=question)
    return {
        "success": res.get("success", True),
        "description": res.get("description", "Screen captured."),
        "screenshot_path": res.get("screenshot_path"),
        "active_window": res.get("active_window")
    }


def switch_window() -> Dict[str, Any]:
    """Switches active window using Alt+Tab."""
    return press_keys(["alt", "tab"])


def _find_window_handle(title_or_process: str) -> Optional[int]:
    """
    Finds a visible top-level window by title or process name.

    Candidates are ranked so the closest match wins: an exact title, then a title
    starting with the query, then a whole-word title match, then a substring match,
    and finally a match on the owning process name.
    """
    import psutil
    import win32gui
    import win32process

    query = (title_or_process or "").strip().lower()
    if not query:
        return None

    ranked: List[tuple] = []

    # The desktop itself is not an application window: closing it is impossible
    # and matching it (its title contains "File Explorer" once a folder is open)
    # hijacks window commands.
    IGNORED_CLASSES = {"progman", "workerw"}
    IGNORED_TITLES = {"program manager", ""}

    def _collect(hwnd, _payload):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title or title.lower() in IGNORED_TITLES:
                return
            try:
                if (win32gui.GetClassName(hwnd) or "").lower() in IGNORED_CLASSES:
                    return
            except Exception:
                pass
            title_lower = title.lower()
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                process = psutil.Process(pid).name().lower()
            except Exception:
                process = ""
            proc_base = process[:-4] if process.endswith(".exe") else process
            # Tie-break equal titles by the owning process: "file explorer" should
            # close explorer.exe, not an editor tab that merely mentions it.
            proc_match = bool(proc_base) and (proc_base in query or query in proc_base)
            proc_rank = 0 if proc_match else 1

            if title_lower == query:
                rank = 0
            elif title_lower.startswith(query):
                rank = 1
            elif re.search(rf"\b{re.escape(query)}\b", title_lower):
                rank = 2
            elif query in title_lower:
                rank = 3
            elif proc_match:
                rank = 4
            else:
                return
            ranked.append((rank, proc_rank, hwnd))
        except Exception:
            pass

    win32gui.EnumWindows(_collect, None)
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def focus_window(title_or_process: str) -> Dict[str, Any]:
    """
    Brings a named window to the foreground.

    Matches on window title first, then on the owning process name.
    """
    query = (title_or_process or "").strip()
    if not query:
        return {"success": False, "error": "No window name or process was provided."}

    hwnd = _find_window_handle(query)
    if hwnd is None:
        return {
            "success": False,
            "error": f"No visible window matching '{query}' was found.",
        }

    try:
        import win32con
        import win32gui

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            # Windows only lets the foreground process hand focus to another
            # window.  TARS often runs in a background console, so attach to the
            # current foreground thread, which is what the shell itself does.
            import pyautogui
            pyautogui.press("alt")
            try:
                import win32api
                import win32process

                foreground = win32gui.GetForegroundWindow()
                target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
                current_thread, _ = win32process.GetWindowThreadProcessId(foreground)
                my_thread = win32api.GetCurrentThreadId()
                attached = False
                for thread_id in {current_thread, my_thread}:
                    try:
                        win32process.AttachThreadInput(thread_id, target_thread, True)
                        attached = True
                        break
                    except Exception:
                        continue
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    if attached:
                        try:
                            win32process.AttachThreadInput(thread_id, target_thread, False)
                        except Exception:
                            pass
                return {
                    "success": True,
                    "window": win32gui.GetWindowText(hwnd),
                    "message": f"Focused window '{win32gui.GetWindowText(hwnd)}'.",
                }
            except Exception:
                # Last resort: cycling the window through minimized state makes
                # Windows treat the next activation as a user action.
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)

        return {
            "success": True,
            "window": win32gui.GetWindowText(hwnd),
            "message": f"Focused window '{win32gui.GetWindowText(hwnd)}'.",
        }
    except Exception as exc:
        return {"success": False, "error": f"Could not focus '{query}': {exc}"}


def close_active_window(window_name: Optional[str] = None) -> Dict[str, Any]:
    """Closes the named window if given, otherwise the active window (Alt+F4)."""
    if window_name:
        focused = focus_window(window_name)
        if not focused.get("success"):
            return focused
        result = press_keys(["alt", "f4"])
        if result.get("success"):
            result["message"] = f"Closed '{focused.get('window') or window_name}'."
        return result
    return press_keys(["alt", "f4"])


def maximize_active_window(window_name: Optional[str] = None) -> Dict[str, Any]:
    """Maximizes the named window if given, otherwise the active window (Win+Up)."""
    if window_name:
        focused = focus_window(window_name)
        if not focused.get("success"):
            return focused
        result = press_keys(["win", "up"])
        if result.get("success"):
            result["message"] = f"Maximized '{focused.get('window') or window_name}'."
        return result
    return press_keys(["win", "up"])


def minimize_active_window(window_name: Optional[str] = None) -> Dict[str, Any]:
    """Minimizes the named window if given, otherwise the active window (Win+Down)."""
    if window_name:
        focused = focus_window(window_name)
        if not focused.get("success"):
            return focused
        result = press_keys(["win", "down"])
        if result.get("success"):
            result["message"] = f"Minimized '{focused.get('window') or window_name}'."
        return result
    return press_keys(["win", "down"])
