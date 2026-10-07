"""A real web browser Jarvis drives itself: sites, logins, forms, shopping carts, downloads.

Uses Playwright with a persistent profile in ~/.jarvis/browser, so anything you log into once
stays logged in. The window is visible, so you can watch or step in (e.g. for a captcha).
"""

from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import Future

from ..tools import Image, ToolRegistry, obj

ELEMENTS_JS = """
() => {
  const out = [];
  const els = document.querySelectorAll('a, button, input, textarea, select, [role=button], [role=link], [contenteditable=true]');
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.bottom < 0 || r.top > innerHeight) continue;
    const label = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.name || '').trim().slice(0, 80);
    let sel = el.id ? '#' + CSS.escape(el.id) : (el.name ? `${el.tagName.toLowerCase()}[name="${el.name}"]` : null);
    out.push({tag: el.tagName.toLowerCase(), type: el.type || null, label, selector: sel});
    if (out.length >= 150) break;
  }
  return out;
}
"""


class BrowserWorker:
    """Playwright's sync API must stay on one thread; every call is shipped to this worker."""

    def __init__(self, profile_dir: str):
        self.profile_dir = profile_dir
        self.jobs: queue.Queue = queue.Queue()
        self.page = None
        threading.Thread(target=self._run, name="jarvis-browser", daemon=True).start()

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        pw = None
        context = None
        while True:
            fn, fut = self.jobs.get()
            try:
                if context is None:
                    pw = pw or sync_playwright().start()
                    try:
                        context = pw.chromium.launch_persistent_context(
                            self.profile_dir,
                            headless=os.environ.get("JARVIS_BROWSER_HEADLESS") == "1",
                            executable_path=os.environ.get("JARVIS_BROWSER_PATH") or None,
                            viewport={"width": 1280, "height": 800},
                        )
                    except Exception as exc:
                        raise RuntimeError(f"Browser failed to start; run `playwright install chromium`. ({exc})") from exc
                    self.page = context.pages[0] if context.pages else context.new_page()
                    context.on("page", lambda p: setattr(self, "page", p))  # follow new tabs
                fut.set_result(fn(self.page))
            except Exception as exc:
                fut.set_exception(exc)

    def do(self, fn, timeout: float = 120):
        fut: Future = Future()
        self.jobs.put((fn, fut))
        return fut.result(timeout=timeout)


def register(registry: ToolRegistry, ctx) -> None:
    worker: dict = {}

    def browser() -> BrowserWorker:
        if "w" not in worker:
            worker["w"] = BrowserWorker(str(ctx.settings.home / "browser"))
        return worker["w"]

    @registry.tool(
        "Drive a real web browser (stays logged in between sessions). Actions:\n"
        "goto(url) · read (visible text of the page) · elements (clickable things with selectors) · "
        "look (screenshot you can see) · click(selector or text) · fill(selector, text) · press(key, e.g. Enter) · "
        "select(selector, text=option) · scroll(amount, + down) · back · wait(seconds).\n"
        "Before anything irreversible in the browser (paying, ordering, posting, sending a message), "
        "call request_approval first.",
        obj({
            "action": ("string", "goto | read | elements | look | click | fill | press | select | scroll | back | wait"),
            "url?": ("string", "For goto"),
            "selector?": ("string", "CSS selector (from elements) for click/fill/select"),
            "text?": ("string", "Text to click on, text to fill, or option to select"),
            "key?": ("string", "Key for press"),
            "amount?": ("integer", "Scroll pixels (default 600)"),
        }),
    )
    def web_browser(action: str, url: str | None = None, selector: str | None = None, text: str | None = None,
                    key: str | None = None, amount: int = 600):
        def act(page):
            if action == "goto":
                page.goto(url if url and "://" in url else f"https://{url}", wait_until="domcontentloaded")
                return f"Opened {page.url} — {page.title()}"
            if action == "read":
                body = page.inner_text("body")
                return f"{page.title()} ({page.url})\n\n{body[:15000]}"
            if action == "elements":
                return page.evaluate(ELEMENTS_JS)
            if action == "look":
                return Image(page.screenshot(), "image/png", f"{page.title()} ({page.url})")
            if action == "click":
                target = page.locator(selector) if selector else page.get_by_text(text or "", exact=False)
                target.first.click(timeout=10000)
                page.wait_for_load_state("domcontentloaded")
                return f"Clicked. Now at {page.url}"
            if action == "fill":
                page.locator(selector).first.fill(text or "", timeout=10000)
                return "Filled."
            if action == "select":
                page.locator(selector).first.select_option(label=text, timeout=10000)
                return "Selected."
            if action == "press":
                page.keyboard.press(key or "Enter")
                page.wait_for_load_state("domcontentloaded")
                return f"Pressed {key or 'Enter'}. Now at {page.url}"
            if action == "scroll":
                page.mouse.wheel(0, amount)
                return "Scrolled."
            if action == "back":
                page.go_back()
                return f"Back at {page.url}"
            if action == "wait":
                page.wait_for_timeout(int((amount if amount != 600 else 2) * 1000))
                return "Waited."
            raise ValueError(f"Unknown action {action}")

        return browser().do(act)
