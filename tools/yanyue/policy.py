"""Fail-closed URL, response, budget and single-worker request policy."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import math
import re
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

ORIGIN = "https://www.yanyue.cn"
ASSET_ORIGIN = "https://res.yanyue.cn"
USER_AGENT = "YanCaoFacts/0.1 (source-attributed public fact catalog; no commerce)"
# Pagination is not guessed. Expand only after a real clean link is observed.
PAGE_RE = re.compile(r"^/(?:tobacco|sort/[1-9][0-9]*|sort/sort/sortid/[1-9][0-9]*/p/[1-9][0-9]*|product/[1-9][0-9]*)/?$")
ASSET_RE = re.compile(r"^/thumb/(?:genpic|[1-9][0-9]{0,4}x[1-9][0-9]{0,4})/[a-f0-9]{3}/[a-f0-9]{3}/[a-f0-9]{32}\.(?:png|jpe?g|webp|gif)$", re.I)
SECRET_WORDS = re.compile(r"(?:session|cookie|loading|token|secret|signature|auth|password)", re.I)

class PolicyError(ValueError):
    pass

class Pause(RuntimeError):
    def __init__(self, reason: str, *, retry_after: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after

class BudgetExhausted(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_url(url: str, base: str = ORIGIN, *, asset: bool = False) -> str:
    """Reject secrets rather than silently making a session URL replayable."""
    absolute = urljoin(base, url)
    p = urlsplit(absolute)
    host = "res.yanyue.cn" if asset else "www.yanyue.cn"
    if p.scheme != "https" or p.hostname != host or p.username or p.password:
        raise PolicyError("URL scheme or host is outside the reviewed allowlist")
    if p.port not in (None, 443) or p.query or p.fragment or "%" in p.path or ".." in p.path:
        raise PolicyError("URL contains unreviewed parameters, encoding or traversal")
    if SECRET_WORDS.search(p.path) or not (ASSET_RE if asset else PAGE_RE).fullmatch(p.path):
        raise PolicyError("URL path is outside the observed catalog/asset contract")
    path = p.path.rstrip("/")
    # The observed first-page option is the same source page, not a second job.
    path = re.sub(r"^/sort/sort/sortid/([1-9][0-9]*)/p/1$", r"/sort/\1", path)
    return urlunsplit(("https", host, path, "", ""))


def assert_public_dns(host: str) -> None:
    """Host allowlisting plus public IP check; no arbitrary/private destination."""
    if host not in {"www.yanyue.cn", "res.yanyue.cn"}:
        raise PolicyError("Unreviewed destination")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise PolicyError("Destination DNS did not resolve exclusively to public addresses")


def retry_after_iso(raw: str | None, now: float | None = None) -> str | None:
    if not raw:
        return None
    now = time.time() if now is None else now
    try:
        stamp = now + max(0, int(raw)) if raw.strip().isdigit() else parsedate_to_datetime(raw).timestamp()
        return datetime.fromtimestamp(max(now, stamp), timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return None


def check_status(status: int, headers: dict[str, str]) -> None:
    lower = {k.lower(): v for k, v in headers.items()}
    if status in (401, 403, 429):
        raise Pause(f"source_http_{status}", retry_after=retry_after_iso(lower.get("retry-after")))
    if status >= 500:
        raise Pause(f"source_http_{status}", retry_after=retry_after_iso(lower.get("retry-after")))
    if status != 200:
        raise PolicyError(f"Unexpected HTTP status {status}; no automatic retry")


def classify_document(html: str) -> str:
    """Only classify obvious interstitials, never solve them."""
    text = re.sub(r"<[^>]+>", " ", html).lower()
    if any(s in text for s in ("captcha", "verify you are human", "access denied", "安全验证", "人机验证", "验证码")):
        return "challenge"
    # Login links are normal page furniture; only a login-form-only document is a boundary.
    if re.search(r'<input\b[^>]*type=[\"\']?password', html, re.I) and not re.search(r'id=[\"\']basicinfo[\"\']', html, re.I):
        return "login"
    if len(html) < 12000 and any(s in text for s in ("enable javascript", "启用javascript", "启用 javascript", "javascript is required", "正在加载")):
        return "loader"
    return "content"


@dataclass
class Budget:
    max_requests: int = 12
    max_bytes: int = 20 * 1024 * 1024
    max_seconds: int = 300
    requests: int = 0
    bytes: int = 0
    started: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_requests <= 1000 or not 1024 <= self.max_bytes <= 512 * 1024 * 1024 or not 5 <= self.max_seconds <= 7200:
            raise PolicyError("Run budgets exceed hard limits")
        self.started = time.monotonic()

    def check_time(self) -> None:
        if time.monotonic() - self.started >= self.max_seconds:
            raise BudgetExhausted("hard_wall_time_budget_reached")

    def before_request(self) -> None:
        if self.requests >= self.max_requests or self.bytes >= self.max_bytes or time.monotonic() - self.started >= self.max_seconds:
            raise BudgetExhausted("hard_run_budget_reached")
        self.requests += 1

    def add_bytes(self, count: int) -> None:
        if count < 0 or self.bytes + count > self.max_bytes:
            raise BudgetExhausted("hard_byte_budget_reached")
        self.bytes += count


class RateLimiter:
    def __init__(self, interval: float = 5.0, *, clock=time.monotonic, sleeper=time.sleep):
        if not math.isfinite(interval) or interval < 5:
            raise PolicyError("Minimum source request interval is five seconds")
        self.interval, self.clock, self.sleeper = interval, clock, sleeper
        self.last: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self.last is not None:
            self.sleeper(max(0, self.interval - (now - self.last)))
        self.last = self.clock()


from contextlib import contextmanager
import signal

@contextmanager
def walltime_deadline(seconds):
    """POSIX CI/executor guard, including blocking network/body operations."""
    if not hasattr(signal, "setitimer"):
        raise PolicyError("This collector requires the POSIX hard deadline guard")
    def expired(signum, frame):
        raise BudgetExhausted("hard_wall_time_budget_reached")
    previous_handler = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
