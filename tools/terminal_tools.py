"""Terminal and PowerShell execution tools for TARS."""
import subprocess
import time
from typing import Dict, Any, Optional
from tools.safety import SafetyValidator

_safety = SafetyValidator()
MAX_OUTPUT_CHARS = 8000

_last_command_output = {
    "command": "",
    "stdout": "",
    "stderr": "",
    "exit_code": 0,
    "timestamp": 0.0
}


def run_approved_command(command: str, shell: str = "powershell", timeout: int = 30) -> Dict[str, Any]:
    """
    Executes an approved Windows command (via PowerShell or CMD) after safety protocol validation.
    Caches execution output for subsequent retrieval.
    """
    global _last_command_output
    if shell.lower() in ["cmd", "cmd.exe"]:
        res = run_cmd(command, timeout=timeout)
    else:
        res = run_powershell(command, timeout=timeout)

    _last_command_output = {
        "command": command,
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
        "exit_code": res.get("exit_code", 0),
        "timestamp": time.time()
    }
    return res


def get_command_output(last_n_chars: int = 4000) -> Dict[str, Any]:
    """
    Retrieves the output, exit code, and execution status of the last executed command.
    """
    stdout = _last_command_output.get("stdout", "")
    if len(stdout) > last_n_chars:
        stdout = stdout[-last_n_chars:]
    return {
        "success": _last_command_output.get("exit_code") == 0,
        "command": _last_command_output.get("command", ""),
        "stdout": stdout,
        "stderr": _last_command_output.get("stderr", ""),
        "exit_code": _last_command_output.get("exit_code", 0),
        "timestamp": _last_command_output.get("timestamp", 0.0)
    }


def run_powershell(command: str, timeout: int = 30, cwd: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a PowerShell command asynchronously with timeout and safety checks.
    
    Args:
        command: The PowerShell command string.
        timeout: Maximum seconds before terminating process.
        cwd: Working directory (defaults to current process directory).
        
    Returns:
        Dictionary with exit_code, stdout, stderr, execution_time, and status.
    """
    # Safety Check
    is_allowed, req_confirm, reason = _safety.evaluate_command(command)
    if not is_allowed:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"BLOCKED BY SAFETY PROTOCOL: {reason}",
            "requires_confirmation": False
        }
    
    if req_confirm:
        return {
            "success": False,
            "exit_code": -2,
            "stdout": "",
            "stderr": f"CONFIRMATION REQUIRED: {reason}",
            "requires_confirmation": True
        }

    start_time = time.time()
    try:
        # Launch PowerShell with UTF-8 encoding support
        ps_command = f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {command}"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + f"\n... [Truncated: {len(stdout) - MAX_OUTPUT_CHARS} characters omitted]"

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "execution_time_seconds": round(time.time() - start_time, 3),
            "requires_confirmation": False
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -3,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds.",
            "execution_time_seconds": round(time.time() - start_time, 3),
            "requires_confirmation": False
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -4,
            "stdout": "",
            "stderr": f"Execution error: {str(e)}",
            "execution_time_seconds": round(time.time() - start_time, 3),
            "requires_confirmation": False
        }


def run_cmd(command: str, timeout: int = 30, cwd: Optional[str] = None) -> Dict[str, Any]:
    """Runs a standard Windows Command Prompt (cmd.exe) command."""
    is_allowed, req_confirm, reason = _safety.evaluate_command(command)
    if not is_allowed:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"BLOCKED BY SAFETY PROTOCOL: {reason}",
            "requires_confirmation": False
        }

    if req_confirm:
        return {
            "success": False,
            "exit_code": -2,
            "stdout": "",
            "stderr": f"CONFIRMATION REQUIRED: {reason}",
            "requires_confirmation": True
        }

    start_time = time.time()
    try:
        result = subprocess.run(
            f"cmd.exe /c {command}",
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd
        )

        stdout = result.stdout.strip()
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + "\n... [Truncated]"

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": result.stderr.strip(),
            "execution_time_seconds": round(time.time() - start_time, 3),
            "requires_confirmation": False
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -4,
            "stdout": "",
            "stderr": str(e),
            "execution_time_seconds": round(time.time() - start_time, 3),
            "requires_confirmation": False
        }
