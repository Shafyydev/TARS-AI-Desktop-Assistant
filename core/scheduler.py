"""Persistent reminder and timer scheduler for TARS using APScheduler and SQLite."""
import os
import time
import uuid
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logger = logging.getLogger("TARS.Scheduler")

# Global alert speaker callback
_alert_callback: Optional[Callable[[str], None]] = None


def set_alert_callback(callback: Callable[[str], None]) -> None:
    """Sets the spoken/notification callback when a reminder triggers."""
    global _alert_callback
    _alert_callback = callback


def _trigger_reminder_alert(reminder_id: str, message: str) -> None:
    """Invoked by APScheduler when a reminder fires."""
    alert_text = f"Reminder: {message}"
    logger.info(f"[ALARM] {alert_text}")
    print(f"\n🔔 [REMINDER ALERT] {alert_text}\n")
    if _alert_callback:
        try:
            _alert_callback(alert_text)
        except Exception as e:
            logger.error(f"Error in reminder alert callback: {e}")


class ReminderScheduler:
    """
    Manages timers and reminders that persist across restarts in a SQLite database.
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "reminders.db"

        self.db_path = str(db_path)
        self.meta_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_meta_db()

        jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{self.db_path}")
        }
        self.scheduler = BackgroundScheduler(jobstores=jobstores, daemon=True)
        try:
            self.scheduler.start()
        except Exception as e:
            logger.error(f"Failed to start APScheduler: {e}")

    def _init_meta_db(self) -> None:
        """Initializes metadata table for reminders."""
        with self.meta_conn:
            cursor = self.meta_conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminder_metadata (
                    id TEXT PRIMARY KEY,
                    message TEXT,
                    due_timestamp REAL,
                    created_at REAL,
                    status TEXT
                )
            """)

    def add_reminder(self, message: str, delay_seconds: float) -> Dict[str, Any]:
        """Schedules a new reminder to fire after delay_seconds."""
        job_id = f"rem_{uuid.uuid4().hex[:8]}"
        run_date = datetime.now() + timedelta(seconds=max(1.0, delay_seconds))
        due_ts = run_date.timestamp()

        # Save metadata
        with self.meta_conn:
            cursor = self.meta_conn.cursor()
            cursor.execute(
                "INSERT INTO reminder_metadata (id, message, due_timestamp, created_at, status) VALUES (?, ?, ?, ?, 'active')",
                (job_id, message, due_ts, time.time())
            )

        # Schedule persistent job
        self.scheduler.add_job(
            _trigger_reminder_alert,
            trigger="date",
            run_date=run_date,
            args=[job_id, message],
            id=job_id,
            replace_existing=True
        )

        due_time_str = run_date.strftime("%I:%M %p")
        return {
            "success": True,
            "id": job_id,
            "message": message,
            "due_time": due_time_str,
            "delay_seconds": delay_seconds
        }

    def list_reminders(self) -> List[Dict[str, Any]]:
        """Lists all upcoming scheduled reminders."""
        now_ts = time.time()
        cursor = self.meta_conn.cursor()
        cursor.execute(
            "SELECT id, message, due_timestamp FROM reminder_metadata WHERE due_timestamp > ? ORDER BY due_timestamp ASC",
            (now_ts,)
        )
        rows = cursor.fetchall()
        results = []
        for r_id, msg, due_ts in rows:
            dt = datetime.fromtimestamp(due_ts)
            results.append({
                "id": r_id,
                "message": msg,
                "due_time": dt.strftime("%Y-%m-%d %I:%M %p"),
                "seconds_remaining": max(0, int(due_ts - now_ts))
            })
        return results

    def cancel_reminder(self, job_id: str) -> bool:
        """Cancels a scheduled reminder by its ID."""
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

        with self.meta_conn:
            cursor = self.meta_conn.cursor()
            cursor.execute("DELETE FROM reminder_metadata WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    def shutdown(self) -> None:
        """Shuts down the scheduler and closes database."""
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        if self.meta_conn:
            self.meta_conn.close()


# Singleton instance
_default_scheduler: Optional[ReminderScheduler] = None


def get_default_scheduler() -> ReminderScheduler:
    """Returns the singleton ReminderScheduler instance."""
    global _default_scheduler
    if _default_scheduler is None:
        _default_scheduler = ReminderScheduler()
    return _default_scheduler


# Tool interfaces for TARS agent and LLM calling
def set_reminder(message: str, minutes: float = 1.0) -> Dict[str, Any]:
    """
    Sets a timer or reminder with spoken alert after a specified delay in minutes.

    Args:
        message: The task or reminder description (e.g. 'check oven', 'team standup').
        minutes: The delay duration in minutes (e.g. 5, 0.5, 30).
    """
    scheduler = get_default_scheduler()
    delay_sec = max(1.0, float(minutes) * 60.0)
    res = scheduler.add_reminder(message, delay_sec)
    return {
        "success": True,
        "reminder_id": res["id"],
        "message": f"Reminder set for '{message}' at {res['due_time']} ({minutes} min).",
        "due_time": res["due_time"]
    }


def list_active_reminders() -> Dict[str, Any]:
    """Lists all upcoming active reminders and timers."""
    scheduler = get_default_scheduler()
    items = scheduler.list_reminders()
    return {
        "success": True,
        "count": len(items),
        "reminders": items
    }


def cancel_active_reminder(reminder_id: str) -> Dict[str, Any]:
    """Cancels an existing scheduled reminder by ID."""
    scheduler = get_default_scheduler()
    removed = scheduler.cancel_reminder(reminder_id)
    return {
        "success": removed,
        "message": f"Reminder '{reminder_id}' cancelled." if removed else f"Reminder '{reminder_id}' not found."
    }
