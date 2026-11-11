"""Real filesystem inspection, navigation, and file management tools for Windows."""
import os
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import psutil

# Common text and source code extensions
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".html", ".htm", ".css", ".scss", ".xml", ".csv", ".ini", ".conf", ".cfg",
    ".env", ".log", ".bat", ".cmd", ".ps1", ".sh", ".toml", ".sql", ".rs", ".go",
    ".c", ".cpp", ".h", ".hpp", ".java", ".kt", ".rb", ".php"
}

# Known binary extensions
BINARY_EXTENSIONS = {
    ".exe", ".dll", ".bin", ".iso", ".dat", ".so", ".dylib", ".sys",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".mp3", ".wav", ".flac", ".mp4", ".mkv", ".avi", ".mov",
    ".pdf", ".docx", ".xlsx", ".pptx", ".db", ".sqlite"
}


def list_drives() -> Dict[str, Any]:
    """
    Lists all available Windows logical drives (e.g. C:\\, D:\\) with their mount status and free space.
    """
    drives = []
    try:
        for part in psutil.disk_partitions(all=False):
            mount = part.mountpoint
            try:
                usage = psutil.disk_usage(mount)
                free_gb = round(usage.free / (1024 ** 3), 2)
                total_gb = round(usage.total / (1024 ** 3), 2)
                drives.append({
                    "drive": mount,
                    "device": part.device,
                    "fstype": part.fstype,
                    "free_gb": free_gb,
                    "total_gb": total_gb,
                    "status": "ready"
                })
            except (PermissionError, OSError):
                drives.append({
                    "drive": mount,
                    "device": part.device,
                    "status": "offline_or_protected"
                })
    except Exception:
        # Fallback to manual drive letter scan
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{letter}:\\")
            if p.exists():
                drives.append({"drive": str(p), "status": "exists"})

    return {
        "success": True,
        "drives": drives,
        "count": len(drives),
        "summary": ", ".join([d["drive"] for d in drives])
    }


def navigate_to_path(target_path: str, open_in_explorer: bool = False) -> Dict[str, Any]:
    """
    Validates and resolves a drive or directory path on Windows.
    Checks drive existence, path existence, and directory permissions.
    If the target does not exist, inspects the parent drive/directory to provide recovery suggestions.
    """
    if not target_path or not isinstance(target_path, str):
        return {"success": False, "error": "Invalid or empty target path provided."}

    raw = target_path.strip()

    # Normalize drive-only requests (e.g. "D", "D:", "D:\", "d drive")
    import re
    drive_match = re.search(r"^(?:the\s+)?([a-zA-Z])(?:\s*(?:drive|disk)|:?\\?)$", raw, re.IGNORECASE)
    if drive_match:
        drive_letter = drive_match.group(1).upper()
        p = Path(f"{drive_letter}:\\")
        if not p.exists():
            return {
                "success": False,
                "path": str(p),
                "error": f"Drive {drive_letter}: does not exist on this system.",
                "available_drives": [d["drive"] for d in list_drives()["drives"]]
            }
        if open_in_explorer:
            try:
                os.startfile(str(p))
            except Exception as e:
                return {"success": True, "path": str(p), "exists": True, "warning": f"Could not launch explorer: {e}"}
        return {
            "success": True,
            "path": str(p),
            "is_dir": True,
            "exists": True,
            "message": f"Successfully resolved drive {str(p)}."
        }

    # Resolve full target path
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    
    # If relative, check relative to current working directory
    if not p.is_absolute():
        p = Path.cwd() / p

    # 1. Check drive presence
    drive_part = p.drive
    if drive_part:
        drive_root = Path(f"{drive_part}\\")
        if not drive_root.exists():
            return {
                "success": False,
                "path": str(p),
                "error": f"Target drive '{drive_part}' does not exist on this Windows machine.",
                "available_drives": [d["drive"] for d in list_drives()["drives"]]
            }

    # 2. Check path presence
    if not p.exists():
        # Inspect parent to provide accurate diagnostics
        parent = p.parent
        nearby_folders = []
        if parent.exists() and parent.is_dir():
            try:
                nearby_folders = [item.name for item in parent.iterdir() if item.is_dir()][:10]
            except Exception:
                pass
        return {
            "success": False,
            "path": str(p),
            "error": f"Directory '{p}' does not exist.",
            "parent_directory": str(parent),
            "parent_exists": parent.exists(),
            "available_folders_in_parent": nearby_folders,
            "suggestion": f"Folder '{p.name}' was not found in '{parent}'. Available folders: {', '.join(nearby_folders) if nearby_folders else 'None'}."
        }

    if open_in_explorer:
        try:
            os.startfile(str(p))
        except Exception as e:
            return {"success": True, "path": str(p), "exists": True, "warning": f"Could not launch Explorer: {e}"}

    return {
        "success": True,
        "path": str(p),
        "exists": True,
        "is_dir": p.is_dir(),
        "is_file": p.is_file(),
        "message": f"Successfully resolved '{p}'."
    }


