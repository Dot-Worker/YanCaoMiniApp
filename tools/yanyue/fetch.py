"""Fresh official browser navigation and bounded public asset fetching.

No persistent browser profile, cookie export/import, stealth, proxy rotation,
manual loading-URL replay, login, challenge solving, or hidden API calls.
"""
from __future__ import annotations
from dataclasses import dataclass
import http.client
import os
from io import BytesIO
import re
import time
from urllib.parse import urlsplit, parse_qs
from .robots import RobotsRules
from .browser_gate import BrowserGate
from .policy import (ORIGIN, ASSET_ORIGIN, USER_AGENT, BudgetExhausted, PolicyError, Pause,
                     assert_public_dns, canonical_url, check_status, classify_document, retry_after_iso)

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024

@dataclass
class Page:
    html: str
    access_method: str = "normal_browser_dom"


class Transport:
    def __init__(self, budget, limiter):
        self.budget, self.limiter = budget, limiter
        self.robots = {}
        self.robots_audit = []
        self.robots_checked = {}

    def request(self, url, *, max_bytes, robots=False):
        parts = urlsplit(url)
        if robots:
            if url not in {ORIGIN + "/robots.txt", ASSET_ORIGIN + "/robots.txt"}:
                raise PolicyError("Unreviewed robots URL")
        else:
            canonical_url(url, asset=True)
        assert_public_dns(parts.hostname)
        self.limiter.wait()
        self.budget.before_request()
        conn = http.client.HTTPSConnection(parts.hostname, timeout=30)
        try:
            conn.request("GET", parts.path, headers={"User-Agent": USER_AGENT, "Accept": "text/plain" if robots else "image/png,image/jpeg,image/webp,image/gif", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            if not (robots and response.status in (404, 410)):
                check_status(response.status, headers)
            if headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                raise PolicyError("Unexpected compressed HTTP asset response")
            declared = headers.get("content-length")
            cap = min(max_bytes, self.budget.max_bytes - self.budget.bytes)
            if declared and (not declared.isdigit() or int(declared) > cap):
                raise BudgetExhausted("response_exceeds_remaining_byte_budget")
            chunks = []
            received = 0
            while received <= cap:
                self.budget.check_time()
                chunk = response.read(min(65536, cap - received + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            body = b"".join(chunks)
            if len(body) > cap:
                raise BudgetExhausted("response_exceeds_remaining_byte_budget")
            self.budget.add_bytes(len(body))
            return response.status, headers, body
        finally:
            conn.close()

    def ensure_robots(self, origin):
        if origin in self.robots and time.monotonic() - self.robots_checked[origin] < 3600:
            return
        status, _, body = self.request(origin + "/robots.txt", max_bytes=65536, robots=True)
        text = body.decode("utf-8", errors="strict") if status == 200 else ""
        if status == 200 and ("<html" in text.lower() or classify_document(text) != "content"):
            raise Pause("robots_not_readable")
        rules = RobotsRules(text)
        self.robots_checked[origin] = time.monotonic()
        self.robots[origin] = rules
        delay = rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 5
        self.limiter.interval = max(self.limiter.interval, float(delay), 5)
        from hashlib import sha256
        self.robots_audit.append({"source_url": origin + "/robots.txt", "status": status, "sha256": sha256(body).hexdigest(), "crawl_delay_applied": self.limiter.interval})

    def allowed(self, url, origin):
        self.ensure_robots(origin)
        if not self.robots[origin].can_fetch(USER_AGENT, url):
            raise Pause("robots_disallow")

    def image(self, url):
        url = canonical_url(url, asset=True)
        self.allowed(url, ASSET_ORIGIN)
        _, headers, body = self.request(url, max_bytes=MAX_IMAGE_BYTES)
        mime = headers.get("content-type", "").split(";", 1)[0].lower().strip()
        details = decode_image(body, mime)
        return body, details


def decode_image(body: bytes, mime: str):
    if len(body) > MAX_IMAGE_BYTES:
        raise PolicyError("Image exceeds fixed resource limit")
    expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}
    if mime not in expected:
        raise PolicyError("Image MIME is not an approved raster type")
    try:
        from PIL import Image
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(body)) as image:
                if image.format != expected[mime] or image.width < 1 or image.height < 1 or image.width * image.height > 16_000_000 or getattr(image, "n_frames", 1) != 1:
                    raise PolicyError("Invalid raster format, dimensions or animation")
                width, height, fmt = image.width, image.height, image.format
                image.verify()
            with Image.open(BytesIO(body)) as image:
                image.load()  # Verify decoding, not only a plausible signature.
    except ImportError:
        raise PolicyError("Pillow is required for image decoding") from None
    except PolicyError:
        raise
    except Exception:
        raise PolicyError("Image decoding failed") from None
    return {"mime_type": mime, "format": fmt, "width": width, "height": height, "byte_count": len(body)}


class BrowserFetcher:
    """Fresh official Chromium with request/response-stage safety interception."""
    def __init__(self, transport):
        self.transport = transport
        self.browser = self.context = self.page = self.manager = self.cdp = self.gate = None

    def __enter__(self):
        if os.environ.get("DEBUG") or os.environ.get("PWDEBUG"):
            raise PolicyError("Browser debug logging must be disabled for source acquisition")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise PolicyError("Install the reviewed optional Playwright dependencies") from None
        self.manager = sync_playwright().start()
        try:
            self.browser = self.manager.chromium.launch(headless=True, ignore_default_args=["--disable-popup-blocking"])
            self.context = self.browser.new_context(service_workers="block", accept_downloads=False)
            self.context.route_web_socket("**/*", lambda socket: self._safe_close(socket))
            self.page = self.context.new_page()
            self.context.on("page", lambda page: self._safe_close(page))
            self.cdp = self.context.new_cdp_session(self.page)
            frame = self.cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            self.gate = BrowserGate(self.cdp, self.transport, frame_id=frame, target=ORIGIN + "/tobacco")
            self.cdp.on("Fetch.requestPaused", self.gate.handle)
            self.cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}], "handleAuthRequests": True})
            self.cdp.on("Fetch.authRequired", lambda e: self._cancel_auth(e))
            # Ask for no compressed response; if the server nevertheless sends
            # compression, response-stage guard pauses before reading its body.
            self.cdp.send("Network.enable")
            self.cdp.send("Network.setAcceptedEncodings", {"encodings": []})
            return self
        except (BudgetExhausted, KeyboardInterrupt):
            self.__exit__(None, None, None)
            raise
        except Exception:
            self.__exit__(None, None, None)
            raise Pause("browser_start_failed_no_fallback_or_escalation") from None

    def _safe_close(self, target):
        try:
            target.close()
        except Exception:
            if self.gate:
                self.gate.error = Pause("blocked_browser_channel_close_failed")

    def _cancel_auth(self, event):
        self.gate.error = Pause("browser_authentication_required")
        try:
            self.cdp.send("Fetch.continueWithAuth", {"requestId": event["requestId"], "authChallengeResponse": {"response": "CancelAuth"}})
        except Exception:
            self.gate.error = Pause("browser_authentication_cancel_failed")

    def __exit__(self, *_):
        for target, method in ((self.context, "close"), (self.browser, "close"), (self.manager, "stop")):
            if target:
                try:
                    getattr(target, method)()
                except Exception:
                    pass

    def fetch(self, url):
        target = canonical_url(url)
        self.gate.target, self.gate.error = target, None
        self.transport.allowed(target, ORIGIN)
        try:
            self.page.goto(target, wait_until="domcontentloaded", timeout=45000)
            started = time.monotonic()
            canonical_reentry_done = False
            while True:
                if self.gate.error:
                    raise self.gate.error
                self.transport.budget.check_time()
                try:
                    html = self.page.content()
                except Exception as exc:
                    if self.gate.error:
                        raise self.gate.error
                    # Only this known DOM-snapshot transition is retryable. Do
                    # not retry navigation, HTTP errors, closed pages or other
                    # failures, and never persist the raw exception text.
                    transition = "Unable to retrieve content because the page is navigating and changing the content."
                    if transition not in str(exc):
                        raise
                    if time.monotonic() - started >= 20:
                        raise Pause("normal_page_navigation_did_not_settle") from None
                    self.page.wait_for_timeout(250)
                    continue
                if len(html.encode()) > MAX_DOCUMENT_BYTES:
                    raise BudgetExhausted("document_exceeds_byte_budget")
                kind = classify_document(html)
                if kind in {"challenge", "login"}:
                    raise Pause("document_" + kind)
                if kind != "loader":
                    if self.page.url != target and not canonical_reentry_done:
                        canonical_reentry_done = True
                        self.page.goto(target, wait_until="domcontentloaded", timeout=45000)
                        continue
                    if self.page.url != target:
                        raise Pause("canonical_page_not_reached")
                    return Page(html)
                if time.monotonic() - started >= 20:
                    raise Pause("normal_loader_did_not_finish")
                self.page.wait_for_timeout(250)
        except (Pause, PolicyError, BudgetExhausted):
            raise
        except Exception:
            if self.gate.error:
                raise self.gate.error
            raise Pause("normal_browser_navigation_failed") from None
