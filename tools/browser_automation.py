"""Playwright-based browser automation for TARS with native Brave and Chrome support."""
import os
import atexit
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional

_browser = None
_context = None
_page = None
_playwright = None

# Known Windows Brave browser installation paths
BRAVE_CANDIDATE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
]


def find_brave_executable() -> Optional[str]:
    """Finds the local Brave browser executable on Windows if installed."""
    env_path = os.getenv("BRAVE_PATH") or os.getenv("TARS_BROWSER_EXE")
    if env_path and os.path.exists(env_path):
        return env_path

    for candidate in BRAVE_CANDIDATE_PATHS:
        if os.path.exists(candidate):
            return candidate
    return None


def _ensure_browser(prefer_brave: bool = True) -> Any:
    """
    Initializes or retrieves the active Playwright browser page instance.
    Prefers Brave browser if found, otherwise falls back to Chrome or Chromium.
    """
    global _browser, _context, _page, _playwright

    if _page is not None and not _page.is_closed():
        return _page

    from playwright.sync_api import sync_playwright

    if _playwright is None:
        _playwright = sync_playwright().start()

    brave_exe = find_brave_executable() if prefer_brave else None
    headless = os.getenv("TARS_BROWSER_HEADLESS", "0") in ["1", "true", "True"]

    if brave_exe and os.path.exists(brave_exe):
        try:
            _browser = _playwright.chromium.launch(
                executable_path=brave_exe,
                headless=headless,
                args=["--start-maximized", "--no-default-browser-check"]
            )
        except Exception:
            _browser = None

    if _browser is None:
        try:
            _browser = _playwright.chromium.launch(channel="chrome", headless=headless, args=["--start-maximized"])
        except Exception:
            _browser = _playwright.chromium.launch(headless=headless, args=["--start-maximized"])

    _context = _browser.new_context(viewport=None, no_viewport=True)
    _page = _context.new_page()
    return _page


