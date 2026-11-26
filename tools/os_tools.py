"""Operating System and hardware management tools for Windows."""
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import psutil

# Predefined application & web destination mappings for fast voice launching
COMMON_APPS = {
    "chrome": "start chrome",
    "google chrome": "start chrome",
    "browser": "start chrome",
    "brave": "start brave:",
    "brave browser": "start brave:",
    "firefox": "start firefox",
    "edge": "start msedge",
    "code": "code",
    "vs code": "code",
    "vscode": "code",
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "terminal": "start wt",
    "powershell": "start powershell",
    "cmd": "start cmd",
    "spotify": "start spotify:",
    "discord": "start discord:",
    "slack": "start slack:",
    "telegram": "start telegram:",
    "whatsapp": "start whatsapp:",
    "steam": "start steam:",
    "explorer": "explorer",
    "files": "explorer",
    "file explorer": "explorer",
    "task manager": "taskmgr",
    "settings": "start ms-settings:",
    "control panel": "control",
    "paint": "mspaint",
    "word": "start winword",
    "excel": "start excel",
    "powerpoint": "start powerpnt",
    "obs": "start obs64",
    "vlc": "start vlc",
    "snipping tool": "start ms-screenclip:",
    "photos": "start ms-photos:",
    "calendar": "start outlookcal:",
    "store": "start ms-windows-store:",
    "microsoft store": "start ms-windows-store:",
    "device manager": "devmgmt.msc",
    "resource monitor": "resmon",
    "postman": "start postman",
    # Websites & Quick Web Destinations
    "github": "start https://github.com",
    "my github": "start https://github.com/Shafyydev",
    "my profile github": "start https://github.com/Shafyydev",
    "profile github": "start https://github.com/Shafyydev",
    "github profile": "start https://github.com/Shafyydev",
    "youtube": "start https://youtube.com",
    "google": "start https://google.com",
    "gmail": "start https://mail.google.com",
    "mail": "start https://mail.google.com",
    "email": "start https://mail.google.com",
    "chatgpt": "start https://chat.openai.com",
    "reddit": "start https://reddit.com",
    "twitter": "start https://x.com",
    "x": "start https://x.com",
    "linkedin": "start https://linkedin.com",
    "instagram": "start https://instagram.com",
    "netflix": "start https://netflix.com",
    "amazon": "start https://amazon.com",
}


def _match_common_app(name: str) -> Optional[str]:
    """
    Returns the COMMON_APPS key that ``name`` is a typo of, or None.

    "open yoputube" used to run ``start "" "yoputube"``, which silently does
    nothing.  Single-word near misses now resolve to the intended app or site.
    """
    from difflib import get_close_matches

    if not name or " " in name.strip():
        return None
    matches = get_close_matches(name.strip().lower(), list(COMMON_APPS.keys()), n=1, cutoff=0.8)
    return matches[0] if matches else None


def _clean_app_target(app_name: str) -> str:
    """
    Strips container phrases from a spoken app name.

    "open a new tab in brave browser" is a request to open Brave, not an app
    literally named "a new tab in brave browser".
    """
    clean = (app_name or "").strip()
    for phrase in (
        "a new tab in ", "new tab in ", "a tab in ", "tab in ",
        "a new window in ", "new window in ", "a window in ", "window in ",
    ):
        if clean.lower().startswith(phrase):
            clean = clean[len(phrase):].strip()
    stripped = re.sub(r"\s+(?:web\s+)?browser$", "", clean, flags=re.IGNORECASE).strip()
    # "open my browser" must stay "my browser": only drop a trailing "browser"
    # when what remains is an actual name.
    if stripped and stripped.lower() not in {"my", "the", "a", "an", "this", "that", "web"}:
        clean = stripped
    clean = re.sub(r"^the\s+", "", clean, flags=re.IGNORECASE).strip()
    return clean or (app_name or "").strip().lower()


def clean_app_target(app_name: str) -> str:
    """Public wrapper for :func:`_clean_app_target`."""
    return _clean_app_target(app_name)


