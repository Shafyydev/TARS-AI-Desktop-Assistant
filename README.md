# TARS — Autonomous Voice-Driven AI Desktop Assistant 🤖

> *"Honesty, new parameter: ninety percent. Humor: seventy-five percent."* — TARS, *Interstellar*

**TARS** (Tactical Autonomous Robotic System) is a practical, voice-controlled, autonomous AI desktop assistant capable of understanding natural-language commands, planning multi-step tasks, controlling native Windows applications, interacting with websites, managing files, executing approved commands, inspecting results, recovering from errors, and reporting task completion through speech.

---

## ⚡ Architecture & Execution Loop

```mermaid
graph TD
    User["🗣️ Voice Command / Keyboard Input"] --> Listener["VoiceListener (Acoustic VAD / STT)"]
    Listener --> FastPath{"Fast-Path Heuristics?<br/>(<1ms)"}
    
    FastPath -- "Yes (Volume, Media, Hotkeys)" --> ExecFast["Direct Tool Execution"]
    FastPath -- "No (Compound / Novel Tasks)" --> Orchestrator["TaskOrchestrator & TaskState"]
    
    Orchestrator --> LLM["LLMProvider (Gemini 3.8 Flash ReAct Engine)"]
    LLM --> ToolRegistry["Central Tool Registry (78 Tools)"]
    
    subgraph Execution Engines [TARS Execution 'Hands']
        ToolRegistry --> Term["💻 Terminal Engine (PowerShell / Shell / Safety)"]
        ToolRegistry --> OS["🎛️ Windows OS Engine (Apps, Audio, Explorer, Power)"]
        ToolRegistry --> UIA["🪟 Windows UI Automation (pywinauto UIA Semantic)"]
        ToolRegistry --> GUI["🖱️ Computer Use & GUI (Mouse, Keyboard, Vision)"]
        ToolRegistry --> Web["🌐 Web Automation (Playwright Native Brave / Chrome)"]
        ToolRegistry --> FS["📁 Real Filesystem Engine (Pathlib, Diagnostics)"]
    end

    Execution Engines --> Verify["Observation, Verification & Error Recovery"]
    Verify --> Context["Task-Scoped Context Memory"]
    Context --> Speaker["🔊 VoiceSpeaker (SAPI / Neural TTS with Barge-In)"]
```

---

## 🛠️ Implemented Tool Ecosystem (78 Registered Tools)

### 1. Terminal & Execution Security
- `run_powershell(command, timeout, cwd)`: Executes PowerShell commands with UTF-8 encoding.
- `run_cmd(command, timeout, cwd)`: Executes classic Windows CMD commands.
- `run_approved_command(command, shell, timeout)`: Validates commands against `SafetyValidator` before running and caches output.
- `get_command_output(last_n_chars)`: Retrieves stdout and exit status from the most recent command.

### 2. Windows Desktop & Hardware Control
- `launch_application(app_name)` / `open_application(app_name)`: Launches applications, drives, or URLs.
- `kill_process(process_name)`: Safely terminates running Windows processes.
- `get_system_telemetry()`: Real-time CPU, RAM, disk, and battery statistics.
- `set_system_volume(level_percent)`: Calibrates Windows master audio via pycaw / COM.
- `media_control(action)`: Hardware media keys (play/pause, next, prev, volume up/down, mute).
- `get_running_windows()`: Enumerates visible top-level Windows applications.
- `minimize_all_windows()`: Clears screen to desktop (`Win+D`).
- `read_clipboard()` / `write_clipboard(text)`: Windows clipboard integration.
- `lock_workstation()`: Instantly locks Windows workstation.
- `system_power(action, delay_sec)`: Sleep, restart, shutdown, and cancellation.

### 3. Windows UI Automation (pywinauto UIA Backend)
- `get_active_window()`: Identifies foreground window title, class name, and control type.
- `list_running_applications()`: Enumerates running desktop applications.
- `get_ui_elements(window_title, control_type)`: Inspects buttons, edit controls, and menus.
- `click_ui_element(name, window_title)`: Clicks accessible UI controls by name with partial matching.
- `type_into_ui_element(name, text, window_title)`: Enters text into accessible controls.
- `wait_for_condition(condition, timeout)`: Polling engine supporting `process:`, `path:`, `window:`, and `seconds:`.

