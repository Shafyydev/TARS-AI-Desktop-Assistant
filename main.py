import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import sys
import time
import argparse
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt

from core.brain import TARSAgent, is_affirmative
from core.voice_speaker import VoiceSpeaker
from core.voice_listener import VoiceListener
import tools

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True)

TARS_BANNER = """
 ████████╗ █████╗ ██████╗ ███████╗
 ╚══██╔══╝██╔══██╗██╔══██╗██╔════╝
    ██║   ███████║██████╔╝███████╗
    ██║   ██╔══██║██╔══██╗╚════██║
    ██║   ██║  ██║██║  ██║███████║
    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝
    Tactical Autonomous Robotic System
"""


def render_hud(agent: TARSAgent) -> None:
    """Renders the futuristic status dashboard."""
    console.print(Panel(Text(TARS_BANNER, style="bold cyan"), border_style="cyan", expand=False))
    
    # System Telemetry Table
    telemetry = tools.get_system_telemetry()
    p = agent.settings.get("personality", {})

    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    info_table = Table(show_header=False, border_style="dim", box=None)
    info_table.add_column(style="cyan")
    info_table.add_column(style="white")

    info_table.add_row("Status", "[bold green]ONLINE & ARMED[/bold green]")
    info_table.add_row("Honesty Matrix", f"{int(p.get('honesty', 0.90) * 100)}%")
    info_table.add_row("Humor Matrix", f"{int(p.get('humor', 0.75) * 100)}%")
    info_table.add_row("Sarcasm Matrix", f"{int(p.get('sarcasm', 0.60) * 100)}%")
    info_table.add_row("CPU Load", f"{telemetry.get('cpu_load_percent')}%")
    info_table.add_row("RAM Used", f"{telemetry.get('ram_used_gb')} / {telemetry.get('ram_total_gb')} GB ({telemetry.get('ram_usage_percent')}%)")
    info_table.add_row("Battery", str(telemetry.get('battery')))

    console.print(Panel(info_table, title="[bold cyan]SYSTEM TELEMETRY & MATRICES[/bold cyan]", border_style="cyan"))


def list_microphones() -> None:
    """Lists all detected microphone devices with their system indices."""
    import speech_recognition as sr
    console.print("\n[bold cyan]🎙️  Available Audio Input Devices (Microphones):[/bold cyan]\n")
    mics = sr.Microphone.list_microphone_names()
    table = Table(border_style="cyan", show_header=True)
    table.add_column("Index", style="yellow", justify="right")
    table.add_column("Device Name", style="white")
    for i, name in enumerate(mics):
        table.add_row(str(i), name)
    console.print(table)
    console.print("\n[dim]To use a specific microphone, run:[/dim] [bold green]python main.py --voice --mic-index <Index>[/bold green]\n")


def test_microphone(language: Optional[str] = None, mic_index: Optional[int] = None) -> None:
    """Runs an instant microphone and speech-to-text benchmark."""
    console.print("\n[bold yellow]🎙️  Testing Microphone & Speech Recognition...[/bold yellow]")
    listener = VoiceListener(language=language, mic_index=mic_index)
    if not listener._recognizer or not listener._microphone:
        console.print("[bold red]❌ Microphone failed to initialize. Check Windows audio settings.[/bold red]")
        return

    console.print(f"[dim cyan]Acoustic Model: [bold white]{listener.language}[/bold white][/dim cyan]")
    console.print("[dim cyan]Calibrating ambient acoustics...[/dim cyan]")
    thresh = listener.calibrate(duration=0.8)
    console.print(f"[green]✓ Ambient energy threshold set to: {int(thresh)} (Adaptive VAD active)[/green]")

    console.print("\n[bold green]Speak something into your microphone now (e.g. 'Hello TARS' or 'close tab')...[/bold green]")
    t0 = time.time()
    text, status = listener.listen_once(timeout=8, phrase_time_limit=10)
    elapsed = time.time() - t0

    if text:
        console.print(f"\n[bold green]✓ SUCCESS! Captured in {elapsed:.2f}s:[/bold green] [bold white]\"{text}\"[/bold white]")
        speaker = VoiceSpeaker(fast_voice=True)
        speaker.speak(f"Heard {text}.", blocking=True)
    else:
        console.print(f"\n[bold red]❌ No speech transcribed. Status: {status}[/bold red]")
        console.print("[dim yellow]Tips: Check your Windows Default Microphone volume or run with --list-mics.[/dim yellow]")


