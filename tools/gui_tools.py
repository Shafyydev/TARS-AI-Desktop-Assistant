"""GUI and Computer-Use automation tools for mouse, keyboard, and screen capture."""
import os
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
    ai_provider = os.getenv("AI_PROVIDER", "gemini").lower()
    prompt = question or (
        "Describe what is currently visible on this desktop screen concisely in 2 sentences. "
        "Highlight active applications, visible windows, websites, or open documents."
    )

    if shot_path and ai_provider == "openrouter" and openrouter_key and openrouter_key != "your_openrouter_api_key_here":
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
            model = os.getenv("AI_MODEL", "anthropic/claude-3.5-sonnet")
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
                max_tokens=300
            )
            if resp.choices and resp.choices[0].message.content:
                description = resp.choices[0].message.content.strip()
        except Exception as e:
            description = f"{description} (Vision analysis unavailable: {str(e)[:60]})"
    elif shot_path and api_key and api_key != "your_gemini_api_key_here":
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            with open(shot_path, "rb") as f:
                img_bytes = f.read()

            resp = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    prompt
                ]
            )
            if resp and resp.text:
                description = resp.text.strip()
        except Exception as e:
            description = f"{description} (Vision analysis unavailable: {str(e)[:60]})"

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


def close_active_window() -> Dict[str, Any]:
    """Closes the current active window using Alt+F4."""
    return press_keys(["alt", "f4"])


def maximize_active_window() -> Dict[str, Any]:
    """Maximizes current active window using Win+Up."""
    return press_keys(["win", "up"])


def minimize_active_window() -> Dict[str, Any]:
    """Minimizes current active window using Win+Down."""
    return press_keys(["win", "down"])
