"""FastAPI backend for the TARS dashboard (no changes to existing TARS modules)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure project root is importable and cwd for relative paths (memory.db, logs, config)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import tools  # noqa: E402
from core.brain import TARSAgent  # noqa: E402
from core.scheduler import get_default_scheduler  # noqa: E402

app = FastAPI(title="TARS Dashboard", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
_agent_lock = threading.Lock()
_agent: Optional[TARSAgent] = None


class ChatRequest(BaseModel):
    prompt: str


def get_agent() -> TARSAgent:
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = TARSAgent()
                # Dashboard cannot interactively confirm destructive actions; require explicit confirmation flow client-side if needed.
                _agent.set_confirmation_callback(lambda tool, args: False)
    return _agent


def _read_jsonl_tail(path: Path, limit: int = 50) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    items: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            items.append({"raw": line})
    return list(reversed(items))


def _sqlite_rows(db_path: Path, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "service": "tars-dashboard", "time": datetime.now().isoformat()}


@app.get("/api/status")
def status() -> Dict[str, Any]:
    agent = get_agent()
    telemetry = {}
    try:
        telemetry = tools.get_system_telemetry()
    except Exception as e:
        telemetry = {"error": str(e)}
    p = agent.settings.get("personality", {})
    return {
        "online": True,
        "assistant": agent.settings.get("assistant", {"name": "TARS"}),
        "personality": {
            "humor": int(float(p.get("humor", 0.75)) * 100),
            "honesty": int(float(p.get("honesty", 0.90)) * 100),
            "sarcasm": int(float(p.get("sarcasm", 0.60)) * 100),
        },
        "llm": {
            "provider": getattr(agent, "llm", None) and agent.llm.provider or agent.settings.get("llm", {}).get("provider"),
            "model": getattr(agent, "llm", None) and agent.llm.model or agent.settings.get("llm", {}).get("model"),
            "available": bool(getattr(agent, "client", None)) or bool(getattr(getattr(agent, "llm", None), "available", lambda: False)()),
        },
        "telemetry": telemetry,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cwd": str(Path.cwd()),
    }


@app.get("/api/history")
def history(limit: int = 30) -> Dict[str, Any]:
    limit = max(1, min(limit, 200))
    rows = _sqlite_rows(
        ROOT / "memory.db",
        "SELECT id, timestamp, role, content, tool_calls FROM conversation_history ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    for r in rows:
        tc = r.get("tool_calls")
        if isinstance(tc, str):
            try:
                r["tool_calls"] = json.loads(tc or "[]")
            except Exception:
                r["tool_calls"] = []
        ts = r.get("timestamp")
        if ts:
            try:
                r["datetime"] = datetime.fromtimestamp(float(ts)).isoformat()
            except Exception:
                r["datetime"] = None
    return {"items": list(reversed(rows))}


@app.get("/api/preferences")
def preferences() -> Dict[str, Any]:
    rows = _sqlite_rows(
        ROOT / "memory.db",
        "SELECT key, value, updated_at FROM user_memories ORDER BY updated_at DESC",
    )
    for r in rows:
        v = r.get("value")
        if isinstance(v, str):
            try:
                r["value"] = json.loads(v)
            except Exception:
                pass
    return {"items": rows}


@app.get("/api/tool-logs")
def tool_logs(limit: int = 50) -> Dict[str, Any]:
    limit = max(1, min(limit, 200))
    items = _read_jsonl_tail(ROOT / "logs" / "tool_calls.jsonl", limit=limit)
    return {"items": items}


@app.get("/api/reminders")
def reminders() -> Dict[str, Any]:
    try:
        scheduler = get_default_scheduler()
        items = scheduler.list_reminders()
        return {"success": True, "items": items}
    except Exception as e:
        return {"success": False, "error": str(e), "items": []}


@app.get("/api/screenshot")
def screenshot() -> Any:
    try:
        res = tools.take_screenshot()
        path = res.get("path") if isinstance(res, dict) else None
        if path and Path(path).exists():
            return FileResponse(path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="Screenshot unavailable")


@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict[str, Any]:
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    agent = get_agent()
    statuses: List[str] = []

    def on_status(msg: str) -> None:
        statuses.append(msg)

    t0 = time.time()
    with _agent_lock:
        result = agent.run_agent_loop(prompt, on_status_update=on_status)
    elapsed = round(time.time() - t0, 3)
    # Ensure JSON-serializable result
    try:
        json.dumps(result, default=str)
        safe_result = result
    except Exception:
        safe_result = json.loads(json.dumps(result, default=str))
    return {
        "prompt": prompt,
        "response": safe_result.get("response"),
        "success": safe_result.get("success"),
        "steps": safe_result.get("steps", []),
        "statuses": statuses,
        "duration_sec": elapsed,
    }


@app.get("/")
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI not found")
    return FileResponse(index_path)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