def list_directory(location: str = ".") -> Dict[str, Any]:
    """
    Retrieves the contents of a directory, clearly separating subdirectories from files.
    Includes item count, file sizes, and handles non-existent or inaccessible folders.
    """
    # Check if target is a simple drive or natural language folder
    from tools.os_tools import resolve_system_target
    resolved = resolve_system_target(location)
    target = resolved if resolved else Path(os.path.expandvars(os.path.expanduser(location)))

    if not target.is_absolute():
        target = Path.cwd() / target

    # Validate target exists
    if not target.exists():
        # Check if parent exists to give useful feedback
        parent = target.parent
        available = []
        if parent.exists():
            try:
                available = [x.name for x in parent.iterdir() if x.is_dir()][:10]
            except Exception:
                pass
        return {
            "success": False,
            "path": str(target),
            "error": f"Directory '{target}' does not exist.",
            "available_subdirectories": available
        }

    if not target.is_dir():
        return {
            "success": False,
            "path": str(target),
            "error": f"Target path '{target}' is a file, not a directory."
        }

    try:
        subdirectories = []
        files = []

        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                if item.is_dir():
                    subdirectories.append(item.name)
                else:
                    size_bytes = item.stat().st_size
                    if size_bytes < 1024:
                        sz_str = f"{size_bytes} B"
                    elif size_bytes < 1024 * 1024:
                        sz_str = f"{round(size_bytes / 1024, 1)} KB"
                    else:
                        sz_str = f"{round(size_bytes / (1024 * 1024), 1)} MB"
                    files.append({"name": item.name, "size": sz_str, "extension": item.suffix.lower()})
            except (PermissionError, OSError):
                continue

        # Format human-readable summary
        dir_names = subdirectories[:12]
        file_names = [f["name"] for f in files[:12]]
        
        summary_parts = []
        if dir_names:
            summary_parts.append(f"{len(subdirectories)} folder(s): {', '.join(dir_names)}")
        if file_names:
            summary_parts.append(f"{len(files)} file(s): {', '.join(file_names)}")

        summary = " | ".join(summary_parts) if summary_parts else "Directory is empty."

        return {
            "success": True,
            "path": str(target),
            "total_items": len(subdirectories) + len(files),
            "subdirectories_count": len(subdirectories),
            "files_count": len(files),
            "subdirectories": subdirectories,
            "files": files,
            "summary": summary
        }

    except PermissionError:
        return {"success": False, "path": str(target), "error": f"Access denied to directory '{target}'."}
    except Exception as e:
        return {"success": False, "path": str(target), "error": f"Failed to list directory '{target}': {str(e)}"}


def read_file(path: str, max_chars: int = 8000) -> Dict[str, Any]:
    """
    Reads supported plain-text and source-code files on Windows.
    Validates file existence, format compatibility, and handles binary files safely.
    """
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.is_absolute():
        p = Path.cwd() / p

    if not p.exists():
        return {"success": False, "path": str(p), "error": f"File '{p}' does not exist."}

    if p.is_dir():
        return {"success": False, "path": str(p), "error": f"Path '{p}' is a directory, not a file."}

    # Format check: Reject known binary formats safely
    suffix = p.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        stat = p.stat()
        return {
            "success": False,
            "path": str(p),
            "error": f"Cannot read binary file format '{suffix}'. Only text and source code files are supported.",
            "file_size_bytes": stat.st_size,
            "is_binary": True
        }

    try:
        # Detect if file is binary by inspecting first 1024 bytes
        with open(p, "rb") as f:
            chunk = f.read(1024)
            if b"\x00" in chunk:
                return {
                    "success": False,
                    "path": str(p),
                    "error": f"File '{p.name}' contains binary null bytes and cannot be read as plain text.",
                    "is_binary": True
                }

        content = p.read_text(encoding="utf-8", errors="replace")
        total_len = len(content)
        truncated = False
        if total_len > max_chars:
            content = content[:max_chars] + f"\n... [Truncated: {total_len - max_chars} characters omitted]"
            truncated = True

        return {
            "success": True,
            "path": str(p),
            "name": p.name,
            "extension": suffix,
            "size_bytes": p.stat().st_size,
            "content": content,
            "truncated": truncated
        }

    except PermissionError:
        return {"success": False, "path": str(p), "error": f"Access denied: insufficient permissions to read '{p}'."}
    except Exception as e:
        return {"success": False, "path": str(p), "error": f"Failed to read file '{p}': {str(e)}"}


def get_file_metadata(path: str) -> Dict[str, Any]:
    """
    Retrieves detailed filesystem metadata for a file or directory.
    """
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.is_absolute():
        p = Path.cwd() / p

    if not p.exists():
        return {"success": False, "path": str(p), "error": f"Path '{p}' does not exist."}

    try:
        st = p.stat()
        return {
            "success": True,
            "path": str(p),
            "name": p.name,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "size_bytes": st.st_size,
            "created_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_ctime)),
            "modified_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "is_readonly": not os.access(p, os.W_OK)
        }
    except Exception as e:
        return {"success": False, "path": str(p), "error": str(e)}


def search_files(root: str, pattern: str, max_results: int = 50) -> Dict[str, Any]:
    """
    Searches for files matching a pattern recursively under the specified root directory.
    """
    root_path = Path(os.path.expandvars(os.path.expanduser(root)))
    if not root_path.exists():
        return {"success": False, "error": f"Root directory '{root}' does not exist."}

    results = []
    try:
        for p in root_path.rglob(pattern):
            try:
                results.append({
                    "path": str(p),
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size_bytes": p.stat().st_size if p.is_file() else 0
                })
                if len(results) >= max_results:
                    break
            except (PermissionError, OSError):
                continue

        return {
            "success": True,
            "root": str(root_path),
            "pattern": pattern,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        return {"success": False, "error": f"Search failed: {str(e)}"}