def run_diagnostics() -> None:
    """Runs a quick hardware and tool self-test."""
    console.print("[bold yellow]Running TARS Core Tool Diagnostics...[/bold yellow]\n")
    
    # 1. PowerShell Test
    ps_res = tools.run_powershell("Write-Output 'PowerShell Subsystem Operational'")
    console.print(f"[{'green' if ps_res['success'] else 'red'}][+] PowerShell Engine: {ps_res['stdout']}[/]")

    # 2. Telemetry Test
    telem = tools.get_system_telemetry()
    console.print(f"[green][+] Hardware Sensors: CPU {telem['cpu_load_percent']}%, RAM {telem['ram_usage_percent']}%, Battery: {telem['battery']}[/green]")

    # 3. Screen Dimensions
    dims = tools.get_screen_dimensions()
    console.print(f"[green][+] Primary Display: {dims['width']}x{dims['height']} pixels[/green]")

    # 4. Running Windows
    wins = tools.get_running_windows()
    console.print(f"[green][+] Window Enumeration: Detected {len(wins)} active window(s)[/green]")

    # 5. Audio Synthesizer Test
    speaker = VoiceSpeaker()
    console.print(f"[green][+] Voice Synthesizer: Configured voice '{speaker.voice_name}' with offline SAPI fallback ready.[/green]")

    console.print("\n[bold green]All core execution engines are ready to take commands.[/bold green]")