def _launch_token(cmd: str) -> str:
    """Derives the process/window token to look for after launching ``cmd``."""
    token = cmd.strip()
    if token.lower().startswith("start "):
        token = token[6:].strip()
    # Drop an empty quoted title, as in: start "" "brave"
    token = token.replace('""', " ").strip()
    if token.lower().startswith(("http://", "https://")):
        return ""
    token = token.strip("\"'")
    token = token.replace("\\", "/").rsplit("/", 1)[-1]
    if token.lower().endswith(".exe"):
        token = token[:-4]
    parts = [part.strip("\"'").rstrip(":").strip() for part in token.split()]
    parts = [part for part in parts if part]
    token = parts[0] if parts else ""
    return token.strip().lower()


_LAUNCH_ALIAS_TOKENS: Dict[str, tuple] = {
    "calc": ("calc", "calculator", "applicationframehost"),
    "wt": ("wt", "windowsterminal", "terminal"),
    "ms-settings": ("ms-settings", "systemsettings", "settings"),
    "winword": ("winword", "word"),
    "powerpnt": ("powerpnt", "powerpoint"),
    "mspaint": ("mspaint", "paint"),
    "taskmgr": ("taskmgr", "task manager"),
    "explorer": ("explorer", "file explorer"),
    "msedge": ("msedge", "edge"),
    "ms-screenclip": ("ms-screenclip", "snipping", "screenclippinghost"),
    "ms-photos": ("ms-photos", "photos"),
    "ms-windows-store": ("ms-windows-store", "winstore", "microsoft store"),
}


def _has_visible_window(token: str) -> bool:
    """True when a visible top-level window title contains ``token``."""
    try:
        import win32gui

        matched = False

        def _collect(hwnd, _payload):
            nonlocal matched
            if matched:
                return False
            try:
                if win32gui.IsWindowVisible(hwnd):
                    title = (win32gui.GetWindowText(hwnd) or "").lower()
                    if token in title:
                        matched = True
                        return False
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_collect, None)
        return matched
    except Exception:
        return False


def _launch_target_present(token: str) -> bool:
    """True when a process or visible window matching ``token`` exists."""
    if not token:
        return True

    candidates = _LAUNCH_ALIAS_TOKENS.get(token, (token,))

    # UWP/WebView2 tray apps like WhatsApp stay running headless as WhatsApp.Root.exe
    # even when zero windows are visible, so process checks alone produce false positives.
    if token != "whatsapp":
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = (proc.info.get("name") or "").lower()
                if any(alias in proc_name for alias in candidates):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    try:
        import win32gui

        titles: List[str] = []

        def _collect(hwnd, _payload):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    titles.append(win32gui.GetWindowText(hwnd) or "")
            except Exception:
                pass

        win32gui.EnumWindows(_collect, None)
        return any(any(alias in title.lower() for alias in candidates) for title in titles)
    except Exception:
        return False


