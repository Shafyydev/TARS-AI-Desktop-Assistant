"""Web navigation, free knowledge retrieval, and search automation tools for TARS."""
import webbrowser
import urllib.parse
import urllib.request
import json
import time
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional, Tuple


def open_url(url: str) -> Dict[str, Any]:
    """
    Opens a URL in the user's default web browser.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        webbrowser.open(url)
        return {"success": True, "message": f"Opened {url} in browser."}
    except Exception as e:
        return {"success": False, "error": f"Failed to open {url}: {str(e)}"}


_KNOWLEDGE_CACHE: Dict[str, Tuple[float, Optional[str]]] = {}
_KNOWLEDGE_TTL = 300.0


def get_free_knowledge(query: str) -> Optional[str]:
    """
    100% free knowledge retrieval using DuckDuckGo Instant Answer and Wikipedia REST API.
    Requires ZERO API keys, has zero rate limits, and returns instant answers.

    Results and misses are cached briefly: both endpoints are network calls, and a
    repeated question must not pay for them again.
    """
    clean_q = query.strip()
    # Strip question preambles
    import re
    clean_q = re.sub(r"^(?:who is|what is|tell me about|explain|where is|search for)\s+", "", clean_q, flags=re.IGNORECASE).strip(" ?.")

    cache_key = clean_q.lower()
    cached = _KNOWLEDGE_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _KNOWLEDGE_TTL:
        return cached[1]

    answer: Optional[str] = None

    # 1. Try DuckDuckGo Instant Answer API
    try:
        ddg_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote_plus(clean_q)}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(ddg_url, headers={"User-Agent": "TARS-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("AbstractText"):
                answer = data["AbstractText"]
            elif data.get("Answer"):
                answer = data["Answer"]
    except Exception:
        pass

    # 2. Try Wikipedia Summary API
    if not answer:
        try:
            wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote_plus(clean_q)}"
            req = urllib.request.Request(wiki_url, headers={"User-Agent": "TARS-Agent/1.0 (contact@tars.local)"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("extract"):
                    answer = data["extract"]
        except Exception:
            pass

    _KNOWLEDGE_CACHE[cache_key] = (time.time(), answer)
    return answer


def search_web(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Searches the web via DuckDuckGo & Wikipedia (100% free, zero cost, no keys required).
    """
    # Check free instant knowledge first
    instant_ans = get_free_knowledge(query)
    if instant_ans:
        return {
            "success": True,
            "query": query,
            "answer": instant_ans,
            "results": [{"title": query, "snippet": instant_ans, "url": "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)}]
        }

    # Fallback to browser search
    encoded_query = urllib.parse.quote_plus(query)
    try:
        webbrowser.open(f"https://www.google.com/search?q={encoded_query}")
        return {
            "success": True,
            "query": query,
            "results": [],
            "message": f"Opened Google search for '{query}' in your default browser."
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to search web: {e}"}


def fetch_webpage_summary(url: str, max_chars: int = 3000) -> Dict[str, Any]:
    """
    Extracts readable text content from a web page without requiring any API keys.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        text = " ".join(soup.stripped_strings)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [Truncated]"

        return {
            "success": True,
            "url": url,
            "title": soup.title.string.strip() if soup.title else "Untitled",
            "content": text
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to fetch content from {url}: {str(e)}"}


def get_weather(location: str = "") -> Dict[str, Any]:
    """
    Fetches current weather conditions for a city/location without needing API keys.

    Args:
        location: Optional city or region name (defaults to IP-detected current location).
    """
    loc = (location or "").strip()
    target = urllib.parse.quote(loc) if loc else ""
    url = f"https://wttr.in/{target}?format=j1"
    try:
        resp = requests.get(url, headers={"User-Agent": "TARS-Agent/1.0"}, timeout=4)
        resp.raise_for_status()
        data = resp.json()
        current = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]
        area_name = ((area.get("areaName") or [{}])[0]).get("value") or loc or "your area"
        country = ((area.get("country") or [{}])[0]).get("value") or ""
        desc = ((current.get("weatherDesc") or [{}])[0]).get("value") or "Clear"
        temp_c = current.get("temp_C", "?")
        feels_c = current.get("FeelsLikeC", temp_c)
        humidity = current.get("humidity", "?")
        wind = current.get("windspeedKmph", "?")
        place = f"{area_name}, {country}".strip(", ")
        return {
            "success": True,
            "location": place,
            "temperature_c": temp_c,
            "feels_like_c": feels_c,
            "condition": desc,
            "humidity_percent": humidity,
            "wind_kmph": wind,
            "message": f"Weather in {place}: {desc}, {temp_c}°C (feels like {feels_c}°C), humidity {humidity}%, wind {wind} km/h.",
        }
    except Exception as e:
        return {"success": False, "error": f"Could not fetch weather telemetry: {e}"}
