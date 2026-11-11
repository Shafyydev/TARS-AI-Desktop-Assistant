"""Configurable LLM provider adapter with structured multi-turn tool calling for TARS."""
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-flash-latest"]

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


class LLMProvider:
    """
    Unified AI provider adapter for Google Gemini and extensible providers.
    Supports structured tool calling, multi-step conversation planning,
    and automatic tool execution recovery.
    """

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider or os.getenv("AI_PROVIDER", "gemini").lower()
        self.model = model or os.getenv("AI_MODEL", "gemini-3.8-flash")
        self.client = None
        self._quota_exhausted_until = 0.0
        self._init_client()

    def _init_client(self) -> None:
        if self.provider == "gemini":
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
            reason = "API key not configured or rate limit pause in effect"
            logger.warning(f"LLM execution skipped: {reason}")
            return {
                "success": False,
                "response": f"LLM unavailable: {reason}.",
                "error": reason,
                "steps": []
            }

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
                logger.error(f"Error during LLM execution with model '{model_name}': {err_msg}", exc_info=True)
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