def _wait_for_launch(token: str, timeout: float = 3.0) -> bool:
    """Waits up to ``timeout`` seconds for a launched target to appear."""
    deadline = time.time() + timeout
    while True:
        if _launch_target_present(token):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def launch_application(app_name: str) -> Dict[str, Any]:
    """
    Launches an application or web destination by common name or executable path.
    """
    clean_name = _clean_app_target(app_name)
    resolved_folder = None
    fuzzy_app = None if " " in clean_name.strip() else _match_common_app(clean_name)

    # Smart GitHub profile resolution
    if "github" in clean_name and any(kw in clean_name for kw in ["profile", "my", "account", "user"]):
        cmd = "start https://github.com/Shafyydev"
        clean_name = "GitHub profile (Shafyydev)"
    elif clean_name in COMMON_APPS:
        cmd = COMMON_APPS[clean_name]
    elif fuzzy_app:
        # Typo tolerance: "open yoputube" resolves to YouTube instead of a
        # silent ``start "" "yoputube"``.
        cmd = COMMON_APPS[fuzzy_app]
        clean_name = fuzzy_app
    elif any(clean_name.endswith(ext) for ext in [".com", ".org", ".io", ".net", ".dev", ".edu", ".gov"]) or clean_name.startswith("http"):
        url = clean_name if clean_name.startswith("http") else f"https://{clean_name}"
        cmd = f"start {url}"
    else:
        # Check if the target is a drive, directory, or known folder before Windows start.
        # Only single-token targets are resolved: a sentence such as
        # "a new tab in brave browser" must not be read as a path.
        resolved_folder = resolve_system_target(clean_name) if " " not in clean_name else None
        if resolved_folder and resolved_folder.exists():
            return open_folder(str(resolved_folder))

        # Fallback to direct Windows start
        cmd = f"start \"\" \"{clean_name}\""

    # WhatsApp Desktop (Store/WebView2) can remain wedged in the background with
    # WhatsApp.Root.exe alive and zero visible windows; focus if open, otherwise
    # clear the headless process before launching so Windows renders the window.
    if clean_name == "whatsapp":
        if _has_visible_window("whatsapp"):
            from tools.gui_tools import focus_window
            foc = focus_window("whatsapp")
            if foc.get("success"):
                return {"success": True, "message": "Focused open WhatsApp window."}
        else:
            for proc in psutil.process_iter(["name"]):
                try:
                    if (proc.info.get("name") or "").lower() in {"whatsapp.root.exe", "whatsapp.exe"}:
                        proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            time.sleep(0.4)

    try:
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        return {"success": False, "error": f"Failed to launch {app_name}: {str(e)}"}

    # A shell start returns immediately whether or not the target exists, so the
    # launch is only reported as successful once something matching it appears.
    token = "" if resolved_folder is not None else _launch_token(cmd)
    wait_budget = 1.5 if token == "whatsapp" else 1.0
    if not token or _wait_for_launch(token, timeout=wait_budget):
        return {"success": True, "message": f"Successfully launched {clean_name}."}

    return {
        "success": False,
        "error": (
            f"Launched '{clean_name}' but no matching process or window appeared "
            f"within {wait_budget:.1f} seconds."
        ),
    }


def open_application(app_name: str) -> Dict[str, Any]:
    """Alias for launch_application."""
    return launch_application(app_name)


def kill_process(process_name: str) -> Dict[str, Any]:
    """
    Terminates running processes matching the given name (e.g. 'chrome.exe' or 'notepad').
    """
    clean_name = process_name.strip().lower()
    if not clean_name.endswith(".exe"):
        clean_name += ".exe"

    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == clean_name:
                proc.kill()
                killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed_count > 0:
        return {"success": True, "message": f"Terminated {killed_count} instances of {clean_name}."}
    return {"success": False, "message": f"No running process found matching '{clean_name}'."}


def get_system_telemetry() -> Dict[str, Any]:
    """
    Collects real-time hardware metrics: CPU, RAM, Disk, and Battery.
    """
    cpu_usage = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    battery = psutil.sensors_battery()

    battery_info = "No battery detected (Desktop)"
    if battery:
        charging = "Plugged in" if battery.power_plugged else "Discharging"
        battery_info = f"{battery.percent}% ({charging})"

    return {
        "cpu_load_percent": cpu_usage,
        "ram_used_gb": round(memory.used / (1024 ** 3), 2),
        "ram_total_gb": round(memory.total / (1024 ** 3), 2),
        "ram_usage_percent": memory.percent,
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2),
        "battery": battery_info
    }


def set_system_volume(level_percent: int) -> Dict[str, Any]:
    """
    Sets the Windows master audio endpoint volume (0 to 100).
    """
    level_percent = max(0, min(100, level_percent))
    
    # 1. Modern pycaw (sub-50ms execution)
    try:
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "volume_percent"):
            speakers.volume_percent = level_percent
            return {"success": True, "message": f"Master volume set to {level_percent}%."}
    except Exception:
        pass

    # 2. Legacy pycaw COM endpoint
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        
        devices = AudioUtilities.GetSpeakers()
        if hasattr(devices, "Activate"):
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(level_percent / 100.0, None)
            return {"success": True, "message": f"Master volume set to {level_percent}%."}
    except Exception:
        pass

    # 3. Fallback using PowerShell
    try:
        ps_vol = f"""
        $obj = New-Object -ComObject WScript.Shell
        1..50 | ForEach-Object {{ $obj.SendKeys([char]174) }}
        $steps = [math]::Round({level_percent} / 2)
        1..$steps | ForEach-Object {{ $obj.SendKeys([char]175) }}
        """
        subprocess.run(["powershell", "-Command", ps_vol], capture_output=True, timeout=3)
        return {"success": True, "message": f"Volume adjusted to ~{level_percent}% via fallback."}
    except Exception as e:
        return {"success": False, "error": f"Could not adjust volume: {e}"}


