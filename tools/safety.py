"""Safety and security validator for TARS execution commands."""
import json
import re
from pathlib import Path
from typing import Tuple, Optional


class SafetyValidator:
    """Validates commands and actions to prevent unintended system destruction."""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "config" / "safety_rules.json"
        
        self.config_path = config_path
        self.blocked_commands = []
        self.confirmation_patterns = []
        self.protected_paths = []
        self._load_rules()

    def _load_rules(self) -> None:
        """Loads safety rules from JSON configuration."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.blocked_commands = [c.lower().strip() for c in data.get("blocked_commands", [])]
                    self.confirmation_patterns = [
                        re.compile(p, re.IGNORECASE) for p in data.get("confirmation_required_patterns", [])
                    ]
                    self.protected_paths = [p.lower() for p in data.get("protected_paths", [])]
            except Exception as e:
                print(f"[Warning] Failed to load safety rules: {e}")

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalizes whitespace and case for reliable matching."""
        return re.sub(r"\s+", " ", text.strip()).lower()

    def evaluate_command(self, command: str) -> Tuple[bool, bool, str]:
        """
        Evaluates a shell/OS command.
        
        Returns:
            (is_allowed: bool, requires_confirmation: bool, reason: str)
        """
        cmd_clean = self._normalize(command)

        # 1. Check outright blocked commands
        for blocked in self.blocked_commands:
            normalized_blocked = self._normalize(blocked)
            if normalized_blocked in cmd_clean:
                return False, False, f"Command contains blocked destructive sequence: '{blocked}'"

        # 2. Check protected system paths
        for path in self.protected_paths:
            normalized_path = self._normalize(path)
            if normalized_path in cmd_clean and any(destructive in cmd_clean for destructive in ["rm", "del", "remove", "rd"]):
                return False, False, f"Attempted deletion targeting protected system path: '{path}'"

        # 3. Check patterns that require human confirmation
        for pattern in self.confirmation_patterns:
            if pattern.search(cmd_clean) or pattern.search(command):
                return True, True, f"Command matches sensitive pattern '{pattern.pattern}'. Confirmation required."

        return True, False, "Command is safe to execute."


_default_safety_validator = SafetyValidator()


def check_safety_rules(command: str) -> Tuple[bool, str, bool]:
    """
    Evaluates a command against safety rules.
    Returns:
        (is_allowed: bool, reason: str, requires_confirmation: bool)
    """
    is_allowed, req_confirm, reason = _default_safety_validator.evaluate_command(command)
    return is_allowed, reason, req_confirm

