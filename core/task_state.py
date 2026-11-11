"""Task state, step tracking, and execution context management for TARS."""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import time


@dataclass
class TaskStep:
    index: int
    description: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    status: str = "planned"  # "planned" | "running" | "done" | "failed" | "skipped"
    result: Any = None
    error: Optional[str] = None
    attempts: int = 0
    verification: Optional[Dict[str, Any]] = None


@dataclass
class TaskState:
    request: str
    objective: str
    steps: List[TaskStep] = field(default_factory=list)
    current_index: int = 0
    completed: List[int] = field(default_factory=list)
    failed: List[int] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    retry_limit: int = 2
    cancelled: bool = False
    verified: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    final_response: str = ""

    def current_step(self) -> Optional[TaskStep]:
        if 0 <= self.current_index < len(self.steps):
            return self.steps[self.current_index]
        return None

    def remaining_steps(self) -> List[TaskStep]:
        return [s for s in self.steps if s.index >= self.current_index and s.status == "planned"]

    def advance(self) -> None:
        self.current_index += 1

    def mark_done(self, idx: int, result: Any, verification: Optional[Dict[str, Any]] = None) -> None:
        if 0 <= idx < len(self.steps):
            self.steps[idx].status = "done"
            self.steps[idx].result = result
            self.steps[idx].verification = verification
            if idx not in self.completed:
                self.completed.append(idx)

    def mark_failed(self, idx: int, error: str) -> None:
        if 0 <= idx < len(self.steps):
            self.steps[idx].status = "failed"
            self.steps[idx].error = error
            if idx not in self.failed:
                self.failed.append(idx)

    def is_finished(self) -> bool:
        return self.cancelled or (self.current_index >= len(self.steps) and len(self.steps) > 0)

    def finish(self, response: str, success: bool = True) -> None:
        self.finished_at = time.time()
        self.final_response = response
        self.verified = success

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request,
            "objective": self.objective,
            "steps": [s.__dict__ for s in self.steps],
            "current_index": self.current_index,
            "completed": self.completed,
            "failed": self.failed,
            "context": self.context,
            "cancelled": self.cancelled,
            "verified": self.verified,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "final_response": self.final_response
        }
