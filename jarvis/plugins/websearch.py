"""Web search without any AI service: the results page in the owner's own browser, else in Jarvis's browser
(Google, then Bing), else DuckDuckGo's plain page. The brain on the laptop reads the results."""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
from html import unescape

from ..tools import ToolRegistry, obj

log = logging.getLogger("jarvis.websearch")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html))).strip()


AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
BLOCKED = re.compile(r"unusual traffic|captcha|не сте робот|необичаен трафик|not a robot|bots use duckduckgo|"
                     r"google\.com/sorry", re.I)
ROBOT = ("asks to prove this is not a robot. Never solve or click such a check, not with the mouse or the keyboard "
         "either: the owner answers it himself if he wants")


def in_owners_browser(query: str, facts: list[str], wait: float = 2.5) -> str:
    """Google's results in the owner's own browser (their profile, the way they search), read off the screen."""
    from .. import browsers, winui

    if not winui.on_windows():
        raise RuntimeError("the owner's browser is read on Windows only")
    name = browsers.open_url("https://www.google.com/search?" + urllib.parse.urlencode({"q": query, "hl": "bg"}), facts)
    time.sleep(wait)  # the new tab loads
    text = winui.read(name.split()[0] if name != "the default browser" else "браузъра")
    if BLOCKED.search(text):
        raise RuntimeError(f"Google in the owner's browser {ROBOT}")
    if len(text) < 200:
        raise RuntimeError("no results page in the owner's browser")
    return "Google results (in the owner's browser; open_page the best one if this is not enough):\n" + text[:5000]


ENGINES = [("Google", "https://www.google.com/search?{}", "hl"), ("Bing", "https://www.bing.com/search?{}", "setlang")]


def in_browser(browser, query: str) -> str:
    """A results page (Google, else Bing) read in Jarvis's own browser (Playwright), the way a person would search."""
    failed = []
    for name, url, lang in ENGINES:
        try:
            return f"{name} results (read in the browser; open_page the best one if this is not enough):\n" + \
                _results(browser, url.format(urllib.parse.urlencode({"q": query, lang: "bg"})))
        except Exception as exc:
            failed.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(failed))


def _results(browser, address: str) -> str:
    def act(page):
        page.goto(address, wait_until="domcontentloaded")
        if "consent." in page.url:  # Google's cookie page in the EU: the privacy-preserving choice, then the results
            for label in ("Отхвърляне на всички", "Reject all"):
                button = page.get_by_role("button", name=label)
                if button.count():
                    button.first.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded")
                    break
        return page.url, page.inner_text("body")

    where, text = browser().do(act, timeout=30)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if BLOCKED.search(where) or BLOCKED.search(text[:3000]):  # a check for robots is never answered: another way
        raise RuntimeError(f"{ROBOT} ({where})")
    if len(text) < 200:
        raise RuntimeError(f"no results page ({where}: {text[:120]!r})")
    return text[:5000]


def duckduckgo(query: str, fetch=None, limit: int = 8) -> str:
    """The first results of a DuckDuckGo search (no key, no AI): title, address and the snippet of each."""
    address = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    if fetch is None:
        request = urllib.request.Request(address, headers={"User-Agent": AGENT, "Accept-Language": "bg,en;q=0.8"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    else:
        page = fetch(address, timeout=10)
    found = []
    for block in re.split(r'class="result__a"', page)[1:limit + 1]:
        link = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        url = unescape(link.group(1))
        url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("uddg", [url])[0]  # DuckDuckGo's redirect
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        found.append(f"- {_plain(link.group(2))} ({url}): {_plain(snippet.group(1)) if snippet else ''}")
    if not found:
        said = _plain(page)
        raise RuntimeError(f"DuckDuckGo {ROBOT}." if BLOCKED.search(said) else f"DuckDuckGo returned no results ({said[:120]!r}).")
    return "Results (DuckDuckGo; open_page the best one if the snippets are not enough):\n" + "\n".join(found)


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Search the web with Google for current facts: news, prices, opening hours, weather, people, "
        "products. Returns the results page (titles, links, snippets); open_page the best one for more.",
        obj({"query": ("string", "What to search for, in any language")}),
    )
    def google_search(query: str):
        try:  # the owner's own browser first: it searches as they do
            return in_owners_browser(query, [r["fact"] for r in ctx.store.query("SELECT fact FROM facts")])
        except Exception as exc:
            log.warning("Google in the owner's browser failed (%s: %s)", type(exc).__name__, str(exc)[:200])
        browser = getattr(ctx, "browser", None)
        if browser is not None:
            try:
                return in_browser(browser, query)
            except Exception as exc:
                log.warning("Google in the browser failed (%s: %s); DuckDuckGo instead", type(exc).__name__,
                            str(exc)[:200])
        return duckduckgo(query)
