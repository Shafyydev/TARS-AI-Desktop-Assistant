# TARS Dashboard

A local web dashboard to control and monitor TARS without using the terminal.  
This is an add-on and does **not** modify existing TARS code.

## Install dashboard dependencies

```powershell
pip install -r dashboard/requirements.txt
```

(Install core TARS dependencies first via `pip install -r requirements.txt` if you have not already.)

## Run

```powershell
python run_dashboard.py
```

Then open: http://127.0.0.1:8765

Options:

```powershell
python run_dashboard.py --host 127.0.0.1 --port 8765
```

## What it provides

- **Command console:** send prompts to the TARS agent (`TARSAgent.run_agent_loop`) and view responses + executed steps.
- **System telemetry:** CPU, RAM, disk, battery and TARS personality/LLM status via existing tools and config.
- **Conversation history:** recent messages from `memory.db`.
- **Reminders:** upcoming scheduled reminders from the existing scheduler store.
- **Preferences:** stored long-term facts from memory.
- **Tool call log:** tail of `logs/tool_calls.jsonl`.

## Notes & safety

- Runs locally and binds to `127.0.0.1` by default.
- Destructive tool confirmations are **denied** in dashboard mode (no interactive prompt channel), so commands requiring confirmation will be blocked rather than executed silently.
- Voice I/O, wake word, and terminal UI remain available via existing entry points (`python main.py`, `--voice`, etc.).