def media_control(action: str) -> Dict[str, Any]:
    """
    Sends virtual media keys: 'play_pause', 'next', 'prev', 'stop', 'volume_up', 'volume_down', 'mute'.
    """
    key_codes = {
        "play_pause": 0xB3,
        "next": 0xB0,
        "prev": 0xB1,
        "stop": 0xB2,
        "volume_up": 0xAF,
        "volume_down": 0xAE,
        "mute": 0xAD
    }
    
    action_clean = action.strip().lower()
    if action_clean not in key_codes:
        return {"success": False, "error": f"Unknown media action: {action}. Valid options: {list(key_codes.keys())}"}

    import ctypes
    vk = key_codes[action_clean]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0) # Key down
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0) # Key up
    return {"success": True, "message": f"Executed media action: {action_clean}"}


def get_running_windows() -> List[str]:
    """
    Returns a list of titles of all currently visible top-level application windows.
    """
    try:
        import win32gui
        windows = []
        def enum_handler(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and len(title.strip()) > 1:
                    windows.append(title.strip())
        win32gui.EnumWindows(enum_handler, None)
        return windows[:25]
    except Exception:
        try:
            res = subprocess.run(["tasklist", "/v", "/fo", "csv"], capture_output=True, text=True, timeout=5)
            lines = res.stdout.splitlines()[1:]
            return [line.split(",")[-1].replace('"', '') for line in lines if "N/A" not in line][:20]
        except Exception:
            return []


def minimize_all_windows() -> Dict[str, Any]:
    """Minimizes all windows to show the desktop (Win+D)."""
    import ctypes
    ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)
    return {"success": True, "message": "Minimized all windows."}