### 4. Web Browser Automation (Playwright with Brave Support)
- `browser_navigate(url)`: Navigates to web pages and waits for DOM readiness.
- `browser_search(query, engine)`: Direct search on Google, YouTube, or DuckDuckGo.
- `browser_click_first_result(intent_filter)`: Locates organic search results, filters out advertisements, clicks the target, and verifies destination.
- `browser_find_elements(selector, limit)`: Inspects DOM elements.
- `browser_click_element(selector, index)`: DOM-based element clicking.
- `browser_extract_text(selector)`: Extracts visible text content.
- `connect_brave(port)` / `find_brave_executable()`: Native Brave integration via standard paths or CDP.
- `browser_close()`: Clean browser shutdown with `atexit` registration.

### 5. Filesystem Management & Diagnostics
- `list_drives()`: Enumerates logical drives (`C:\`, `D:\`, etc.) with partition status and free space.
- `navigate_to_path(target_path, open_in_explorer)`: Resolves drive/folder paths, validates presence, and provides parent diagnostic recovery if missing.
- `list_directory(location)`: Distinguishes subdirectories from files, formats sizes and extensions.
- `read_file(path, max_chars)`: Reads text/code files (`.py`, `.md`, `.txt`, `.json`, etc.) with binary file protection.
- `get_file_metadata(path)`: Retrieves size, creation time, modified time, and permissions.
- `search_files(root, pattern, max_results)`: Recursive glob search across approved roots.

### 6. Closed-Loop Verification, Memory & Safety
- `remember_fact(key, value)`: Stores persistent user preferences and long-term memory into SQLite.
- `verify_action(check, expected)`: Confirms path existence, process state, or window presence.
- `cancel_current_task()`: Immediately cancels active multi-step operations.
- `SafetyValidator`: Blocks destructive sequences (`format`, `diskpart`, `rmdir /s /q C:\Windows`) and enforces confirmation on sensitive commands.

### 7. Vision & Multimodal Screen Understanding
- `describe_screen(question)`: High-accuracy screen understanding passing desktop captures to Gemini (`gemini-3.8-flash`) to identify open apps, UI elements, and document text.
- `inspect_screen()`: Retrieves screenshot, dimensions, mouse position, and active window metadata.

### 8. Reminders & Persistent Timers (APScheduler + SQLite)
- `set_reminder(message, minutes)`: Schedules persistent timers and reminders that trigger spoken voice alerts even across application restarts.
- `list_active_reminders()`: Lists all upcoming scheduled alarms and timers.
- `cancel_active_reminder(reminder_id)`: Cancels scheduled reminder by ID.

### 9. Google Workspace (Gmail & Google Calendar)
- `list_unread_emails(max_results)`: Fetches recent unread Gmail messages with snippets and senders.
- `read_email(message_id)`: Retrieves full message contents and plain text body.
- `send_email(to, subject, body)`: Sends email via official Gmail API (gated by central confirmation).
- `list_upcoming_events(max_results)`: Lists upcoming Google Calendar events from user's primary calendar.
- `create_calendar_event(summary, start_time, end_time, description)`: Schedules events on Google Calendar.

---

## 🎙️ Voice Interface & Interruption System

- **Speech Recognition:** `speech_recognition` with tuned low-latency Voice Activity Detection (0.35s pause threshold) and phonetic normalization (`tap` -> `tab`, `clothes` -> `close`).
- **Zero-Latency SAPI TTS:** Native Windows speech engine for instant verbal responses (<20ms).
- **Neural Cloud TTS:** Optional Microsoft Neural TTS (`edge-tts`) with GuyNeural voice.
- **Acoustic Voice Barge-In:** Background audio energy monitor (`VoiceBargeInMonitor`) automatically detects when the user speaks mid-sentence and cuts off playback.
- **Console Keyboard Interruption:** Tapping any key (Space, Enter, Esc) cuts off speech instantly.
- **Silence Commands:** Supported verbal commands: *"stop talking"*, *"be quiet"*, *"shut up"*, *"silence"*, *"can you hear me"*.

---

## 🚀 Setup & Installation

### 1. Requirements
- **Operating System:** Windows 10/11
- **Python:** 3.10 to 3.12 (Tested on Python 3.12.10)
- **Audio:** Microphone and speakers for voice control

### 2. Install Dependencies
```powershell
# Clone the repository
cd D:\codehub\TARS

# Install Python dependencies
pip install -r requirements.txt

# (Optional) Install Playwright Chromium browser if needed
playwright install chromium
```

### 3. Environment Configuration
Create or edit `.env` in the project root:
```ini
# Primary AI Provider: "gemini" | "openrouter" | "ollama"
AI_PROVIDER=gemini
AI_MODEL=gemini-3.8-flash

# Google Gemini API Key (Default cloud provider)
GEMINI_API_KEY=your_gemini_api_key_here

# OpenRouter API Key (For high-end and fast models e.g. Claude 3.5 Sonnet, GPT-4o, etc.)
OPENROUTER_API_KEY=your_openrouter_api_key_here

# Optional local Ollama model
OLLAMA_URL=http://localhost:11434

# Optional: dedicated on-device wake-word detection. Create a Windows custom
# "TARS" .ppn keyword model in the Picovoice Console, then configure its path.
PICOVOICE_ACCESS_KEY=your_picovoice_access_key_here
TARS_WAKE_KEYWORD_PATH=C:\path\to\tars_windows.ppn

# Voice settings
TARS_VOICE=en-US-GuyNeural
TARS_DEFAULT_MODE=hybrid
```

### Supported AI Providers

TARS supports three AI backends with native multi-turn ReAct tool calling:
1. **Google Gemini** (`AI_PROVIDER=gemini`): Fast multi-modal reasoning and screen vision via `gemini-3.8-flash`. Requires `GEMINI_API_KEY`.
2. **OpenRouter** (`AI_PROVIDER=openrouter`): Access to high-end and fast cloud models (e.g. `anthropic/claude-3.5-sonnet`, `openai/gpt-4o`, `google/gemini-2.5-flash`) via the OpenAI-compatible API with full tool calling and vision support. Requires `OPENROUTER_API_KEY`.
3. **Ollama** (`AI_PROVIDER=ollama`): Fully offline local model execution (e.g. `qwen3:4b`).

Local desktop controls (hotkeys, system telemetry, media keys, audio controls, and UI automation) remain available even without cloud API keys. Gmail and Google Calendar require enabling the relevant Google APIs and placing OAuth `credentials.json` under `config/`.

---

## 💻 Running TARS

```powershell
# 1. Interactive Hybrid Mode (Type or speak commands):
python main.py

# 2. Continuous Hands-Free Voice Control:
python main.py --voice

# 3. Text-Only Silent Mode (No TTS):
python main.py --text-only

# 4. Run Hardware & Subsystem Diagnostics:
python main.py --diagnostics

# 5. List and Benchmark Microphones:
python main.py --list-mics
python main.py --test-mic
```

---

## 🧪 Running Automated Tests

```powershell
# Run the full test suite (66+ unit, integration, acceptance, and interaction tests):
python -m unittest discover tests

# Run master acceptance criteria tests:
python -m unittest tests/test_agent_acceptance.py

# Run tool & safety tests:
python -m unittest tests/test_tools.py
```

---

## 📋 Tested Command Examples

| Objective | Command |
|-----------|---------|
| **Filesystem compound task** | *"TARS, open D drive, go to the CWD folder, and tell me what files are inside"* |
| **Missing directory recovery** | *"TARS, open D drive, go to the missing_folder directory, and list files"* |
| **Contextual follow-up** | *"Tell me what you found"* / *"Read the first python file"* |
| **Browser automation** | *"TARS, search YouTube and open the first relevant result"* |
| **Application launching** | *"TARS, open Brave"* / *"Open File Explorer"* / *"Open Calculator"* |
| **System volume** | *"TARS, set volume to 50 percent"* / *"Volume up"* / *"Mute"* |
| **System telemetry** | *"TARS, system status"* / *"Hardware telemetry"* |
| **Voice interruption** | *"Stop talking"* / *"Be quiet"* / *"Cancel"* / Tap Spacebar |

---

## 🛡️ License & Attribution
MIT License. Inspired by TARS from *Interstellar*. Built by Shafyydev.