def main() -> None:
    parser = argparse.ArgumentParser(description="TARS Autonomous Voice-Driven OS Control Agent")
    parser.add_argument("--text-only", action="store_true", help="Silent typed mode: no microphone and no spoken replies")
    parser.add_argument("--voice", "--voice-only", dest="voice_mode", action="store_true", help="Hands-free voice loop (this is the default)")
    parser.add_argument("--text", "--typed", dest="text_mode", action="store_true", help="Type commands instead of speaking (disables the hands-free loop)")
    parser.add_argument("--neural-voice", action="store_true", help="Use cloud neural Edge-TTS voice (higher fidelity, but slower network latency)")
    parser.add_argument("--fast-voice", action="store_true", default=True, help="Use zero-latency native Windows voice for maximum speed (default)")
    parser.add_argument("--language", type=str, default=None, help="Set STT language code (default auto-detected e.g. en-IN or en-US)")
    parser.add_argument("--mic-index", type=int, default=None, help="Select input microphone device index")
    parser.add_argument("--list-mics", action="store_true", help="List all available microphones and exit")
    parser.add_argument("--test-mic", action="store_true", help="Test microphone input and speech recognition")
    parser.add_argument("--diagnostics", action="store_true", help="Run hardware and tool diagnostics")
    parser.add_argument("--dashboard", action="store_true", default=True, help="Launch web dashboard on startup (default)")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable web dashboard on startup")
    args = parser.parse_args()

    # Hands-free voice is the default; --text (or --text-only) switches to typed commands.
    voice_mode = not (args.text_mode or args.text_only)

    if args.list_mics:
        list_microphones()
        sys.exit(0)

    if args.diagnostics:
        run_diagnostics()
        sys.exit(0)

    if args.test_mic:
        test_microphone(language=args.language, mic_index=args.mic_index)
        sys.exit(0)

    # Initialize TARS Brain & Voice
    agent = TARSAgent()

    # Launch web dashboard in background if enabled
    if args.dashboard and not args.no_dashboard:
        import threading, webbrowser, time as _t
        def _start_dashboard():
            try:
                import uvicorn
                uvicorn.run("dashboard.server:app", host="127.0.0.1", port=8765, log_level="warning")
            except Exception as e:
                console.print(f"[red]Dashboard failed to start: {e}[/red]")
        threading.Thread(target=_start_dashboard, daemon=True).start()
        _t.sleep(1.5)
        webbrowser.open("http://127.0.0.1:8765")
        console.print("[green][+] Web dashboard: http://127.0.0.1:8765[/green]")

    llm = agent.orchestrator.llm
    fallbacks = agent.orchestrator._get_fallback_providers()
    fb_names = ", ".join(p.provider for p in fallbacks)
    if llm.available():
        fb_note = f" (failover ready: {fb_names})" if fb_names else ""
        console.print(f"[dim green]Language model ready:[/dim green] {llm.provider} / {llm.model}{fb_note}")
    else:
        if fb_names:
            console.print(f"[yellow]Primary LLM ({llm.provider}) offline ({llm.unavailable_reason()}); failover ready: {fb_names}[/yellow]")
        else:
            console.print(f"[bold red]Language model offline:[/bold red] {llm.unavailable_reason()}")
    use_fast = not args.neural_voice
    speaker = VoiceSpeaker(fast_voice=use_fast) if not args.text_only else None

    # Speak scheduled reminder alerts aloud when audio output is enabled
    if speaker:
        from core.scheduler import set_alert_callback
        set_alert_callback(lambda msg: speaker.speak(msg, blocking=False))
    v_cfg = agent.settings.get("voice", {})
    stt_timeout = int(v_cfg.get("stt_timeout", 8))
    stt_phrase_limit = int(v_cfg.get("stt_phrase_time_limit", 10))
    listener = (
        VoiceListener(
            energy_threshold=v_cfg.get("stt_energy_threshold", 250),
            pause_threshold=v_cfg.get("stt_pause_threshold", 0.35),
            language=args.language,
            mic_index=args.mic_index,
            stt_engine=v_cfg.get("stt_engine"),
        )
        if (voice_mode or not args.text_only)
        else None
    )

    # Wire Central Confirmation Gate for destructive actions
    def confirm_action(tool_name: str, arguments: dict) -> bool:
        # Speak the tool and its arguments, not just the tool name, so a voice
        # approval is informed rather than reflexive.
        arg_summary = ", ".join(f"{k} {str(v)[:120]}" for k, v in arguments.items()) or "no arguments"
        if voice_mode and speaker and listener:
            speaker.speak(
                f"Confirmation required. {tool_name.replace('_', ' ')} with {arg_summary}. Say yes or no.",
                blocking=True,
            )
            speech, _ = listener.listen_once(timeout=6, phrase_time_limit=4)
            return is_affirmative(speech or "")
        else:
            ans = Prompt.ask(f"[bold red]CONFIRMATION REQUIRED[/bold red] - Execute [bold yellow]{tool_name}[/bold yellow] ({arg_summary})? (yes/no)", default="no")
            return is_affirmative(ans)

    agent.set_confirmation_callback(confirm_action)

    render_hud(agent)

    if listener:
        with console.status("[dim cyan]Calibrating microphone for room acoustics...[/dim cyan]"):
            listener.calibrate(duration=0.45)

    if speaker:
        # Block on the brief startup greeting in voice mode so the mic does not capture TARS's own speaker output.
        speaker.speak("TARS operational. All systems online.", blocking=voice_mode)

    if voice_mode:
        console.print("[bold green]🎙️  HANDS-FREE VOICE CONTROL ENGAGED[/bold green]")
        if listener and listener.wake_detector.is_porcupine_active():
            console.print("[dim cyan]Wake-word engine: Porcupine. Say the configured keyword, then your command.[/dim cyan]")
        else:
            console.print("[dim yellow]Wake-word engine is not configured; commands are transcribed directly. Set PICOVOICE_ACCESS_KEY and TARS_WAKE_KEYWORD_PATH to enable wake-word mode.[/dim yellow]")
        console.print("[white]Just speak. (e.g. [cyan]'open Chrome'[/cyan], [cyan]'open d drive'[/cyan], [cyan]'volume 50'[/cyan], [cyan]'status'[/cyan])[/white]")
        console.print("[dim cyan]⚡ Live Interruption: speak anytime, or tap any key, to cut off speech instantly.[/dim cyan]")
        console.print("[dim white]Say [bold red]'exit'[/bold red] or [bold red]'disarm'[/bold red] to disarm TARS. Add [bold green]--text[/bold green] to type instead.[/dim white]\n")
    else:
        console.print("\n[bold white]Typed mode:[/bold white]")
        console.print(" • Type your instructions directly.")
        console.print(" • Type [bold cyan]'v'[/bold cyan] or [bold cyan]'voice'[/bold cyan] to speak a single command via microphone.")
        console.print(" • Run [bold green]python main.py[/bold green] (no flags) for hands-free voice control.")
        console.print(" • Zero-latency native speech is active by default. (Add [bold green]--neural-voice[/bold green] for cloud Edge-TTS).")
        console.print(" • Type [bold cyan]'exit'[/bold cyan] or [bold cyan]'quit'[/bold cyan] to disarm TARS.\n")

    listening_banner_shown = False
    try:
        while True:
            try:
                user_input = None

                if voice_mode:
                    if listener and listener.wake_detector.is_porcupine_active():
                        console.print("\n[bold green]🎙️  Waiting for wake word...[/bold green]")
                        if not listener.listen_for_wake_word():
                            continue
                        console.print("[bold yellow]🎙️  Wake word detected. Listening for command...[/bold yellow]")
                        raw_speech, status = listener.listen_once(timeout=stt_timeout, phrase_time_limit=stt_phrase_limit)
                        if status == "timeout" or not raw_speech:
                            continue
                        user_input = raw_speech
                        console.print(f"[bold cyan]🎙️  You said:[/bold cyan] [bold white]\"{user_input}\"[/bold white]")
                        # Porcupine already authenticated this interaction; do not run
                        # transcript substring matching on every microphone capture.
                        raw_speech = None

                    if user_input is not None:
                        pass
                    else:
                        if not listening_banner_shown:
                            console.print("\n[bold green]🎙️  Listening... (Speak now)[/bold green]")
                            listening_banner_shown = True
                        _t_stt_start = time.time()
                        raw_speech, status = listener.listen_once(timeout=stt_timeout, phrase_time_limit=stt_phrase_limit) if listener else (None, "no listener")
                        _t_stt_end = time.time()
                        if status == "timeout":
                            continue
                        listening_banner_shown = False
                        _timings = getattr(listener, "_stt_timings", {})
                        console.print("[dim]TIMING: STT total {:.2f}s | wait+record {:.2f}s | transcription {:.2f}s ({}) | end_reason: {} | energy_threshold: {}[/dim]".format(
                            _t_stt_end - _t_stt_start,
                            _timings.get("wait_and_record", 0),
                            _timings.get("transcription", 0),
                            _timings.get("engine", "?"),
                            _timings.get("end_reason", "unknown"),
                            getattr(listener, "energy_threshold", "?")
                        ))

                        if status == "unrecognized":
                            console.print("[dim yellow]⚡ Audio detected, but speech was unclear. Try speaking slightly closer.[/dim yellow]")
                            continue
                        elif not raw_speech:
                            time.sleep(0.05)
                            continue

                        # Compatibility path for users who have not configured the
                        # dedicated Porcupine engine yet.
                        is_ww, clean_command = listener.check_wake_word(raw_speech)

                        # If only wake word alone was said ("TARS"), ask user what they want
                        if is_ww and not clean_command:
                            console.print(f"[bold cyan]🎙️  You said:[/bold cyan] [bold white]{raw_speech}[/bold white]")
                            if speaker:
                                speaker.speak("Standing by. What is your command?", blocking=True, listener=listener)
                            console.print("[bold yellow]🎙️  Listening for command...[/bold yellow]")
                            follow_speech, _ = listener.listen_once(timeout=stt_timeout, phrase_time_limit=stt_phrase_limit)
                            if not follow_speech:
                                continue
                            clean_command = follow_speech

                        user_input = clean_command if (is_ww and clean_command) else raw_speech
                        console.print(f"[bold cyan]🎙️  You said:[/bold cyan] [bold white]\"{user_input}\"[/bold white]")

                else:
                    user_input = Prompt.ask("[bold cyan]TARS >[/bold cyan]")
                    if not user_input or not user_input.strip():
                        continue

                    if user_input.strip().lower() in ["exit", "quit", "q"]:
                        if speaker:
                            speaker.speak("Disarming. Goodbye.", blocking=True)
                        console.print("[dim]TARS offline.[/dim]")
                        break

                    if user_input.strip().lower() in ["v", "voice"]:
                        if listener:
                            console.print("\n[bold yellow]🎙️  Listening... Speak your command clearly.[/bold yellow]")
                            raw_speech, status = listener.listen_once(timeout=stt_timeout, phrase_time_limit=stt_phrase_limit)
                            if not raw_speech:
                                console.print(f"[dim red]No speech detected ({status}).[/dim red]")
                                continue
                            is_ww, clean = listener.check_wake_word(raw_speech)
                            user_input = clean if (is_ww and clean) else raw_speech
                            console.print(f"[bold cyan]🎙️  You said:[/bold cyan] [bold white]\"{user_input}\"[/bold white]")
                        else:
                            console.print("[red]Microphone listener not initialized.[/red]")
                            continue

                # Check shutdown commands
                if user_input.strip().lower() in ["exit", "quit", "disarm", "shutdown", "shut down", "shutdown tars", "shut down tars", "shutdown task", "stop tars"]:
                    if speaker:
                        speaker.speak("Disarming. Goodbye.", blocking=True)
                    console.print("[dim]TARS offline.[/dim]")
                    break

                # Status callback
                def on_status(msg: str) -> None:
                    console.print(f"[dim yellow]⚡ {msg}[/dim yellow]")

                # Run through Agent ReAct Loop with precise timing
                t_exec_start = time.time()
                with console.status("[bold green]TARS reasoning & executing...[/bold green]", spinner="dots"):
                    result = agent.run_agent_loop(user_input, on_status_update=on_status)
                exec_duration = time.time() - t_exec_start
                console.print("[dim]TIMING: LLM+tools took {:.2f}s[/dim]".format(exec_duration))

                # Output Spoken / Written Response
                console.print(f"\n[bold green]TARS:[/bold green] {result['response']} [dim green]({exec_duration:.2f}s)[/dim green]\n")

                # Show tool execution details if tools were used
                if result.get("steps"):
                    step_table = Table(title="[dim]Tools Executed[/dim]", border_style="dim", box=None)
                    step_table.add_column("Tool", style="yellow")
                    step_table.add_column("Status", style="green")
                    for s in result["steps"]:
                        step_result = s.get("result")
                        ok = step_result.get("success", True) if isinstance(step_result, dict) else True
                        status = "OK" if ok else "FAILED"
                        step_table.add_row(s["tool"], status)
                    console.print(step_table)

                # Speak response aloud (with instant keyboard interruption & acoustic voice barge-in)
                if speaker and result.get("response"):
                    _t_tts_start = time.time()
                    interrupted = speaker.speak(
                        result["response"],
                        blocking=voice_mode,
                        listener=listener if voice_mode else None
                    )
                    _t_tts_end = time.time()
                    console.print("[dim]TIMING: TTS took {:.2f}s[/dim]".format(_t_tts_end - _t_tts_start))
                    if interrupted:
                        console.print("[dim yellow]⚡ Speech interrupted by user. Standing by...[/dim yellow]")
                    elif voice_mode:
                        # Prevent acoustic feedback: clear audio device buffers before listening again
                        time.sleep(0.15)

            except KeyboardInterrupt:
                console.print("\n[dim]TARS interrupted. Shutting down.[/dim]")
                break
            except Exception as e:
                console.print(f"[bold red]Unexpected error:[/bold red] {e}")
    finally:
        if listener:
            listener.close_stream()


if __name__ == "__main__":
    main()