def read_clipboard() -> Dict[str, Any]:
    """Reads current text from the Windows clipboard."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        data = win32clipboard.GetClipboardData()
        win32clipboard.CloseClipboard()
        return {"success": True, "content": data}
    except Exception as e:
        return {"success": False, "error": f"Failed to read clipboard: {e}"}


def write_clipboard(text: str) -> Dict[str, Any]:
    """Writes text to the Windows clipboard."""
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return {"success": True, "message": "Copied to clipboard."}
    except Exception as e:
        return {"success": False, "error": f"Failed to write to clipboard: {e}"}


def lock_workstation() -> Dict[str, Any]:
    """Locks the Windows workstation instantly."""
    try:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return {"success": True, "message": "Windows workstation locked."}
    except Exception as e:
        return {"success": False, "error": f"Failed to lock workstation: {e}"}


def get_real_folder(name: str) -> Path:
    """
    Resolves standard Windows User Shell Folders (Desktop, Documents, Downloads, etc.)
    with automatic support for OneDrive and custom redirections.
    """
    clean = name.strip().lower()
    if clean in ["home", "user", "root"]:
        return Path.home()

    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        reg_map = {
            "desktop": "Desktop",
            "documents": "Personal",
            "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
            "pictures": "My Pictures",
            "videos": "My Video",
            "music": "My Music"
        }
        val_name = reg_map.get(clean)
        if val_name:
            val, _ = winreg.QueryValueEx(key, val_name)
            expanded = Path(os.path.expandvars(val))
            if expanded.exists():
                return expanded
    except Exception:
        pass

    onedrive_p = Path.home() / "OneDrive" / clean.capitalize()
    if onedrive_p.exists():
        return onedrive_p
    return Path.home() / clean.capitalize()


def resolve_system_target(target: str) -> Optional[Path]:
    """
    Intelligently resolves natural language target strings (voice input) to concrete filesystem paths.
    Handles:
    - Drive letters: "d drive", "drive d", "d:", "c drive", "f drive", etc.
    - Standard user shell folders: "desktop", "downloads", "documents", "pictures", "videos", "music", "home"
    - Exact or fuzzy directory names across fixed drives and working directories:
      "codehub", "code hub", "code hub folder", "tars", "movies", "games", "backlogs",
      "backup", "attendance system", "projects"
    - Absolute or relative paths: "D:\\codehub", "C:\\Users"
    """
    if not target or not isinstance(target, str):
        return None

    clean = target.strip().lower()

    # Strip common leading voice commands
    for prefix in ["go to ", "open up ", "open ", "navigate to ", "take me to ", "show me ", "visit ", "bring up "]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()

    # Strip common trailing and leading folder indicators
    clean = re.sub(r"\s+(?:folder|directory|dir)$", "", clean).strip()
    clean = re.sub(r"^(?:folder|directory|dir)\s+", "", clean).strip()
    clean = re.sub(r"^(?:the|my)\s+", "", clean).strip()

    if not clean:
        return None

    # 1. Drive check (e.g. "d drive", "drive d", "d:", "c drive", "d")
    if len(clean.split()) <= 3:
        drive_match = re.search(r"^(?:the\s+)?([a-zA-Z])\s*(?:drive|disk)$|^(?:the\s+)?(?:drive|disk)\s*([a-zA-Z])$|^([a-zA-Z]):\\?$|^([a-zA-Z])$", clean)
        if drive_match:
            letter = (drive_match.group(1) or drive_match.group(2) or drive_match.group(3) or drive_match.group(4)).upper()
            p = Path(f"{letter}:\\")
            if p.exists():
                return p

    # 2. Known Windows User Shell Folders (Desktop, Downloads, Documents, etc.)
    if clean in ["desktop", "downloads", "documents", "pictures", "videos", "music", "home", "onedrive"]:
        return get_real_folder(clean)

    # 3. Direct path or environment variable path
    try:
        p_direct = Path(clean)
        if p_direct.exists():
            return p_direct
        p_exp = Path(os.path.expandvars(os.path.expanduser(clean)))
        if p_exp.exists():
            return p_exp
    except Exception:
        pass

    # 4. Search across priority roots
    words = clean.split()
    stopwords = {"a", "an", "the", "in", "on", "of", "to", "for", "and", "my", "is", "at", "with"}
    if len(words) > 3 and any(word in stopwords for word in words):
        # A sentence such as "a new tab in brave browser" is a command about an
        # app, not a folder name; fuzzy-matching it hits random directories.
        return None

    norm_q = re.sub(r"[^a-z0-9]", "", clean)
    if not norm_q:
        return None

    roots: List[Path] = []

    # Priority order:
    # 1) Current working directory and parent (e.g. D:\codehub\TARS, D:\codehub)
    cwd = Path.cwd()
    if cwd.parent.exists() and cwd.parent not in roots:
        roots.append(cwd.parent)
    if cwd.exists() and cwd not in roots:
        roots.append(cwd)

    # 2) Primary project drive (D:\)
    d_drive = Path("D:\\")
    if d_drive.exists() and d_drive not in roots:
        roots.append(d_drive)

    # 3) User folders & OneDrive
    for extra in [Path.home() / "OneDrive", Path.home(), get_real_folder("desktop"), get_real_folder("documents"), get_real_folder("downloads")]:
        if extra and extra.exists() and extra not in roots:
            roots.append(extra)

    # 4) Add other disk partitions (F:\, C:\)
    try:
        for part in psutil.disk_partitions():
            m = Path(part.mountpoint)
            if m.exists() and m not in roots:
                roots.append(m)
    except Exception:
        pass

    # Pass 1: Exact normalized match
    for root in roots:
        try:
            for item in root.iterdir():
                if item.is_dir():
                    norm_item = re.sub(r"[^a-z0-9]", "", item.name.lower())
                    if norm_item == norm_q:
                        return item
        except (PermissionError, OSError):
            continue

    # Pass 2: Substring / Prefix match (min length 3 to avoid false positives)
    if len(norm_q) >= 3:
        for root in roots:
            try:
                for item in root.iterdir():
                    if item.is_dir():
                        norm_item = re.sub(r"[^a-z0-9]", "", item.name.lower())
                        if norm_q in norm_item or (len(norm_item) >= 3 and norm_item in norm_q):
                            return item
            except (PermissionError, OSError):
                continue

    return None


def open_folder(folder_path: str = "downloads") -> Dict[str, Any]:
    """
    Opens a folder or drive in Windows File Explorer with automatic OneDrive,
    drive letter, and dynamic directory resolution.
    """
    target_path = resolve_system_target(folder_path)
    if not target_path or not target_path.exists():
        direct = Path(folder_path)
        if direct.exists():
            target_path = direct
        else:
            return {"success": False, "error": f"Folder or drive '{folder_path}' does not exist."}

    try:
        os.startfile(str(target_path))
        return {"success": True, "message": f"Opened folder: {target_path}", "path": str(target_path)}
    except Exception as e:
        return {"success": False, "error": f"Could not open folder: {e}"}


def create_file_or_folder(item_type: str, name: str, location: str = "desktop", content: str = "") -> Dict[str, Any]:
    """
    Creates a new file or directory on Windows with optional content.
    Supports location aliases: 'desktop', 'documents', 'downloads', 'home' or absolute paths.
    """
    clean_loc = location.strip().lower()
    if clean_loc in ["desktop", "documents", "downloads", "pictures", "videos", "home"]:
        base = get_real_folder(clean_loc)
    else:
        base = Path(location)

    target = base / name
    try:
        if item_type.lower() in ["folder", "directory", "dir"]:
            target.mkdir(parents=True, exist_ok=True)
            return {"success": True, "message": f"Created folder '{name}' at {target}."}
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content or "", encoding="utf-8")
            return {"success": True, "message": f"Created file '{name}' at {target}."}
    except Exception as e:
        return {"success": False, "error": f"Failed to create {item_type}: {e}"}


def delete_file_or_folder(name: str, location: str = "desktop") -> Dict[str, Any]:
    """
    Safely deletes a file or directory from desktop/documents/downloads.
    Deletion is recoverable (Recycle Bin) when send2trash is installed, and
    protected system paths and drive roots are refused.
    """
    from tools.safety import SafetyValidator

    clean_loc = location.strip().lower()
    if clean_loc in ["desktop", "documents", "downloads", "pictures", "videos", "home"]:
        base = get_real_folder(clean_loc)
    else:
        base = Path(location)

    target = (base / name).resolve()
    if not target.exists():
        return {"success": False, "message": f"Target '{name}' does not exist in {base}."}

    protected = [p.lower() for p in SafetyValidator().protected_paths]
    if any(str(target).lower().startswith(p) for p in protected):
        return {"success": False, "error": f"Refusing to delete protected system path: {target}"}
    if target == Path(target.anchor) or len(target.parts) <= 2:
        return {"success": False, "error": "Refusing to delete a drive root or top-level folder."}

    try:
        from send2trash import send2trash
        send2trash(str(target))
        return {
            "success": True,
            "recoverable": True,
            "message": f"Moved '{name}' to the Recycle Bin.",
        }
    except ImportError:
        pass
    except Exception as e:
        return {"success": False, "error": f"Delete failed: {e}"}

    try:
        import shutil
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
        return {
            "success": True,
            "recoverable": False,
            "message": f"Deleted '{name}' permanently (send2trash is not installed).",
        }
    except Exception as e:
        return {"success": False, "error": f"Delete failed: {e}"}


def list_directory(location: str = "desktop") -> Dict[str, Any]:
    """
    Lists the files and folders in a specified directory.
    """
    clean_loc = location.strip().lower()
    if clean_loc in ["desktop", "documents", "downloads", "pictures", "videos", "music", "home"]:
        base = get_real_folder(clean_loc)
    else:
        base = Path(location)

    if not base.exists():
        return {"success": False, "error": f"Directory '{location}' not found."}
    try:
        items = os.listdir(base)
        display_items = items[:15]
        summary = ", ".join(display_items)
        if len(items) > 15:
            summary += f" and {len(items)-15} more"
        return {"success": True, "count": len(items), "items": items, "summary": summary}
    except Exception as e:
        return {"success": False, "error": f"Could not list directory: {e}"}


def empty_recycle_bin() -> Dict[str, Any]:
    """Empties the Windows Recycle Bin."""
    try:
        cmd = "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=5)
        return {"success": True, "message": "Recycle Bin emptied."}
    except Exception as e:
        return {"success": False, "error": f"Failed to empty Recycle Bin: {e}"}


def get_network_info() -> Dict[str, Any]:
    """Retrieves local IP address, hostname, and network status."""
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        return {"success": True, "hostname": hostname, "local_ip": local_ip}
    except Exception as e:
        return {"success": False, "error": str(e)}


def system_power(action: str, delay_sec: int = 0) -> Dict[str, Any]:
    """
    Controls Windows system power states: 'sleep', 'restart', 'shutdown', 'cancel'.
    """
    act = action.strip().lower()
    try:
        if act == "sleep":
            subprocess.run(["powershell", "-Command", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"], timeout=3)
            return {"success": True, "message": "Entering sleep mode."}
        elif act in ["restart", "reboot"]:
            cmd = f"shutdown /r /t {delay_sec}"
            subprocess.run(["cmd.exe", "/c", cmd], timeout=3)
            return {"success": True, "message": f"System reboot scheduled in {delay_sec} seconds."}
        elif act in ["shutdown", "shut down", "power off"]:
            cmd = f"shutdown /s /t {delay_sec}"
            subprocess.run(["cmd.exe", "/c", cmd], timeout=3)
            return {"success": True, "message": f"System shutdown scheduled in {delay_sec} seconds."}
        elif act in ["cancel", "abort"]:
            subprocess.run(["cmd.exe", "/c", "shutdown /a"], timeout=3)
            return {"success": True, "message": "Scheduled shutdown or restart aborted."}
        return {"success": False, "error": f"Unknown power action '{action}'."}
    except Exception as e:
        return {"success": False, "error": f"Power command failed: {e}"}


def set_screen_brightness(level_percent: int) -> Dict[str, Any]:
    """
    Sets the primary internal display brightness (0 to 100%) on Windows laptops/displays.

    Args:
        level_percent: Target brightness percentage between 0 and 100.
    """
    level = max(0, min(100, int(level_percent)))
    try:
        ps_cmd = (
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1, {level})"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if proc.returncode == 0:
            return {"success": True, "brightness": level, "message": f"Screen brightness set to {level}%."}
        err = (proc.stderr or proc.stdout or "").strip()
        return {
            "success": False,
            "error": err or "Display does not support WMI software brightness control (common on external desktop monitors).",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to adjust brightness: {e}"}


def get_top_processes(sort_by: str = "cpu", limit: int = 8) -> Dict[str, Any]:
    """
    Returns top resource-consuming running Windows processes by CPU or memory usage.

    Args:
        sort_by: Metric to rank by ('cpu' or 'ram'/'memory').
        limit: Maximum number of processes to list (1-25).
    """
    limit = max(1, min(25, int(limit)))
    by_ram = (sort_by or "cpu").strip().lower() in {"ram", "memory", "mem"}
    items: List[Dict[str, Any]] = []
    # Prime CPU measurement cleanly with a short delta
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            p.cpu_percent(interval=None)
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.15)
    for p in procs:
        try:
            name = p.info.get("name") or ""
            if not name or name.lower() in {"system idle process", "idle"}:
                continue
            cpu = round(p.cpu_percent(interval=None), 1)
            mem_info = p.info.get("memory_info")
            ram_mb = round((mem_info.rss if mem_info else 0) / (1024 * 1024), 1)
            items.append({"pid": p.info["pid"], "name": name, "cpu_percent": cpu, "ram_mb": ram_mb})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key_name = "ram_mb" if by_ram else "cpu_percent"
    items.sort(key=lambda row: row[key_name], reverse=True)
    top = items[:limit]
    summary = ", ".join(
        f"{r['name']} ({r['ram_mb']} MB)" if by_ram else f"{r['name']} ({r['cpu_percent']}% CPU, {r['ram_mb']} MB)"
        for r in top[:5]
    )
    return {"success": True, "sort_by": "ram" if by_ram else "cpu", "processes": top, "summary": summary}


def snap_window(direction: str, window_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Snaps or tiles the active or named window ('left', 'right', 'up'/'maximize', 'down'/'minimize').
    """
    from tools.gui_tools import focus_window, press_keys

    dir_clean = (direction or "left").strip().lower()
    mapping = {
        "left": ["win", "left"],
        "right": ["win", "right"],
        "up": ["win", "up"],
        "maximize": ["win", "up"],
        "down": ["win", "down"],
        "minimize": ["win", "down"],
    }
    keys = mapping.get(dir_clean)
    if not keys:
        return {"success": False, "error": f"Unsupported snap direction '{direction}'. Use left, right, maximize, or minimize."}

    target_label = "active window"
    if window_name:
        foc = focus_window(window_name)
        if not foc.get("success"):
            return foc
        target_label = foc.get("window") or window_name
        time.sleep(0.08)

    res = press_keys(keys)
    if res.get("success"):
        res["message"] = f"Snapped '{target_label}' {dir_clean}."
    return res


