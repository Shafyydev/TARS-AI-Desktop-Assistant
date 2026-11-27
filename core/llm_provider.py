"""Configurable LLM provider adapter with structured multi-turn tool calling for TARS."""
import os
import re
import time
import logging
import inspect
import json
import traceback
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

DEFAULT_MODELS = ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3-flash-preview"]
OLLAMA_DEFAULT_MODEL = "qwen3:4b"
OPENROUTER_DEFAULT_MODEL = "google/gemini-2.5-flash"
OMNIROUTE_DEFAULT_MODEL = "auto/best-fast"
OMNIROUTE_DEFAULT_URL = "http://localhost:20128/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
NVIDIA_DEFAULT_URL = "https://integrate.api.nvidia.com/v1"
GROQ_DEFAULT_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"

# Leading words that mean "send X Y" is not a real WhatsApp contact.  Mirrors
# TARSAgent.WHATSAPP_SEND_STOPWORDS so the LLM loop can reject a misheard
# bare-send before it fires a message at a non-existent contact.
_BARE_SEND_STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "it", "my", "your", "hello", "hi",
    "hey", "message", "msg", "messg", "mesg", "text", "email", "mail",
    "file", "files", "attachment", "photo", "image", "doc", "document",
    "report", "link", "invite", "reminder", "thanks", "please", "another",
    "one", "us", "me", "you", "them", "him", "her",
})


def get_omniroute_api_key() -> Optional[str]:
    """Retrieves OmniRoute API key from environment or local storage.sqlite."""
    key = os.getenv("OMNIROUTE_API_KEY")
    if key and key != "your_omniroute_api_key_here":
        return key
    try:
        db_path = Path.home() / ".omniroute" / "storage.sqlite"
        if db_path.exists():
            import sqlite3
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            row = cur.execute(
                "SELECT key FROM api_keys WHERE is_active = 1 OR is_active IS NULL LIMIT 1"
            ).fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return None

# Set up dedicated logger for LLM interactions
logger = logging.getLogger("TARS.LLM")
log_dir = Path(__file__).resolve().parent.parent / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / "tars.log"

if not logger.handlers:
    logger.setLevel(logging.INFO)
    try:
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(fh)
    except Exception:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[TARS LLM] %(levelname)s: %(message)s"))
    logger.addHandler(sh)


def _strip_tool_markers(text: str) -> str:
    """Removes internal ``[tools: ...]`` history markers a model may echo back."""
    if not text:
        return text
    return re.sub(r"\s*\[tools:[^\]]*\]", "", text).strip()


