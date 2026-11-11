"""
Launcher for the TARS web dashboard.
Starts a local FastAPI server so TARS can be used without the terminal.

Usage:
  python run_dashboard.py
  python run_dashboard.py --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TARS local dashboard server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for dashboard development")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        import uvicorn
    except Exception:
        print("Missing dependency 'uvicorn'. Install dashboard dependencies with:")
        print("  pip install -r dashboard/requirements.txt")
        sys.exit(1)

    url = f"http://{args.host}:{args.port}"
    print(f"TARS Dashboard starting at {url}")
    print("Open the URL in your browser. Press Ctrl+C to stop.")
    uvicorn.run("dashboard.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