def manage_virtual_desktop(action: str = "new") -> Dict[str, Any]:
    """
    Manages Windows virtual desktops: 'new', 'next', 'prev', or 'close'.
    """
    from tools.gui_tools import press_keys

    act = (action or "new").strip().lower()
    combos = {
        "new": ["ctrl", "win", "d"],
        "create": ["ctrl", "win", "d"],
        "next": ["ctrl", "win", "right"],
        "right": ["ctrl", "win", "right"],
        "prev": ["ctrl", "win", "left"],
        "previous": ["ctrl", "win", "left"],
        "left": ["ctrl", "win", "left"],
        "close": ["ctrl", "win", "f4"],
    }
    keys = combos.get(act)
    if not keys:
        return {"success": False, "error": f"Unknown virtual desktop action '{action}'. Use new, next, prev, or close."}
    res = press_keys(keys)
    if res.get("success"):
        res["message"] = f"Virtual desktop action '{act}' executed."
    return res


_WINDOWS_SETTINGS_URIS: Dict[str, str] = {
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "network": "ms-settings:network-status",
    "bluetooth": "ms-settings:bluetooth",
    "display": "ms-settings:display",
    "screen": "ms-settings:display",
    "sound": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "battery": "ms-settings:batterysaver",
    "power": "ms-settings:powersleep",
    "update": "ms-settings:windowsupdate",
    "updates": "ms-settings:windowsupdate",
    "windows update": "ms-settings:windowsupdate",
    "apps": "ms-settings:appsfeatures",
    "storage": "ms-settings:storagesense",
    "privacy": "ms-settings:privacy",
    "about": "ms-settings:about",
    "default apps": "ms-settings:defaultapps",
    "notifications": "ms-settings:notifications",
}


def open_windows_setting(setting_name: str) -> Dict[str, Any]:
    """
    Opens a specific Windows Settings page directly (e.g. 'wifi', 'bluetooth', 'display', 'sound', 'battery', 'updates').
    """
    clean = (setting_name or "").strip().lower()
    uri = _WINDOWS_SETTINGS_URIS.get(clean, "ms-settings:")
    try:
        subprocess.run(["cmd.exe", "/c", "start", "", uri], capture_output=True, timeout=3)
        return {"success": True, "uri": uri, "message": f"Opened Windows {clean or 'main'} settings."}
    except Exception as e:
        return {"success": False, "error": f"Could not open settings '{setting_name}': {e}"}
