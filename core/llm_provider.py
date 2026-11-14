"""Configurable LLM provider adapter with structured multi-turn tool calling for TARS."""
import os
import time
import logging
import inspect
import json
import traceback
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-flash-latest"]
OLLAMA_DEFAULT_MODEL = "qwen3:4b"
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"

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


def _log_provider_exception(message: str, exc: Exception) -> None:
    """Shows a short console error while retaining the traceback in tars.log."""
    logger.error("%s: %s", message, exc)
    try:
        with open(log_file, "a", encoding="utf-8") as log_handle:
            log_handle.write(traceback.format_exc())
    except OSError:
        pass


class LLMProvider:
    """
    Unified AI provider adapter for Google Gemini and extensible providers.
    Supports structured tool calling, multi-step conversation planning,
    and automatic tool execution recovery.
    """

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = (provider or os.getenv("AI_PROVIDER", "ollama")).lower()
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
        else:
            default_model = "gemini-3.8-flash"
            model_env = os.getenv("GEMINI_MODEL") or os.getenv("AI_MODEL")

        self.model = model or model_env or default_model
        self.client = None
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self._quota_exhausted_until = 0.0
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
        if self.provider == "ollama":
            try:
                import requests
                response = requests.get(f"{self.ollama_url}/api/tags", timeout=1.5)
                return response.ok
            except Exception:
                return False
        if time.time() < self._quota_exhausted_until:
            return False
        return True

    def mark_quota_exhausted(self, duration_sec: float = 60.0) -> None:
        """Applies a temporary circuit breaker when API rate limits are reached."""
        self._quota_exhausted_until = time.time() + duration_sec

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
            elif self.provider == "openrouter":
                reason = "OPENROUTER_API_KEY not configured or rate limit pause in effect"
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
                history_contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text.strip())]))

            # If current prompt was already saved to history, strip it off the end
            if history_contents and history_contents[-1].role == "user" and history_contents[-1].parts:
                last_part = history_contents[-1].parts[0]
                if getattr(last_part, "text", "") == prompt:
                    history_contents.pop()

            # Ensure history starts with user turn
            while history_contents and history_contents[0].role != "user":
                history_contents.pop(0)

        last_error = None
        candidate_models = [self.model] + [m for m in DEFAULT_MODELS if m != self.model]

        for model_name in candidate_models:
            executed_steps = []
            try:
                chat = self.client.chats.create(
                    model=model_name,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=tools_list,
                        temperature=0.2
                    ),
                    history=history_contents if history_contents else None
                )

                current_message: Any = prompt

                for iteration in range(max_iterations):
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
                        final_text = " ".join(text_parts).strip() or "Task completed."
                        return {
                            "success": True,
                            "response": final_text,
                            "steps": executed_steps,
                            "model": model_name
                        }

                    # Execute each requested tool call
                    response_parts = []
                    for fc in function_calls:
                        tool_name = fc.name
                        tool_args = dict(fc.args or {})

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

    @staticmethod
    def _ollama_tool_schema(function: Callable) -> Dict[str, Any]:
        """Converts a registered Python tool into Ollama's function schema."""
        signature = inspect.signature(function)
        properties: Dict[str, Any] = {}
        required: List[str] = []
        type_map = {int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
        for name, parameter in signature.parameters.items():
            if name.startswith("_"):
                continue
            annotation = parameter.annotation
            json_type = type_map.get(annotation, "string")
            properties[name] = {"type": json_type}
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
                messages.append({"role": role, "content": content.strip()})
        if messages[-1].get("role") == "user" and messages[-1].get("content") == prompt:
            messages.pop()
        messages.append({"role": "user", "content": prompt})

        tool_schemas = [self._ollama_tool_schema(function) for function in tools_list]
        steps: List[Dict[str, Any]] = []
        try:
            for iteration in range(max_iterations):
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
                    return {
                        "success": True,
                        "response": self._strip_ollama_thinking(assistant_message.get("content", "")) or "Task completed.",
                        "steps": steps,
                        "model": self.model,
                    }
                for call in tool_calls:
                    function = call.get("function", {})
                    tool_name = function.get("name", "")
                    tool_args = function.get("arguments") or {}
                    if on_status:
                        on_status(f"Executing: {tool_name}")
                    result = tool_executor(tool_name, tool_args)
                    steps.append({"tool": tool_name, "args": tool_args, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": json.dumps(result, default=str),
                    })
            return {"success": False, "response": "Ollama stopped after too many tool steps.", "steps": steps}
        except Exception as exc:
            _log_provider_exception("Ollama execution failed", exc)
            return {
                "success": False,
                "response": f"LLM unavailable: {str(exc).splitlines()[0][:80]}.",
                "error": str(exc),
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
        """Runs multi-turn ReAct tool calling loop using OpenRouter (OpenAI-compatible API)."""
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
                messages.append({"role": role, "content": content.strip()})

        if len(messages) > 1 and messages[-1].get("role") == "user" and messages[-1].get("content") == prompt:
            messages.pop()
        messages.append({"role": "user", "content": prompt})

        tool_schemas = [self._ollama_tool_schema(function) for function in tools_list] if tools_list else []
        steps: List[Dict[str, Any]] = []

        try:
            for iteration in range(max_iterations):
                if on_status and iteration > 0:
                    on_status(f"Reasoning next step (step {iteration + 1})...")

                call_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "timeout": timeout,
                }
                if tool_schemas:
                    call_kwargs["tools"] = tool_schemas

                response = self.client.chat.completions.create(**call_kwargs)
                if not response.choices:
                    raise RuntimeError("OpenRouter returned no completion choices")

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
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ]
                messages.append(assistant_dict)

                if not tool_calls:
                    final_content = assistant_msg.content or ""
                    cleaned = self._strip_ollama_thinking(final_content) or "Task completed."
                    return {
                        "success": True,
                        "response": cleaned,
                        "steps": steps,
                        "model": self.model,
                    }

                for tc in tool_calls:
                    tool_name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        tool_args = {}

                    if on_status:
                        on_status(f"Executing: {tool_name}")

                    result = tool_executor(tool_name, tool_args)
                    steps.append({"tool": tool_name, "args": tool_args, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tool_name,
                        "content": json.dumps(result, default=str),
                    })

            # Max iterations reached, ask for summary
            messages.append({
                "role": "user",
                "content": "Provide a concise summary of the actions taken and current status.",
            })
            summary_resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                timeout=timeout,
            )
            summary_text = summary_resp.choices[0].message.content or "Completed requested actions."
            return {
                "success": True,
                "response": self._strip_ollama_thinking(summary_text),
                "steps": steps,
                "model": self.model,
            }

        except Exception as exc:
            err_msg = str(exc)
            _log_provider_exception(f"OpenRouter execution failed with model '{self.model}'", exc)
            if "429" in err_msg or "rate limit" in err_msg.lower():
                self.mark_quota_exhausted(60.0)
                short_reason = "rate limit reached"
            elif "401" in err_msg or "auth" in err_msg.lower() or "api key" in err_msg.lower():
                short_reason = "invalid OpenRouter API key"
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