def browser_navigate(url: str) -> Dict[str, Any]:
    """
    Navigates to a specified URL in the browser and waits for the page to load.
    """
    try:
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = "https://" + clean_url

        page = _ensure_browser()
        page.goto(clean_url, timeout=30000, wait_until="domcontentloaded")
        return {
            "success": True,
            "url": page.url,
            "title": page.title(),
            "message": f"Successfully loaded {page.title()} ({page.url})."
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to navigate to {url}: {str(e)}"}


def browser_search(query: str, engine: str = "google") -> Dict[str, Any]:
    """
    Navigates to a search engine (Google, YouTube, or DuckDuckGo) and searches for the query.
    """
    try:
        page = _ensure_browser()
        clean_q = query.strip()
        encoded = urllib.parse.quote_plus(clean_q)

        eng = engine.strip().lower()
        if eng == "youtube":
            target_url = f"https://www.youtube.com/results?search_query={encoded}"
        elif eng in ["duckduckgo", "ddg"]:
            target_url = f"https://duckduckgo.com/?q={encoded}"
        else:
            target_url = f"https://www.google.com/search?q={encoded}"

        page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        return {
            "success": True,
            "query": clean_q,
            "engine": eng,
            "url": page.url,
            "title": page.title()
        }
    except Exception as e:
        return {"success": False, "error": f"Browser search failed: {str(e)}"}


def browser_click_first_result(intent_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Identifies and clicks the first relevant organic result on a Google or YouTube search page.
    Filters out sponsored/ad results and verifies the resulting page.
    """
    try:
        page = _ensure_browser()
        current_url = page.url.lower()

        # 1. YouTube Search Results Page
        if "youtube.com/results" in current_url:
            # Wait for video renderer
            try:
                page.wait_for_selector("ytd-video-renderer, ytd-rich-item-renderer", timeout=7000)
            except Exception:
                pass

            candidates = page.locator("ytd-video-renderer a#video-title, ytd-video-renderer a#thumbnail")
            count = candidates.count()
            if count > 0:
                for i in range(min(5, count)):
                    candidate = candidates.nth(i)
                    title = candidate.get_attribute("title") or candidate.inner_text()
                    href = candidate.get_attribute("href")
                    if href and "/watch" in href:
                        candidate.click()
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                        return {
                            "success": True,
                            "clicked_title": title.strip() if title else "Video",
                            "url": page.url,
                            "destination_title": page.title(),
                            "message": f"Clicked video '{title}' -> {page.title()}."
                        }

        # 2. Google Search Results Page
        if "google.com" in current_url:
            # Wait for search results container
            try:
                page.wait_for_selector("#search, #rso", timeout=7000)
            except Exception:
                pass

            # If user wants a specific destination (e.g. YouTube result)
            if intent_filter and "youtube" in intent_filter.lower():
                yt_links = page.locator("a[href*='youtube.com']")
                if yt_links.count() > 0:
                    first_yt = yt_links.first
                    title = first_yt.inner_text() or "YouTube Link"
                    first_yt.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    return {
                        "success": True,
                        "clicked": title.strip(),
                        "url": page.url,
                        "title": page.title(),
                        "message": f"Clicked YouTube search result -> {page.title()}."
                    }

            # Find organic result headings (skip ads)
            organic_headings = page.locator("#rso a:has(h3)")
            count = organic_headings.count()
            if count > 0:
                first_result = organic_headings.first
                title = first_result.inner_text()
                first_result.click()
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                return {
                    "success": True,
                    "clicked_title": title.strip(),
                    "url": page.url,
                    "destination_title": page.title(),
                    "message": f"Clicked first organic result '{title}' -> {page.title()}."
                }

        # Generic fallback: click first visible main heading link
        links = page.locator("main a, article a, #content a")
        if links.count() > 0:
            link = links.first
            title = link.inner_text()
            link.click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            return {
                "success": True,
                "clicked_title": title.strip(),
                "url": page.url,
                "destination_title": page.title()
            }

        return {"success": False, "error": "No relevant search result links found on page."}

    except Exception as e:
        return {"success": False, "error": f"Failed to click search result: {str(e)}"}


def browser_find_elements(selector: str, limit: int = 20) -> Dict[str, Any]:
    """
    Finds DOM elements matching a CSS or XPath selector and returns their text and hrefs.
    """
    try:
        page = _ensure_browser()
        loc = page.locator(selector)
        count = min(loc.count(), limit)
        elems = []
        for i in range(count):
            try:
                el = loc.nth(i)
                elems.append({
                    "index": i,
                    "text": el.inner_text(timeout=1000).strip(),
                    "href": el.get_attribute("href")
                })
            except Exception:
                elems.append({"index": i, "text": "", "href": None})
        return {"success": True, "count": count, "elements": elems}
    except Exception as e:
        return {"success": False, "error": str(e)}


def browser_click_element(selector: str, index: int = 0) -> Dict[str, Any]:
    """
    Clicks a DOM element matching the selector at the specified index.
    """
    try:
        page = _ensure_browser()
        loc = page.locator(selector).nth(index)
        loc.click(timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        return {"success": True, "url": page.url, "title": page.title()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def browser_extract_text(selector: Optional[str] = None) -> Dict[str, Any]:
    """
    Extracts readable visible text from the browser's current page.
    """
    try:
        page = _ensure_browser()
        if selector:
            text = page.locator(selector).inner_text(timeout=5000)
        else:
            text = page.inner_text("body")
        return {
            "success": True,
            "text": text[:8000],
            "url": page.url,
            "title": page.title()
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def connect_brave(port: int = 9222) -> Dict[str, Any]:
    """
    Attaches to an existing running Brave instance launched with --remote-debugging-port=9222.
    """
    global _browser, _context, _page, _playwright
    try:
        from playwright.sync_api import sync_playwright
        if _playwright is None:
            _playwright = sync_playwright().start()
        _browser = _playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        contexts = _browser.contexts
        _context = contexts[0] if contexts else _browser.new_context()
        pages = _context.pages
        _page = pages[0] if pages else _context.new_page()
        return {"success": True, "url": _page.url, "title": _page.title()}
    except Exception as e:
        return {"success": False, "error": f"Failed to connect to Brave on port {port}: {str(e)}"}


def browser_close() -> Dict[str, Any]:
    """
    Closes the automated browser session and cleans up resources.
    """
    global _browser, _context, _page, _playwright
    try:
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
        _browser = _context = _page = _playwright = None
        return {"success": True, "message": "Browser session closed."}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Cleanup on exit
atexit.register(browser_close)
