"""Real filesystem inspection, navigation, and file management tools for Windows."""
import os
import re
import shutil
import time
import zipfile
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


def _resolve_user_path(raw_path: str) -> Path:
    """Resolves user shell aliases, env vars, and relative paths into an absolute Path."""
    from tools.os_tools import get_real_folder

    clean = (raw_path or "").strip()
    parts = re.split(r"[\\/]", clean, maxsplit=1) if clean else []
    alias = parts[0].lower() if parts else ""
    if alias in {"desktop", "documents", "downloads", "pictures", "videos", "music", "home"}:
        base = get_real_folder(alias)
        return (base / parts[1]).resolve() if len(parts) == 2 and parts[1] else base.resolve()

    expanded = Path(os.path.expandvars(os.path.expanduser(clean)))
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.resolve()


def _is_protected_path(target: Path) -> bool:
    """True when ``target`` points inside a critical OS/system directory or drive root."""
    from tools.safety import SafetyValidator

    if target == Path(target.anchor) or len(target.parts) <= 1:
        return True
    resolved_target = target.resolve()
    for raw_p in SafetyValidator().protected_paths:
        prot_path = Path(raw_p).resolve()
        if resolved_target == prot_path or resolved_target.is_relative_to(prot_path):
            return True
    return False


def write_or_append_file(path: str, content: str, append: bool = False) -> Dict[str, Any]:
    """
    Writes or appends UTF-8 text content to a file safely.

    Args:
        path: Target file path (absolute, CWD-relative, or starting with desktop/documents/downloads).
        content: Text content to write or append.
        append: When True, appends to existing content instead of overwriting.
    """
    if not path or not isinstance(path, str):
        return {"success": False, "error": "Target file path is required."}

    target = _resolve_user_path(path)
    if _is_protected_path(target):
        return {"success": False, "error": f"Refusing to modify protected system path: {target}"}

    if target.exists() and target.is_dir():
        return {"success": False, "error": f"Path '{target}' is a directory, not a file."}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as handle:
            handle.write(content or "")
        return {
            "success": True,
            "path": str(target),
            "mode": "appended" if append else "written",
            "bytes_written": len((content or "").encode("utf-8")),
            "message": f"{'Appended to' if append else 'Wrote'} file '{target.name}'.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to write file '{target}': {e}"}


def copy_or_move_item(source: str, destination: str, move: bool = False) -> Dict[str, Any]:
    """
    Copies or moves a file or folder safely with protected-path validation.

    Args:
        source: Path of the file or directory to copy or move.
        destination: Destination path or directory.
        move: When True, moves/renames the item instead of copying it.
    """
    if not source or not destination:
        return {"success": False, "error": "Both source and destination paths are required."}

    src = _resolve_user_path(source)
    dst = _resolve_user_path(destination)

    if not src.exists():
        return {"success": False, "error": f"Source '{src}' does not exist."}
    if _is_protected_path(src) or _is_protected_path(dst):
        return {"success": False, "error": "Refusing to modify or target a protected system path or drive root."}

    try:
        if dst.exists() and dst.is_dir():
            final_dst = dst / src.name
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            final_dst = dst

        if move:
            shutil.move(str(src), str(final_dst))
            verb = "Moved"
        elif src.is_dir():
            shutil.copytree(str(src), str(final_dst), dirs_exist_ok=True)
            verb = "Copied"
        else:
            shutil.copy2(str(src), str(final_dst))
            verb = "Copied"

        return {
            "success": True,
            "source": str(src),
            "destination": str(final_dst),
            "action": verb.lower(),
            "message": f"{verb} '{src.name}' to '{final_dst}'.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to {'move' if move else 'copy'} '{src}': {e}"}


def zip_or_unzip(source: str, destination: Optional[str] = None, action: str = "zip") -> Dict[str, Any]:
    """
    Compresses a file/folder into a .zip archive or extracts an existing .zip archive.

    Args:
        source: Source file/folder (for zip) or .zip archive path (for unzip).
        destination: Optional output archive path or extraction directory.
        action: Either 'zip' (compress) or 'unzip' / 'extract'.
    """
    src = _resolve_user_path(source)
    if not src.exists():
        return {"success": False, "error": f"Source '{src}' does not exist."}

    act = (action or "zip").strip().lower()
    if act in {"unzip", "extract"}:
        if not zipfile.is_zipfile(src):
            return {"success": False, "error": f"'{src}' is not a valid zip archive."}
        extract_dir = _resolve_user_path(destination) if destination else src.with_suffix("")
        if _is_protected_path(extract_dir):
            return {"success": False, "error": f"Refusing to extract into protected path: {extract_dir}"}
        try:
            extract_dir.mkdir(parents=True, exist_ok=True)
            base_resolved = extract_dir.resolve()
            with zipfile.ZipFile(src, "r") as zf:
                for member in zf.infolist():
                    member_target = (base_resolved / member.filename).resolve()
                    if not member_target.is_relative_to(base_resolved):
                        return {"success": False, "error": f"Blocked unsafe zip path traversal: {member.filename}"}
                zf.extractall(base_resolved)
            return {
                "success": True,
                "source": str(src),
                "destination": str(base_resolved),
                "message": f"Extracted '{src.name}' into '{base_resolved}'.",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to extract archive: {e}"}

    out_zip = _resolve_user_path(destination) if destination else src.with_suffix(".zip")
    if out_zip.suffix.lower() != ".zip":
        out_zip = out_zip.with_suffix(".zip")
    if _is_protected_path(out_zip):
        return {"success": False, "error": f"Refusing to write archive into protected path: {out_zip}"}

    try:
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if src.is_dir():
                for item in src.rglob("*"):
                    if item.is_file():
                        zf.write(item, arcname=item.relative_to(src.parent))
            else:
                zf.write(src, arcname=src.name)
        return {
            "success": True,
            "source": str(src),
            "archive_path": str(out_zip),
            "message": f"Created archive '{out_zip.name}'.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to create zip archive: {e}"}
