"""Persistent in-task state manager and contextual contact/conversation resolver for WhatsApp Desktop."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tars.whatsapp.state")


_ORDINAL_WORDS: Dict[str, int] = {
    "first": 1, "1st": 1, "one": 1,
    "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
    "sixth": 6, "6th": 6, "six": 6,
    "seventh": 7, "7th": 7, "seven": 7,
    "eighth": 8, "8th": 8, "eight": 8,
    "ninth": 9, "9th": 9, "nine": 9,
    "tenth": 10, "10th": 10, "ten": 10,
}

_PRONOUN_ACTIVE_REFS = frozenset({
    "him", "her", "his", "hers", "their", "them", "he", "she",
    "same", "same person", "the same person", "same contact", "that person",
    "that contact", "this contact", "current contact", "active contact",
    "that chat", "this chat", "that conversation", "this conversation",
    "active chat", "current chat", "his chat", "her chat", "their chat",
    "it", "that one",
})

_PREVIOUS_RESULT_REFS = frozenset({
    "previous result", "the previous result", "last result", "previous chat",
    "the previous chat", "that result",
})


@dataclass
class SearchResultEntry:
    """Structured representation of a visible WhatsApp search or list item."""

    index: int
    name: str
    section: str = "chats"  # "chats" | "contacts" | "messages" | "groups in common"
    raw_label: str = ""
    preview: str = ""
    timestamp: str = ""
    unread_count: int = 0
    pinned: bool = False
    is_group: bool = False

    def summary_line(self) -> str:
        bits = [f"{self.index}. {self.name}"]
        if self.section == "messages" and self.preview:
            bits.append(f"(message match: \"{self.preview[:60]}\")")
        elif self.preview:
            bits.append(f"[{self.preview[:45]}]")
        if self.unread_count > 0:
            bits.append(f"({self.unread_count} unread)")
        if self.pinned:
            bits.append("[Pinned]")
        return " ".join(bits)


@dataclass
class WhatsAppAgentState:
    """Serializable WhatsApp Desktop task context distinguishing intended, observed, and verified states."""

    application: str = "WhatsApp Desktop"
    whatsapp_window: Dict[str, Any] = field(default_factory=lambda: {
        "hwnd": None,
        "title": "",
        "process": "",
        "active": False,
        "visible": False,
    })
    active_screen: str = "unknown"  # "not_running" | "loading" | "chat_list" | "search_results" | "unread_list" | "active_chat" | "contact_info" | "status_view" | "unknown"
    active_chat: Optional[str] = None
    selected_contact: Optional[str] = None
    previously_selected_contact: Optional[str] = None
    last_search_query: Optional[str] = None
    search_results: List[Dict[str, Any]] = field(default_factory=list)
    selected_search_result: Optional[Dict[str, Any]] = None
    last_executed_action: Optional[Dict[str, Any]] = None
    last_verified_action: Optional[Dict[str, Any]] = None
    pending_user_instruction: Optional[str] = None
    pending_action: Optional[str] = None
    workflow_objective: Optional[str] = None
    workflow_status: str = "idle"  # "idle" | "in_progress" | "awaiting_user_selection" | "awaiting_confirmation" | "verified" | "failed"
    clarification_request: Optional[str] = None
    previous_results: List[Dict[str, Any]] = field(default_factory=list)

    intended_state: Dict[str, Any] = field(default_factory=lambda: {
        "target_action": None,
        "target_contact": None,
        "message": None,
    })
    observed_state: Dict[str, Any] = field(default_factory=lambda: {
        "window_verified": False,
        "window_title": "",
        "foreground_process": "",
        "active_chat": None,
        "search_query": None,
        "search_results_count": 0,
        "composer_present": False,
        "composer_text": "",
    })
    verified_state: Dict[str, Any] = field(default_factory=lambda: {
        "window_verified": False,
        "active_chat": None,
        "active_chat_verified": False,
        "last_search_verified": False,
        "composer_verified": False,
    })
    uncertain_sends: Dict[str, float] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)


class WhatsAppStateManager:
    """Thread-safe manager tracking live WhatsApp UI observations, task context, and reference resolution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = WhatsAppAgentState()

    def reset(self) -> None:
        with self._lock:
            self._state = WhatsAppAgentState()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return asdict(self._state)

    def to_prompt_context(self) -> str:
        """Formats current observed/verified WhatsApp task context for LLM reasoning."""
        with self._lock:
            s = self._state
            if (
                s.active_screen == "unknown"
                and not s.active_chat
                and not s.search_results
                and not s.last_executed_action
                and not s.whatsapp_window.get("visible")
            ):
                return ""
            lines = [
                "[WHATSAPP TASK STATE]",
                f"- Application target: {s.application}",
                f"- Window visible/active: {bool(s.whatsapp_window.get('visible'))}/{bool(s.whatsapp_window.get('active'))} (process={s.whatsapp_window.get('process') or 'none'}, verified={s.verified_state.get('window_verified')})",
                f"- Observed active screen: {s.active_screen}",
                f"- Verified active chat: {s.verified_state.get('active_chat') or 'none'} (header_verified={bool(s.verified_state.get('active_chat_verified'))})",
                f"- Selected contact: {s.selected_contact or 'none'} | Previous contact: {s.previously_selected_contact or 'none'}",
                f"- Workflow status: {s.workflow_status} | Pending action: {s.pending_action or 'none'}",
            ]
            if s.last_search_query:
                lines.append(f"- Last search query: '{s.last_search_query}' (search_verified={bool(s.verified_state.get('last_search_verified'))})")
            if s.search_results:
                preview = "; ".join(
                    SearchResultEntry(**{k: v for k, v in r.items() if k in SearchResultEntry.__dataclass_fields__}).summary_line()
                    for r in s.search_results[:8]
                )
                lines.append(f"- Ordered search/list results: {preview}")
            if s.selected_search_result:
                lines.append(
                    f"- Selected search result: #{s.selected_search_result.get('index')} {s.selected_search_result.get('name')}"
                )
            if s.clarification_request:
                lines.append(f"- Outstanding clarification: {s.clarification_request}")
            if s.last_executed_action:
                lines.append(
                    f"- Last executed action: {s.last_executed_action.get('action')} -> success={s.last_executed_action.get('success')} verified={s.last_executed_action.get('verified')}"
                )
            if s.last_verified_action:
                lines.append(
                    f"- Last verified action: {s.last_verified_action.get('action')} ({s.last_verified_action.get('detail', '')})"
                )
            return "\n".join(lines)

    def begin_intent(
        self,
        action: str,
        contact: Optional[str] = None,
        message: Optional[str] = None,
        instruction: Optional[str] = None,
        objective: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._state.intended_state = {
                "target_action": action,
                "target_contact": contact,
                "message": message,
            }
            if instruction:
                self._state.pending_user_instruction = instruction
            if objective:
                self._state.workflow_objective = objective
            self._state.pending_action = action
            self._state.workflow_status = "in_progress"
            self._state.updated_at = time.time()
            logger.info("INTENT action=%s contact=%r", action, contact)

    def record_window_state(
        self,
        *,
        visible: bool,
        active: bool,
        hwnd: Optional[int] = None,
        title: str = "",
        process: str = "",
        verified: bool = False,
        screen: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._state.whatsapp_window = {
                "hwnd": hwnd,
                "title": title,
                "process": process,
                "active": bool(active),
                "visible": bool(visible),
            }
            self._state.observed_state["window_verified"] = bool(verified)
            self._state.observed_state["window_title"] = title
            self._state.observed_state["foreground_process"] = process
            self._state.verified_state["window_verified"] = bool(verified)
            if screen:
                self._state.active_screen = screen
            elif not visible:
                self._state.active_screen = "not_running"
                self._state.verified_state["active_chat_verified"] = False
            self._state.updated_at = time.time()

    def record_search(
        self,
        query: str,
        results: List[Dict[str, Any]],
        *,
        screen: str = "search_results",
        verified: bool = True,
        clarification: Optional[str] = None,
    ) -> None:
        with self._lock:
            clean_results = []
            for idx, item in enumerate(results, start=1):
                entry = dict(item)
                entry["index"] = idx
                clean_results.append(entry)
            self._state.last_search_query = query
            self._state.search_results = clean_results
            self._state.selected_search_result = clean_results[0] if len(clean_results) == 1 else None
            self._state.active_screen = screen
            self._state.observed_state["search_query"] = query
            self._state.observed_state["search_results_count"] = len(clean_results)
            self._state.verified_state["last_search_verified"] = bool(verified)
            if len(clean_results) > 1:
                self._state.pending_action = "open_chat"
                self._state.workflow_status = "awaiting_user_selection"
                self._state.clarification_request = clarification or (
                    f"Multiple contacts match '{query}'. Select 1..{len(clean_results)}."
                )
            elif len(clean_results) == 1:
                self._state.clarification_request = None
                self._state.workflow_status = "observed" if verified else "in_progress"
            else:
                self._state.clarification_request = clarification
                self._state.workflow_status = "failed"
            self._state.updated_at = time.time()
            logger.info("SEARCH query=%r count=%d verified=%s", query, len(clean_results), verified)

    def record_verified_chat(
        self,
        contact_name: str,
        *,
        header_raw: str = "",
        selected_entry: Optional[Dict[str, Any]] = None,
        composer_present: bool = True,
    ) -> None:
        with self._lock:
            prev = self._state.active_chat or self._state.selected_contact
            if prev and prev.casefold() != contact_name.casefold():
                self._state.previously_selected_contact = prev
            self._state.selected_contact = contact_name
            self._state.active_chat = contact_name
            self._state.active_screen = "active_chat"
            if selected_entry is not None:
                self._state.selected_search_result = dict(selected_entry)
            self._state.observed_state["active_chat"] = header_raw or contact_name
            self._state.observed_state["composer_present"] = bool(composer_present)
            self._state.verified_state["active_chat"] = contact_name
            self._state.verified_state["active_chat_verified"] = True
            self._state.clarification_request = None
            self._state.pending_action = None
            self._state.workflow_status = "verified"
            self._state.updated_at = time.time()
            logger.info("VERIFIED_CHAT contact=%r header=%r", contact_name, header_raw)

    def record_observation(
        self,
        *,
        active_screen: Optional[str] = None,
        active_chat: Optional[str] = None,
        active_chat_verified: Optional[bool] = None,
        composer_present: Optional[bool] = None,
        composer_text: Optional[str] = None,
        search_query: Optional[str] = None,
    ) -> None:
        with self._lock:
            if active_screen is not None:
                self._state.active_screen = active_screen
            if active_chat is not None:
                self._state.active_chat = active_chat
                self._state.observed_state["active_chat"] = active_chat
                if not self._state.selected_contact:
                    self._state.selected_contact = active_chat
            if active_chat_verified is not None:
                self._state.verified_state["active_chat_verified"] = bool(active_chat_verified)
                self._state.verified_state["active_chat"] = active_chat if active_chat_verified else None
            if composer_present is not None:
                self._state.observed_state["composer_present"] = bool(composer_present)
            if composer_text is not None:
                self._state.observed_state["composer_text"] = composer_text
                self._state.verified_state["composer_verified"] = bool(composer_text.strip())
            if search_query is not None:
                self._state.observed_state["search_query"] = search_query
            self._state.updated_at = time.time()

    def record_action_outcome(
        self,
        action: str,
        *,
        success: bool,
        verified: bool,
        detail: str = "",
        error: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            record = {
                "action": action,
                "success": bool(success),
                "verified": bool(verified),
                "detail": detail,
                "error": error,
                "timestamp": time.time(),
            }
            self._state.last_executed_action = record
            if verified and success:
                self._state.last_verified_action = record
                self._state.workflow_status = "verified"
                self._state.pending_action = None
            elif not success:
                self._state.workflow_status = "failed"
            self._state.previous_results.append({
                "action": action,
                "success": bool(success),
                "verified": bool(verified),
                "detail": detail,
                "error": error,
                "data": data or {},
            })
            if len(self._state.previous_results) > 8:
                self._state.previous_results = self._state.previous_results[-8:]
            self._state.updated_at = time.time()
            logger.info("ACTION action=%s success=%s verified=%s detail=%s", action, success, verified, detail or error)

    @staticmethod
    def _send_fingerprint(contact: str, message: str) -> str:
        c = re.sub(r"\s+", " ", (contact or "").strip().casefold())
        m = re.sub(r"\s+", " ", (message or "").strip())
        return f"{c}||{m}"

    def record_uncertain_send(self, contact: str, message: str) -> None:
        with self._lock:
            fp = self._send_fingerprint(contact, message)
            self._state.uncertain_sends[fp] = time.time()

    def clear_uncertain_send(self, contact: str, message: str) -> None:
        with self._lock:
            fp = self._send_fingerprint(contact, message)
            self._state.uncertain_sends.pop(fp, None)

    def has_recent_uncertain_send(self, contact: str, message: str, window_sec: float = 120.0) -> bool:
        with self._lock:
            fp = self._send_fingerprint(contact, message)
            ts = self._state.uncertain_sends.get(fp)
            if ts is None:
                return False
            if (time.time() - ts) > window_sec:
                self._state.uncertain_sends.pop(fp, None)
                return False
            return True

    def resolve_contact_reference(self, reference: str) -> Dict[str, Any]:
        """Resolves ordinal/contextual contact references strictly from observed task state."""
        with self._lock:
            raw = (reference or "").strip()
            low = re.sub(r"\s+", " ", raw.casefold()).strip(" ?!.")
            s = self._state

            if not low or low in {"active", "current", "this"}:
                if s.active_chat:
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": s.active_chat,
                        "source": "active_chat",
                        "selected_result": s.selected_search_result,
                        "ambiguous": False,
                    }
                return {
                    "success": False,
                    "resolved": False,
                    "ambiguous": False,
                    "error": "No active WhatsApp conversation is currently open.",
                    "reason": "no_active_chat",
                }

            # Strip outer navigational phrasing ("open the ", "select the ", "in whatsapp")
            stripped = re.sub(
                r"^(?:open|select|show|choose|pick|go\s+to)\s+", "", low
            ).strip()
            stripped = re.sub(r"^the\s+", "", stripped).strip()
            stripped_item = re.sub(
                r"\s+(?:one|chat|contact|result|option|item|person|conversation|match|entry)$",
                "",
                stripped,
            ).strip()

            # 1. Explicit ordinal references ("first", "first chat", "2nd result", "result 3", "last")
            if stripped in {"last", "last one", "last result", "last chat"} or stripped_item == "last":
                if s.search_results:
                    chosen = s.search_results[-1]
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": chosen["name"],
                        "source": "search_results_last",
                        "selected_result": chosen,
                        "ambiguous": False,
                    }
                return {
                    "success": False,
                    "resolved": False,
                    "ambiguous": False,
                    "error": "There are no previous WhatsApp search results to pick from.",
                    "reason": "no_search_results",
                }

            idx_match = None
            for token in (stripped_item, stripped):
                if token in _ORDINAL_WORDS:
                    idx_match = _ORDINAL_WORDS[token]
                    break
                m_num = re.match(r"^(?:option|result|number|no|#)?\s*(\d{1,2})$", token)
                if m_num:
                    idx_match = int(m_num.group(1))
                    break

            if idx_match is not None:
                if not s.search_results:
                    return {
                        "success": False,
                        "resolved": False,
                        "ambiguous": False,
                        "error": f"Cannot resolve '{raw}': no recent WhatsApp search results are stored.",
                        "reason": "no_search_results",
                    }
                if 1 <= idx_match <= len(s.search_results):
                    chosen = s.search_results[idx_match - 1]
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": chosen["name"],
                        "source": "search_results_ordinal",
                        "selected_result": chosen,
                        "ambiguous": False,
                    }
                return {
                    "success": False,
                    "resolved": False,
                    "ambiguous": False,
                    "candidates": s.search_results,
                    "error": f"Requested item #{idx_match}, but only {len(s.search_results)} search result(s) exist.",
                    "reason": "index_out_of_range",
                }

            # 2. Previous single search/discussion result ("previous result", "open that conversation")
            if low in _PREVIOUS_RESULT_REFS or stripped in _PREVIOUS_RESULT_REFS:
                candidate = s.selected_search_result or (s.search_results[0] if len(s.search_results) == 1 else None)
                if candidate:
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": candidate["name"],
                        "source": "selected_search_result",
                        "selected_result": candidate,
                        "ambiguous": False,
                    }
                if len(s.search_results) > 1:
                    return {
                        "success": False,
                        "resolved": False,
                        "ambiguous": True,
                        "candidates": s.search_results,
                        "error": "Multiple previous search results exist; please specify which index or name.",
                        "clarification_question": self._format_clarification(s.last_search_query or raw, s.search_results),
                        "reason": "ambiguous_previous_results",
                    }

            # 3. Alternate contact ("the other Shafi", "the other one", "next one")
            if stripped.startswith("other") or stripped_item.startswith("other") or stripped in {"next", "next one"}:
                if not s.search_results:
                    return {
                        "success": False,
                        "resolved": False,
                        "ambiguous": False,
                        "error": "No previous search results available to pick an alternate contact.",
                        "reason": "no_search_results",
                    }
                excluded = {
                    (c or "").casefold()
                    for c in (s.active_chat, s.selected_contact, s.previously_selected_contact)
                    if c
                }
                remaining = [r for r in s.search_results if r["name"].casefold() not in excluded]
                if len(remaining) == 1:
                    chosen = remaining[0]
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": chosen["name"],
                        "source": "search_results_alternate",
                        "selected_result": chosen,
                        "ambiguous": False,
                    }
                if len(remaining) > 1:
                    return {
                        "success": False,
                        "resolved": False,
                        "ambiguous": True,
                        "candidates": remaining,
                        "error": "Multiple other matching contacts remain; clarification required.",
                        "clarification_question": self._format_clarification(s.last_search_query or raw, remaining),
                        "reason": "ambiguous_alternate",
                    }

            # 4. Active/previous person/conversation pronouns ("his", "her", "that chat", "same person")
            if low in _PRONOUN_ACTIVE_REFS or stripped in _PRONOUN_ACTIVE_REFS:
                target = s.active_chat or s.selected_contact or s.previously_selected_contact
                if target:
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": target,
                        "source": "active_or_recent_contact",
                        "selected_result": s.selected_search_result,
                        "ambiguous": False,
                    }
                if len(s.search_results) == 1:
                    return {
                        "success": True,
                        "resolved": True,
                        "contact": s.search_results[0]["name"],
                        "source": "single_search_result",
                        "selected_result": s.search_results[0],
                        "ambiguous": False,
                    }
                if len(s.search_results) > 1:
                    return {
                        "success": False,
                        "resolved": False,
                        "ambiguous": True,
                        "candidates": s.search_results,
                        "error": "Multiple search results exist; ask which conversation to open.",
                        "clarification_question": self._format_clarification(s.last_search_query or raw, s.search_results),
                        "reason": "ambiguous_pronoun",
                    }
                return {
                    "success": False,
                    "resolved": False,
                    "ambiguous": False,
                    "error": f"Could not resolve '{raw}' because no WhatsApp contact or chat is active.",
                    "reason": "no_context",
                }

            # 5. Substring/distinguishing descriptor against existing search_results ("the work one", "Shafi College")
            if s.search_results:
                q_tokens = [tok for tok in re.findall(r"[a-z0-9]+", stripped_item or stripped) if tok not in {"the", "chat", "contact", "one"}]
                if q_tokens:
                    matches = []
                    for r in s.search_results:
                        hay = f"{r.get('name', '')} {r.get('preview', '')}".casefold()
                        if all(tok in hay for tok in q_tokens):
                            matches.append(r)
                    if len(matches) == 1:
                        return {
                            "success": True,
                            "resolved": True,
                            "contact": matches[0]["name"],
                            "source": "search_results_descriptor",
                            "selected_result": matches[0],
                            "ambiguous": False,
                        }
                    if len(matches) > 1:
                        return {
                            "success": False,
                            "resolved": False,
                            "ambiguous": True,
                            "candidates": matches,
                            "error": f"Multiple search results match '{raw}'.",
                            "clarification_question": self._format_clarification(raw, matches),
                            "reason": "ambiguous_descriptor",
                        }

            return {
                "success": True,
                "resolved": False,
                "contact": raw,
                "source": "literal_query",
                "selected_result": None,
                "ambiguous": False,
            }

    @staticmethod
    def _format_clarification(query: str, results: List[Dict[str, Any]]) -> str:
        items = ", ".join(f"{r['index']}. {r['name']}" for r in results[:6])
        return f"I found {len(results)} matching contacts for '{query}': {items}. Which one would you like?"


_default_whatsapp_state: Optional[WhatsAppStateManager] = None
_state_init_lock = threading.Lock()


def get_whatsapp_state_manager() -> WhatsAppStateManager:
    """Returns the shared WhatsAppStateManager instance."""
    global _default_whatsapp_state
    if _default_whatsapp_state is None:
        with _state_init_lock:
            if _default_whatsapp_state is None:
                _default_whatsapp_state = WhatsAppStateManager()
    return _default_whatsapp_state


def reset_whatsapp_state_manager() -> WhatsAppStateManager:
    """Resets and returns the shared WhatsAppStateManager (for isolated tests)."""
    mgr = get_whatsapp_state_manager()
    mgr.reset()
    return mgr