def _loop_guard(
    tool_name: str,
    tool_args: Dict[str, Any],
    result: Any,
    tool_fails: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """
    Returns a stop-result when the model is spinning instead of making progress.

    Two triggers: a tool that needs user confirmation (the model cannot answer it,
    so further turns only add latency), and one tool that keeps failing.
    """
    if isinstance(result, dict) and result.get("requires_confirmation"):
        return {
            "success": False,
            "response": (
                result.get("error")
                or result.get("message")
                or f"Tool '{tool_name}' needs confirmation."
            ),
            "error": "confirmation required",
            "requires_confirmation": True,
            "tool": tool_name,
            "args": tool_args,
        }
    if isinstance(result, dict) and result.get("success") is False:
        tool_fails[tool_name] = tool_fails.get(tool_name, 0) + 1
        if tool_fails[tool_name] >= 3:
            return {
                "success": False,
                "response": f"Tool '{tool_name}' failed {tool_fails[tool_name]} times. Stopping.",
                "error": result.get("error") or result.get("message"),
            }
    else:
        tool_fails[tool_name] = 0
    return None


def _log_provider_exception(message: str, exc: Exception) -> None:
    """Shows a short console error while retaining the traceback in tars.log."""
    logger.error("%s: %s", message, exc)
    try:
        with open(log_file, "a", encoding="utf-8") as log_handle:
            log_handle.write(traceback.format_exc())
    except OSError:
        pass


# Hosted gateways and agent products sometimes answer HTTP 200 with a product or
# upgrade notice ("this version is no longer supported, download the latest
# build") instead of a completion. Speaking that as TARS's own answer is never
# useful, so the tool-calling loops treat it as a provider failure.
_GATEWAY_NOTICE_MARKERS = (
    "no longer supported",
    "please download and install the latest version",
    "to continue using the agent",
    "unlock our latest models",
    "unsupported client",
    "unsupported version",
)


SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _load_llm_settings() -> Dict[str, Any]:
    """
    Reads the ``llm`` block from config/settings.yaml.

    The file is authoritative for provider and model selection; a missing or
    unreadable file yields an empty mapping so environment defaults apply.
    """
    try:
        import yaml
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        block = data.get("llm") if isinstance(data, dict) else None
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def is_gateway_notice(text: str) -> bool:
    """True when a completion body is really an upstream product/upgrade notice."""
    if not text or len(text) > 600:
        return False
    lowered = text.casefold()
    hits = sum(1 for marker in _GATEWAY_NOTICE_MARKERS if marker in lowered)
    if hits == 0:
        return False
    # Require a corroborating signal so an ordinary answer that merely mentions
    # a version is not discarded.
    return hits >= 2 or "http" in lowered or "download" in lowered


class LLMProvider:
    """
    Unified AI provider adapter for Google Gemini and extensible providers.
    Supports structured tool calling, multi-step conversation planning,
    and automatic tool execution recovery.
    """

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        # config/settings.yaml is authoritative for the provider and model.  The
        # environment only fills a gap, so a stale .env cannot silently swap the
        # configured provider for an unreachable one.
        settings_llm = _load_llm_settings()
        configured_provider = str(settings_llm.get("provider") or "").strip().lower()
        self.provider = (provider or configured_provider or os.getenv("AI_PROVIDER", "ollama")).lower()
        settings_model = "" if provider else str(settings_llm.get("model") or "").strip()

        if self.provider == "ollama":
            default_model = OLLAMA_DEFAULT_MODEL
            model_env = os.getenv("OLLAMA_MODEL") or (
                os.getenv("AI_MODEL") if os.getenv("AI_PROVIDER", "").lower() == "ollama" else None
            )
        elif self.provider == "openrouter":
            default_model = OPENROUTER_DEFAULT_MODEL
            model_env = os.getenv("OPENROUTER_MODEL") or (
                os.getenv("AI_MODEL") if os.getenv("AI_PROVIDER", "").lower() == "openrouter" else None
            )
        elif self.provider == "omniroute":
            default_model = OMNIROUTE_DEFAULT_MODEL
            model_env = os.getenv("OMNIROUTE_MODEL") or (
                os.getenv("AI_MODEL") if os.getenv("AI_PROVIDER", "").lower() == "omniroute" else None
            )
        elif self.provider == "nvidia":
            default_model = NVIDIA_DEFAULT_MODEL
            model_env = os.getenv("NVIDIA_MODEL") or (
                os.getenv("AI_MODEL") if os.getenv("AI_PROVIDER", "").lower() == "nvidia" else None
            )
        elif self.provider == "groq":
            default_model = GROQ_DEFAULT_MODEL
            model_env = os.getenv("GROQ_MODEL") or (
                os.getenv("AI_MODEL") if os.getenv("AI_PROVIDER", "").lower() == "groq" else None
            )
        else:
            default_model = DEFAULT_MODELS[0]
            model_env = os.getenv("GEMINI_MODEL") or (
                os.getenv("AI_MODEL") if os.getenv("AI_PROVIDER", "").lower() == "gemini" else None
            )

        self.model = model or settings_model or model_env or default_model
        self.client = None
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.omniroute_base_url = (
            os.getenv("OMNIROUTE_URL") or os.getenv("OMNIROUTE_BASE_URL") or OMNIROUTE_DEFAULT_URL
        ).rstrip("/")
        self.nvidia_base_url = os.getenv("NVIDIA_BASE_URL", NVIDIA_DEFAULT_URL).rstrip("/")
        self.groq_base_url = os.getenv("GROQ_BASE_URL", GROQ_DEFAULT_URL).rstrip("/")
        self._quota_exhausted_until = 0.0
        # model name -> epoch seconds until which that model is skipped
        self._model_pause_until: Dict[str, float] = {}
        self._load_model_health()
        self._init_client()

    def _init_client(self) -> None:
        if self.provider == "ollama":
            # The Ollama server is checked lazily in available(), so startup
            # remains usable even while Ollama is still launching.
            self.client = object()
        elif self.provider == "openrouter":
            key = os.getenv("OPENROUTER_API_KEY")
            if key and key != "your_openrouter_api_key_here":
                try:
                    from openai import OpenAI
                    self.client = OpenAI(
                        base_url=self.openrouter_base_url,
                        api_key=key,
                        default_headers={
                            "HTTP-Referer": "https://github.com/Shafyydev/TARS",
                            "X-Title": "TARS AI Desktop Assistant",
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize OpenRouter client: {e}")
                    self.client = None
            else:
                self.client = None
        elif self.provider == "omniroute":
            key = get_omniroute_api_key() or "omniroute-default"
            try:
                from openai import OpenAI
                self.client = OpenAI(
                    base_url=self.omniroute_base_url,
                    api_key=key,
                    default_headers={
                        "HTTP-Referer": "https://github.com/Shafyydev/TARS",
                        "X-Title": "TARS AI Desktop Assistant",
                    }
                )
            except Exception as e:
                logger.error(f"Failed to initialize OmniRoute client: {e}")
                self.client = None
        elif self.provider == "nvidia":
            key = os.getenv("NVIDIA_API_KEY")
            if key and key != "your_nvidia_api_key_here":
                try:
                    from openai import OpenAI
                    self.client = OpenAI(
                        base_url=self.nvidia_base_url,
                        api_key=key,
                        default_headers={
                            "HTTP-Referer": "https://github.com/Shafyydev/TARS",
                            "X-Title": "TARS AI Desktop Assistant",
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize NVIDIA client: {e}")
                    self.client = None
            else:
                self.client = None
        elif self.provider == "groq":
            key = os.getenv("GROQ_API_KEY")
            if key and key != "your_groq_api_key_here":
                try:
                    from openai import OpenAI
                    self.client = OpenAI(
                        base_url=self.groq_base_url,
                        api_key=key,
                        default_headers={
                            "HTTP-Referer": "https://github.com/Shafyydev/TARS",
                            "X-Title": "TARS AI Desktop Assistant",
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize Groq client: {e}")
                    self.client = None
            else:
                self.client = None
        elif self.provider == "gemini":
            key = os.getenv("GEMINI_API_KEY")
            if key and key != "your_gemini_api_key_here":
                try:
                    from google import genai
                    self.client = genai.Client(api_key=key)
                except Exception as e:
                    logger.error(f"Failed to initialize GenAI client: {e}")
                    self.client = None
            else:
                self.client = None

    def available(self) -> bool:
        """Returns True if the client is configured and not under an active rate limit pause."""
        if not self.client:
            return False
        # Circuit breaker applies to every provider, including gateways whose
        # health endpoint stays up while the routed model keeps failing.
        if time.time() < self._quota_exhausted_until:
            return False
        if self.provider == "ollama":
            try:
                import requests
                response = requests.get(f"{self.ollama_url}/api/tags", timeout=1.5)
                return response.ok
            except Exception:
                return False
        if self.provider == "omniroute":
            try:
                import requests
                base = self.omniroute_base_url.rsplit("/v1", 1)[0]
                response = requests.get(f"{base}/api/health", timeout=1.5)
                if response.ok:
                    return True
                fallback = requests.get(self.omniroute_base_url, timeout=1.5)
                return fallback.status_code in (200, 401, 404)
            except Exception:
                return False
        return True

    def unavailable_reason(self) -> str:
        """
        Returns a specific explanation of why this provider cannot run, or an
        empty string when it is usable.

        ``available()`` only reports a boolean, which made every failure look
        identical from the outside.
        """
        if not self.client:
            if self.provider == "openrouter":
                return "OPENROUTER_API_KEY is not configured"
            if self.provider == "gemini":
                return "GEMINI_API_KEY is not configured"
            if self.provider == "omniroute":
                return f"OmniRoute client could not be created for {self.omniroute_base_url}"
            if self.provider == "nvidia":
                return "NVIDIA_API_KEY is not configured"
            return f"client for provider '{self.provider}' could not be created"

        if time.time() < self._quota_exhausted_until:
            remaining = int(self._quota_exhausted_until - time.time())
            return f"paused after a rate limit for another {remaining}s"

        if self.provider == "ollama":
            try:
                import requests
                requests.get(f"{self.ollama_url}/api/tags", timeout=1.5)
                return ""
            except Exception:
                return f"Ollama is unreachable at {self.ollama_url}"

        if self.provider == "omniroute":
            base = self.omniroute_base_url.rsplit("/v1", 1)[0]
            try:
                import requests
                response = requests.get(f"{base}/api/health", timeout=1.5)
                if response.ok:
                    return ""
                fallback = requests.get(self.omniroute_base_url, timeout=1.5)
                if fallback.status_code in (200, 401, 404):
                    return ""
                return f"OmniRoute is unreachable at {base}"
            except Exception:
                return f"OmniRoute is unreachable at {base}"

        return ""

    def mark_quota_exhausted(self, duration_sec: float = 60.0) -> None:
        """Applies a temporary circuit breaker when API rate limits are reached."""
        self._quota_exhausted_until = time.time() + duration_sec

    def _models_in_pause(self, candidates: List[str]) -> List[str]:
        """Drops models that are inside their transient-failure pause."""
        now = time.time()
        return [m for m in candidates if now >= self._model_pause_until.get(m, 0.0)]

    def _pause_model(self, model_name: str, duration_sec: float = 120.0) -> None:
        """Pauses one model after a timeout or overload so later turns skip it."""
        if model_name:
            self._model_pause_until[model_name] = time.time() + duration_sec
            self._save_model_health()

    def _save_model_health(self) -> None:
        """Persists model pauses so a restart does not pay the timeout again."""
        try:
            path = Path(__file__).resolve().parent.parent / "logs" / "model_health.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            live = {m: until for m, until in self._model_pause_until.items() if until > now}
            path.write_text(json.dumps(live), encoding="utf-8")
        except Exception:
            pass

    def _load_model_health(self) -> None:
        """Restores model pauses written by an earlier run."""
        try:
            path = Path(__file__).resolve().parent.parent / "logs" / "model_health.json"
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            now = time.time()
            self._model_pause_until = {
                str(model): float(until)
                for model, until in data.items()
                if float(until) > now
            }
        except Exception:
            self._model_pause_until = {}

    def switch_provider(self, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Switches the active LLM provider and optional model at runtime."""
        prov = (provider or "").strip().lower()
        if prov not in {"gemini", "openrouter", "omniroute", "ollama", "nvidia", "groq"}:
            return {"success": False, "error": f"Unsupported provider '{provider}'."}

        defaults = {
            "ollama": os.getenv("OLLAMA_MODEL") or OLLAMA_DEFAULT_MODEL,
            "openrouter": os.getenv("OPENROUTER_MODEL") or OPENROUTER_DEFAULT_MODEL,
            "omniroute": os.getenv("OMNIROUTE_MODEL") or OMNIROUTE_DEFAULT_MODEL,
            "gemini": os.getenv("GEMINI_MODEL") or DEFAULT_MODELS[0],
            "nvidia": os.getenv("NVIDIA_MODEL") or NVIDIA_DEFAULT_MODEL,
            "groq": os.getenv("GROQ_MODEL") or GROQ_DEFAULT_MODEL,
        }
        self.provider = prov
        self.model = (model or "").strip() or defaults[prov]
        self._quota_exhausted_until = 0.0
        self.client = None
        self._init_client()
        return {
            "success": True,
            "provider": self.provider,
            "model": self.model,
            "available": self.available(),
            "reason": self.unavailable_reason(),
        }

    @classmethod
    def list_provider_statuses(cls) -> List[Dict[str, Any]]:
        """Probes all supported LLM backends and reports configuration and live availability."""
        statuses: List[Dict[str, Any]] = []
        for prov in ("gemini", "openrouter", "omniroute", "ollama", "nvidia", "groq"):
            probe = cls(provider=prov)
            avail = probe.available()
            statuses.append({
                "provider": prov,
                "model": probe.model,
                "available": avail,
                "reason": "" if avail else probe.unavailable_reason(),
            })
        return statuses

    @staticmethod
    def _finalize_loop_reply(
        final_text: str,
        steps: List[Dict[str, Any]],
        model_name: str,
    ) -> Dict[str, Any]:
        """Formats the terminal reply of any ReAct tool loop consistently."""
        if steps:
            any_ok = any(
                (step.get("result") or {}).get("success") is not False
                for step in steps
                if isinstance(step.get("result"), dict)
            )
            return {
                "success": any_ok,
                "response": final_text,
                "steps": steps,
                "model": model_name,
            }
        # The model replied without running a single tool; the caller decides
        # whether that is an answer or an unfulfilled action.
        return {
            "success": True,
            "response": final_text,
            "steps": [],
            "model": model_name,
            "no_tool_calls": True,
        }

    def run_tool_agent_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]] = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_iterations: int = 5,
        timeout: float = 25.0
    ) -> Optional[Dict[str, Any]]:
        """
        Executes a closed-loop multi-step ReAct agent using Gemini's native tool calling.
        Executes tools iteratively, feeds outcomes back into the model, and extracts
        the final synthesized answer and executed steps.
        """
        if not self.available():
            if self.provider == "ollama":
                reason = "Ollama is not running at " + self.ollama_url
            elif self.provider == "omniroute":
                reason = "OmniRoute gateway is not running at " + self.omniroute_base_url
            elif self.provider == "openrouter":
                reason = "OPENROUTER_API_KEY not configured or rate limit pause in effect"
            elif self.provider == "nvidia":
                reason = "NVIDIA_API_KEY not configured or rate limit pause in effect"
            elif self.provider == "groq":
                reason = "GROQ_API_KEY not configured or rate limit pause in effect"
            else:
                reason = "API key not configured or rate limit pause in effect"
            logger.warning(f"LLM execution skipped: {reason}")
            return {
                "success": False,
                "response": f"LLM unavailable: {reason}.",
                "error": reason,
                "steps": []
            }

        if self.provider == "ollama":
            return self._run_ollama_tool_agent_loop(
                prompt, system_instruction, tools_list, tool_executor,
                history, user_preferences, on_status, max_iterations, timeout
            )
        elif self.provider == "openrouter":
            return self._run_openrouter_tool_agent_loop(
                prompt, system_instruction, tools_list, tool_executor,
                history, user_preferences, on_status, max_iterations, timeout
            )
        elif self.provider == "omniroute":
            return self._run_omniroute_tool_agent_loop(
                prompt, system_instruction, tools_list, tool_executor,
                history, user_preferences, on_status, max_iterations, timeout
            )
        elif self.provider == "nvidia":
            return self._run_nvidia_tool_agent_loop(
                prompt, system_instruction, tools_list, tool_executor,
                history, user_preferences, on_status, max_iterations, timeout
            )
        elif self.provider == "groq":
            return self._run_groq_tool_agent_loop(
                prompt, system_instruction, tools_list, tool_executor,
                history, user_preferences, on_status, max_iterations, timeout
            )

        from google.genai import types

        # Inject user preferences into system instruction
        if user_preferences:
            pref_lines = [f"- {k}: {v}" for k, v in user_preferences.items()]
            if pref_lines:
                system_instruction = f"{system_instruction}\n\n[USER PREFERENCES & STORED FACTS]\n" + "\n".join(pref_lines)

        # Build multi-turn chat history
        history_contents = []
        if history:
            for item in history:
                role = item.get("role", "user")
                if role in ["assistant", "tars"]:
                    role = "model"
                text = item.get("content", "")
                if not text or not isinstance(text, str):
                    continue
                text = self._with_history_summary(text.strip(), item)
                history_contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))

            # If current prompt was already saved to history, strip it off the end
            if history_contents and history_contents[-1].role == "user" and history_contents[-1].parts:
                last_part = history_contents[-1].parts[0]
                if getattr(last_part, "text", "") == prompt:
                    history_contents.pop()

            # Ensure history starts with user turn
            while history_contents and history_contents[0].role != "user":
                history_contents.pop(0)

        last_error = None
        if not self.client:
            return {
                "success": False,
                "response": "LLM unavailable: Gemini client is not initialized.",
                "error": "Gemini client is None",
                "steps": [],
            }
        cfg_fallbacks = _load_llm_settings().get("fallback_models") or []
        candidate_models = [self.model]
        for candidate in list(cfg_fallbacks) + list(DEFAULT_MODELS):
            if candidate and candidate not in candidate_models:
                candidate_models.append(candidate)
        # A model that just timed out or was overloaded is skipped for a while
        # instead of costing every turn another 10-15s before the fallback runs.
        candidate_models = self._models_in_pause(candidate_models)
        if not candidate_models:
            candidate_models = [self.model]
        from tools.verification import is_task_cancelled

        failed_calls: Dict[str, int] = {}
        tool_fails: Dict[str, int] = {}

        for model_name in candidate_models:
            executed_steps = []
            try:
                config = self._gemini_config(system_instruction, tools_list, timeout, model_name=model_name)
                chat = self.client.chats.create(
                    model=model_name,
                    config=config,
                    history=history_contents if history_contents else None
                )

                current_message: Any = prompt

                for iteration in range(max_iterations):
                    if is_task_cancelled():
                        return {
                            "success": False,
                            "cancelled": True,
                            "response": "Task cancelled.",
                            "steps": executed_steps,
                        }
                    if on_status and iteration > 0:
                        on_status(f"Reasoning next step (step {iteration + 1})...")

                    response = chat.send_message(current_message)

                    # Inspect the model's response parts
                    function_calls = []
                    text_parts = []

                    for candidate in response.candidates or []:
                        for part in candidate.content.parts or []:
                            if hasattr(part, "function_call") and part.function_call:
                                function_calls.append(part.function_call)
                            elif hasattr(part, "text") and part.text:
                                text_parts.append(part.text)

                    # If no function calls requested, the model has finished
                    if not function_calls:
                        final_text = _strip_tool_markers(" ".join(text_parts).strip()) or "Task completed."
                        return self._finalize_loop_reply(final_text, executed_steps, model_name)

                    # Execute each requested tool call
                    response_parts = []
                    for fc in function_calls:
                        tool_name = fc.name
                        tool_args = dict(fc.args or {})

                        if is_task_cancelled():
                            return {
                                "success": False,
                                "cancelled": True,
                                "response": "Task cancelled.",
                                "steps": executed_steps,
                            }

                        if on_status:
                            on_status(f"Executing: {tool_name}")

                        # Execute through central executor
                        result = tool_executor(tool_name, tool_args)
                        executed_steps.append({
                            "tool": tool_name,
                            "args": tool_args,
                            "result": result
                        })

                        # Format structured response part back to Gemini
                        response_part = types.Part.from_function_response(
                            name=tool_name,
                            response={"result": result}
                        )
                        response_parts.append(response_part)

                        # A repeated identical call that keeps failing is a loop, not progress.
                        call_key = json.dumps([tool_name, tool_args], sort_keys=True, default=str)
                        stop = _loop_guard(tool_name, tool_args, result, tool_fails)
                        if stop:
                            stop["steps"] = executed_steps
                            return stop
                        if isinstance(result, dict) and result.get("success") is False:
                            failed_calls[call_key] = failed_calls.get(call_key, 0) + 1
                            if failed_calls[call_key] >= 2:
                                return {
                                    "success": False,
                                    "response": f"Tool '{tool_name}' failed twice with the same arguments. Stopping.",
                                    "error": result.get("error") or result.get("message"),
                                    "steps": executed_steps,
                                }
                        else:
                            failed_calls.pop(call_key, None)

                    # Pass tool execution outputs back to the chat for next turn
                    current_message = response_parts

                # If reached max iterations, ask model for a summary
                summary_prompt = "Provide a concise summary of the actions taken and current status."
                final_resp = chat.send_message(summary_prompt)
                return {
                    "success": True,
                    "response": final_resp.text or "Completed requested actions.",
                    "steps": executed_steps,
                    "model": model_name
                }

            except Exception as e:
                err_msg = str(e)
                last_error = err_msg
                _log_provider_exception(f"Error during LLM execution with model '{model_name}'", e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "Quota exceeded" in err_msg:
                    self.mark_quota_exhausted(45.0)
                    short_reason = "quota exhausted"
                    return {
                        "success": False,
                        "response": f"LLM unavailable: {short_reason}.",
                        "error": err_msg,
                        "steps": executed_steps
                    }
                if "404" in err_msg or "NOT_FOUND" in err_msg:
                    # Never fall back after tools have already run: a second model
                    # would repeat side effects that already happened.
                    if executed_steps:
                        return {
                            "success": False,
                            "response": "LLM unavailable: model not found after tools already ran.",
                            "error": err_msg,
                            "steps": executed_steps
                        }
                    continue
                transient = any(
                    marker in err_msg
                    for marker in (
                        "503", "UNAVAILABLE", "500", "502", "504",
                        "overloaded", "Server disconnected", "INVALID_ARGUMENT", "400",
                    )
                )
                if transient and not executed_steps:
                    # Upstream overload, timeout or unsupported parameter: skip this
                    # model for a while and try the next configured one.  Without the
                    # pause every turn paid the same timeout before failing over.
                    self._pause_model(model_name, 180.0 if "400" in err_msg or "INVALID_ARGUMENT" in err_msg else 120.0)
                    continue
                short_reason = err_msg.split("\n")[0][:80]
                return {
                    "success": False,
                    "response": f"LLM unavailable: {short_reason}.",
                    "error": err_msg,
                    "steps": executed_steps
                }

        short_reason = (last_error or "all models failed").split("\n")[0][:80]
        return {
            "success": False,
            "response": f"LLM unavailable: {short_reason}.",
            "error": last_error or "Unknown error",
            "steps": []
        }

    # Fixed value sets for parameters whose tool accepts only specific strings.
    _ENUM_VALUES: Dict[str, List[str]] = {
        "media_control.action": [
            "play_pause", "next", "prev", "stop", "volume_up", "volume_down", "mute",
        ],
        "system_power.action": ["sleep", "restart", "shutdown", "cancel"],
        "smart_search.engine": ["google", "youtube", "duckduckgo"],
        "browser_search.engine": ["google", "youtube", "duckduckgo"],
        "snap_window.direction": ["left", "right", "up", "down", "maximize", "minimize"],
        "manage_virtual_desktop.action": ["new", "next", "prev", "close"],
        "get_top_processes.sort_by": ["cpu", "ram"],
        "zip_or_unzip.action": ["zip", "unzip"],
        "browser_scroll.direction": ["down", "up"],
        "browser_history_nav.action": ["back", "forward", "reload"],
    }

    @staticmethod
    def _gemini_config(
        system_instruction: str,
        tools_list: List[Callable],
        timeout: float,
        model_name: str = "",
    ) -> Any:
        """
        Builds the Gemini GenerateContentConfig.

        The Gemini loop previously ignored its timeout argument entirely; the
        per-request timeout is applied here when the installed google-genai
        version exposes HttpOptions.
        """
        from google.genai import types

        kwargs: Dict[str, Any] = {
            "system_instruction": system_instruction,
            "tools": tools_list,
            "temperature": 0.2,
        }
        try:
            http_kwargs: Dict[str, Any] = {"timeout": int(max(1.0, timeout) * 1000)}
            if hasattr(types, "HttpRetryOptions"):
                # Avoid 35-90s SDK internal retries on 503/504 so TARS fails over immediately.
                http_kwargs["retry_options"] = types.HttpRetryOptions(attempts=1)
            kwargs["http_options"] = types.HttpOptions(**http_kwargs)
        except Exception:
            pass
        # Flash models spend most of their latency on internal reasoning that this
        # agent does not need: the ReAct loop already supplies the reasoning by
        # feeding tool results back.  Skip this flag on '-latest' / '2.0' models
        # because gemini-flash-lite-latest rejects thinking_budget=0 with HTTP 400.
        m_lower = (model_name or "").lower()
        if "latest" not in m_lower and "2.0" not in m_lower:
            try:
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:
                pass
        try:
            return types.GenerateContentConfig(**kwargs)
        except Exception:
            kwargs.pop("http_options", None)
            kwargs.pop("thinking_config", None)
            return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _json_schema_for(annotation: Any) -> Dict[str, Any]:
        """
        Maps a Python annotation to a JSON-schema fragment.

        Bare-dict lookups lose parameterised generics: ``List[str]`` and
        ``Optional[int]`` both degraded to ``"string"``, so models sent a string
        where the tool expected a list.
        """
        import typing

        origin = typing.get_origin(annotation)
        if origin is typing.Union:
            args = [a for a in typing.get_args(annotation) if a is not type(None)]
            return LLMProvider._json_schema_for(args[0]) if args else {"type": "string"}
        if origin in (list, set, tuple) or annotation in (list, set, tuple):
            args = typing.get_args(annotation)
            item = LLMProvider._json_schema_for(args[0]) if args else {"type": "string"}
            return {"type": "array", "items": item}
        if origin is dict or annotation is dict:
            return {"type": "object"}
        mapping = {int: "integer", float: "number", bool: "boolean", str: "string"}
        return {"type": mapping.get(annotation, "string")}

    @staticmethod
    def _arg_descriptions(function: Callable) -> Dict[str, str]:
        """Extracts per-parameter descriptions from a tool's docstring Args block."""
        doc = inspect.getdoc(function) or ""
        descriptions: Dict[str, str] = {}
        in_args = False
        for raw_line in doc.splitlines():
            line = raw_line.strip()
            if line.lower().startswith("args:"):
                in_args = True
                continue
            if not in_args:
                continue
            if line.lower().startswith(("returns:", "raises:", "example", "note:")):
                break
            match = re.match(r"^(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$", line)
            if match:
                descriptions[match.group(1).lstrip("*")] = match.group(2).strip()
        return descriptions

    @classmethod
    def _ollama_tool_schema(cls, function: Callable) -> Dict[str, Any]:
        """Converts a registered Python tool into Ollama's function schema."""
        signature = inspect.signature(function)
        properties: Dict[str, Any] = {}
        required: List[str] = []
        descriptions = cls._arg_descriptions(function)
        for name, parameter in signature.parameters.items():
            if name.startswith("_"):
                continue
            schema = cls._json_schema_for(parameter.annotation)
            enum_values = cls._ENUM_VALUES.get(f"{function.__name__}.{name}")
            if enum_values:
                schema = {**schema, "enum": enum_values}
            description = descriptions.get(name)
            if description:
                schema = {**schema, "description": description}
            properties[name] = schema
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        return {
            "type": "function",
            "function": {
                "name": function.__name__,
                "description": inspect.getdoc(function) or f"Run {function.__name__}.",
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    @staticmethod
    def _strip_ollama_thinking(content: str) -> str:
        """Removes Qwen thinking text when a model emits it despite think=false."""
        if "</think>" in content:
            return content.rsplit("</think>", 1)[-1].strip()
        return content.strip()

    @staticmethod
    def _summarize_steps(steps: Any) -> str:
        """
        Compact one-line summary of executed tools.

        History rows keep the tool list separately from the message text, so the
        model previously could not see which tools it had already run.
        """
        if not isinstance(steps, list) or not steps:
            return ""
        parts: List[str] = []
        for step in steps[:6]:
            if not isinstance(step, dict):
                continue
            name = step.get("tool") or step.get("tool_name") or "tool"
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            arg_text = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
            result = step.get("result")
            if isinstance(result, dict):
                if result.get("success") is False:
                    outcome = "failed"
                else:
                    outcome = str(result.get("message") or result.get("summary") or "ok")[:60]
            else:
                outcome = "ok"
            parts.append(f"{name}({arg_text}) -> {outcome}")
        return f"[tools: {'; '.join(parts)}]" if parts else ""

    @staticmethod
    def _with_history_summary(content: str, item: Dict[str, Any]) -> str:
        """Appends a compact tool summary to an assistant history entry."""
        if item.get("role") not in ("assistant", "tars", "model"):
            return content
        summary = LLMProvider._summarize_steps(item.get("tool_calls"))
        return f"{content}\n{summary}" if summary else content

    def _run_ollama_tool_agent_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]],
        user_preferences: Optional[Dict[str, Any]],
        on_status: Optional[Callable[[str], None]],
        max_iterations: int,
        timeout: float,
    ) -> Dict[str, Any]:
        """Runs Ollama's documented multi-turn /api/chat function-call loop."""
        import requests

        if user_preferences:
            facts = "\n".join(f"- {key}: {value}" for key, value in user_preferences.items())
            system_instruction = f"{system_instruction}\n\n[USER PREFERENCES & STORED FACTS]\n{facts}"

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_instruction}]
        for item in history or []:
            role = item.get("role", "user")
            if role in {"assistant", "tars", "model"}:
                role = "assistant"
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                messages.append({
                    "role": role,
                    "content": self._with_history_summary(content.strip(), item),
                })
        if messages[-1].get("role") == "user" and messages[-1].get("content") == prompt:
            messages.pop()
        messages.append({"role": "user", "content": prompt})

        tool_schemas = [self._ollama_tool_schema(function) for function in tools_list]
        steps: List[Dict[str, Any]] = []
        from tools.verification import is_task_cancelled

        failed_calls: Dict[str, int] = {}
        tool_fails: Dict[str, int] = {}
        try:
            for iteration in range(max_iterations):
                if is_task_cancelled():
                    return {
                        "success": False,
                        "cancelled": True,
                        "response": "Task cancelled.",
                        "steps": steps,
                    }
                if on_status and iteration:
                    on_status(f"Reasoning next step (step {iteration + 1})...")
                response = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": tool_schemas,
                        "stream": False,
                        "think": False,
                        "keep_alive": "10m",
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                assistant_message = response.json().get("message", {})
                if not assistant_message:
                    raise RuntimeError("Ollama returned no assistant message")
                messages.append(assistant_message)
                tool_calls = assistant_message.get("tool_calls") or []
                if not tool_calls:
                    cleaned = _strip_tool_markers(
                        self._strip_ollama_thinking(assistant_message.get("content", ""))
                    ) or "Task completed."
                    return self._finalize_loop_reply(cleaned, steps, self.model)
                for call in tool_calls:
                    function = call.get("function", {})
                    tool_name = function.get("name", "")
                    tool_args = function.get("arguments") or {}
                    if is_task_cancelled():
                        return {
                            "success": False,
                            "cancelled": True,
                            "response": "Task cancelled.",
                            "steps": steps,
                        }
                    if on_status:
                        on_status(f"Executing: {tool_name}")
                    result = tool_executor(tool_name, tool_args)
                    steps.append({"tool": tool_name, "args": tool_args, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": json.dumps(result, default=str),
                    })

                    # A repeated identical call that keeps failing is a loop, not progress.
                    call_key = json.dumps([tool_name, tool_args], sort_keys=True, default=str)
                    stop = _loop_guard(tool_name, tool_args, result, tool_fails)
                    if stop:
                        stop["steps"] = steps
                        return stop
                    if isinstance(result, dict) and result.get("success") is False:
                        failed_calls[call_key] = failed_calls.get(call_key, 0) + 1
                        if failed_calls[call_key] >= 2:
                            return {
                                "success": False,
                                "response": f"Tool '{tool_name}' failed twice with the same arguments. Stopping.",
                                "error": result.get("error") or result.get("message"),
                                "steps": steps,
                            }
                    else:
                        failed_calls.pop(call_key, None)
            return {"success": False, "response": "Ollama stopped after too many tool steps.", "steps": steps}
        except Exception as exc:
            _log_provider_exception("Ollama execution failed", exc)
            return {
                "success": False,
                "response": f"LLM unavailable: {str(exc).splitlines()[0][:80]}.",
                "error": str(exc),
                "steps": steps,
            }

    def _run_openai_compatible_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]] = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_iterations: int = 5,
        timeout: float = 30.0,
        provider_name: str = "OpenRouter",
    ) -> Dict[str, Any]:
        """Runs multi-turn ReAct tool calling loop using OpenAI-compatible API (OpenRouter/OmniRoute)."""
        if user_preferences:
            facts = "\n".join(f"- {key}: {value}" for key, value in user_preferences.items())
            system_instruction = f"{system_instruction}\n\n[USER PREFERENCES & STORED FACTS]\n{facts}"

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_instruction}]
        for item in history or []:
            role = item.get("role", "user")
            if role in {"assistant", "tars", "model"}:
                role = "assistant"
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                messages.append({
                    "role": role,
                    "content": self._with_history_summary(content.strip(), item),
                })

        if len(messages) > 1 and messages[-1].get("role") == "user" and messages[-1].get("content") == prompt:
            messages.pop()
        messages.append({"role": "user", "content": prompt})

        tool_schemas = [self._ollama_tool_schema(function) for function in tools_list] if tools_list else []
        steps: List[Dict[str, Any]] = []
        from tools.verification import is_task_cancelled

        failed_calls: Dict[str, int] = {}
        tool_fails: Dict[str, int] = {}

        try:
            configured_max = int(_load_llm_settings().get("max_output_tokens") or 1024)
        except Exception:
            configured_max = 1024
        max_tokens = max(256, min(configured_max, 2048))

        try:
            for iteration in range(max_iterations):
                if is_task_cancelled():
                    logger.info("%s loop cancelled before iteration %s", provider_name, iteration + 1)
                    return {
                        "success": False,
                        "cancelled": True,
                        "response": "Task cancelled.",
                        "steps": steps,
                    }
                if on_status and iteration > 0:
                    on_status(f"Reasoning next step (step {iteration + 1})...")

                call_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }
                if tool_schemas:
                    call_kwargs["tools"] = tool_schemas

                try:
                    response = self.client.chat.completions.create(**call_kwargs)
                except Exception as call_exc:
                    if not steps and ("402" in str(call_exc) or "fewer max_tokens" in str(call_exc).lower()) and max_tokens > 512:
                        max_tokens = 512
                        call_kwargs["max_tokens"] = max_tokens
                        response = self.client.chat.completions.create(**call_kwargs)
                    else:
                        raise
                if not response.choices:
                    raise RuntimeError(f"{provider_name} returned no completion choices")

                assistant_msg = response.choices[0].message
                tool_calls = assistant_msg.tool_calls or []

                # Format assistant message for history
                assistant_dict: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                }
                if tool_calls:
                    assistant_dict["tool_calls"] = [
                        {
                            "id": getattr(tc, "id", None) or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for idx, tc in enumerate(tool_calls)
                    ]
                messages.append(assistant_dict)

                if not tool_calls:
                    final_content = assistant_msg.content or ""
                    cleaned = _strip_tool_markers(self._strip_ollama_thinking(final_content)) or "Task completed."
                    if is_gateway_notice(cleaned):
                        logger.warning(
                            "%s returned an upstream notice instead of a completion for model '%s'",
                            provider_name, self.model,
                        )
                        self.mark_quota_exhausted(60.0)
                        return {
                            "success": False,
                            "response": (
                                f"LLM unavailable: {provider_name} returned an unsupported-client "
                                f"notice for model '{self.model}'. Choose a different model."
                            ),
                            "error": cleaned,
                            "steps": steps,
                        }
                    return self._finalize_loop_reply(cleaned, steps, self.model)

                for tc_idx, tc in enumerate(tool_calls):
                    tc_id = getattr(tc, "id", None) or f"call_{tc_idx}"
                    tool_name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        tool_args = {}

                    if is_task_cancelled():
                        return {
                            "success": False,
                            "cancelled": True,
                            "response": "Task cancelled.",
                            "steps": steps,
                        }

                    if on_status:
                        on_status(f"Executing: {tool_name}")

                    # An ambiguous bare "send <greeting> ..." (e.g. a misheard
                    # "send hello how are you") has no real contact.  A capable
                    # model may still fire send_whatsapp_message; refuse so the
                    # user is asked for a name instead of messaging the void.
                    if tool_name == "send_whatsapp_message":
                        import re as _re
                        bare = _re.match(
                            r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?send\s+"
                            r"([a-z][a-z0-9._'-]{1,30})\s+.+$",
                            prompt or "",
                            _re.IGNORECASE,
                        )
                        if bare and bare.group(1).lower() in _BARE_SEND_STOPWORDS:
                            result = {
                                "success": False,
                                "error": (
                                    f"'{bare.group(1)}' is not a valid contact name. "
                                    "Ask the user who to message."
                                ),
                            }
                            steps.append({"tool": tool_name, "args": tool_args, "result": result})
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": tool_name,
                                "content": json.dumps(result, default=str),
                            })
                            continue

                    result = tool_executor(tool_name, tool_args)
                    steps.append({"tool": tool_name, "args": tool_args, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": tool_name,
                        "content": json.dumps(result, default=str),
                    })

                    # A repeated identical call that keeps failing is a loop, not progress.
                    call_key = json.dumps([tool_name, tool_args], sort_keys=True, default=str)
                    stop = _loop_guard(tool_name, tool_args, result, tool_fails)
                    if stop:
                        stop["steps"] = steps
                        return stop
                    if isinstance(result, dict) and result.get("success") is False:
                        failed_calls[call_key] = failed_calls.get(call_key, 0) + 1
                        if failed_calls[call_key] >= 2:
                            return {
                                "success": False,
                                "response": f"Tool '{tool_name}' failed twice with the same arguments. Stopping.",
                                "error": result.get("error") or result.get("message"),
                                "steps": steps,
                            }
                    else:
                        failed_calls.pop(call_key, None)

            # Max iterations reached, ask for summary
            messages.append({
                "role": "user",
                "content": "Provide a concise summary of the actions taken and current status.",
            })
            summary_resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            summary_text = summary_resp.choices[0].message.content or "Completed requested actions."
            cleaned_summary = self._strip_ollama_thinking(summary_text)
            if is_gateway_notice(cleaned_summary):
                logger.warning(
                    "%s returned an upstream notice instead of a summary for model '%s'",
                    provider_name, self.model,
                )
                self.mark_quota_exhausted(60.0)
                return {
                    "success": False,
                    "response": (
                        f"LLM unavailable: {provider_name} returned an unsupported-client "
                        f"notice for model '{self.model}'. Choose a different model."
                    ),
                    "error": cleaned_summary,
                    "steps": steps,
                }
            return {
                "success": True,
                "response": cleaned_summary,
                "steps": steps,
                "model": self.model,
            }

        except Exception as exc:
            err_msg = str(exc)
            _log_provider_exception(f"{provider_name} execution failed with model '{self.model}'", exc)
            if "429" in err_msg or "rate limit" in err_msg.lower():
                self.mark_quota_exhausted(60.0)
                short_reason = "rate limit reached"
            elif "402" in err_msg or "more credits" in err_msg.lower():
                self.mark_quota_exhausted(60.0)
                short_reason = f"insufficient {provider_name} credits"
            elif "401" in err_msg or "auth" in err_msg.lower() or "api key" in err_msg.lower():
                short_reason = f"invalid {provider_name} API key"
            elif "404" in err_msg or "not found" in err_msg.lower():
                short_reason = f"model '{self.model}' not found"
            else:
                short_reason = err_msg.splitlines()[0][:80]

            return {
                "success": False,
                "response": f"LLM unavailable: {short_reason}.",
                "error": err_msg,
                "steps": steps,
            }

    def _run_openrouter_tool_agent_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]] = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_iterations: int = 5,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Runs multi-turn ReAct tool calling loop using OpenRouter."""
        return self._run_openai_compatible_loop(
            prompt, system_instruction, tools_list, tool_executor,
            history, user_preferences, on_status, max_iterations, timeout,
            provider_name="OpenRouter"
        )

    def _run_omniroute_tool_agent_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]] = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_iterations: int = 5,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Runs multi-turn ReAct tool calling loop using OmniRoute gateway."""
        return self._run_openai_compatible_loop(
            prompt, system_instruction, tools_list, tool_executor,
            history, user_preferences, on_status, max_iterations, timeout,
            provider_name="OmniRoute"
        )

    def _run_nvidia_tool_agent_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]] = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_iterations: int = 5,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Runs multi-turn ReAct tool calling loop using the NVIDIA NIM API."""
        return self._run_openai_compatible_loop(
            prompt, system_instruction, tools_list, tool_executor,
            history, user_preferences, on_status, max_iterations, timeout,
            provider_name="NVIDIA"
        )

    def _run_groq_tool_agent_loop(
        self,
        prompt: str,
        system_instruction: str,
        tools_list: List[Callable],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        history: Optional[List[Dict[str, Any]]] = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_iterations: int = 5,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Runs multi-turn ReAct tool calling loop using the Groq API."""
        return self._run_openai_compatible_loop(
            prompt, system_instruction, tools_list, tool_executor,
            history, user_preferences, on_status, max_iterations, timeout,
            provider_name="Groq"
        )

