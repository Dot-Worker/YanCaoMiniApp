"""Chromium response-stage guard: no redirects and bounded stream acquisition.

Protocol references (reviewed 2026-10-26):
https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Fetch.pdl
https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/IO.pdl
Uses the browser's original request/response, without exporting cookies, replacing
identity, replaying loader parameters, or synthesizing source-page content.
"""
import base64
from urllib.parse import urlsplit, parse_qs
from .policy import ORIGIN, Pause, PolicyError, BudgetExhausted, assert_public_dns, check_status

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

class BrowserGate:
    def __init__(self, cdp, transport, *, frame_id, target):
        self.cdp, self.transport = cdp, transport
        self.frame_id, self.target = frame_id, target
        self.error = None

    def fail(self, request_id):
        try:
            self.cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
        except Exception:
            pass

    def handle(self, event):
        request_id = event["requestId"]
        if self.error:
            self.fail(request_id)
            return
        try:
            if "responseStatusCode" in event or "responseErrorReason" in event:
                self.response(event)
            else:
                self.request(event)
        except (Pause, PolicyError, BudgetExhausted) as exc:
            self.error = exc
            self.fail(request_id)
        except Exception:
            self.error = Pause("browser_interception_failed")
            self.fail(request_id)

    def request(self, event):
        request_id, request = event["requestId"], event["request"]
        if event["resourceType"] != "Document" or event["frameId"] != self.frame_id or request["method"] != "GET":
            self.fail(request_id)
            return
        parts, target = urlsplit(request["url"]), urlsplit(self.target)
        if parts.scheme != "https" or parts.hostname != "www.yanyue.cn" or parts.port not in (None, 443) or parts.username or parts.password:
            raise Pause("browser_navigation_outside_source")
        # Only a source-generated query key observed in normal JS loading is
        # tolerated transiently; values are never inspected, persisted or replayed.
        if parts.path != target.path or set(parse_qs(parts.query, keep_blank_values=True)) - {"loading"}:
            raise Pause("browser_navigation_outside_target")
        assert_public_dns(parts.hostname)
        self.transport.allowed(request["url"], ORIGIN)
        self.transport.limiter.wait()
        self.transport.budget.before_request()
        self.cdp.send("Fetch.continueRequest", {"requestId": request_id, "interceptResponse": True})

    def response(self, event):
        request_id = event["requestId"]
        if event.get("responseErrorReason"):
            raise Pause("browser_source_response_error")
        status = event["responseStatusCode"]
        headers_list = event.get("responseHeaders", [])
        headers = {h["name"].lower(): h["value"] for h in headers_list}
        # Crucially: reject before continue/fulfill, so browser cannot follow.
        if 300 <= status < 400:
            raise Pause("http_redirect_requires_review")
        check_status(status, headers)
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {"text/html", "application/xhtml+xml"}:
            raise Pause("unexpected_document_mime")
        # The stream supplies chunks rather than one unbounded response.body().
        # Unsupported compression is refused at headers, before allocation.
        if headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
            raise Pause("compressed_browser_document_requires_review")
        available = min(MAX_DOCUMENT_BYTES, self.transport.budget.max_bytes - self.transport.budget.bytes)
        length = headers.get("content-length")
        if length and (not length.isdigit() or int(length) > available):
            raise BudgetExhausted("document_exceeds_byte_budget")
        stream = self.cdp.send("Fetch.takeResponseBodyAsStream", {"requestId": request_id})["stream"]
        body = bytearray()
        try:
            while True:
                self.transport.budget.check_time()
                size = min(65536, available - len(body) + 1)
                chunk = self.cdp.send("IO.read", {"handle": stream, "size": size})
                raw = base64.b64decode(chunk["data"], validate=True) if chunk.get("base64Encoded") else chunk["data"].encode("utf-8")
                if len(raw) > size or len(body) + len(raw) > available:
                    raise BudgetExhausted("document_exceeds_byte_budget")
                body.extend(raw)
                self.transport.budget.add_bytes(len(raw))
                if chunk["eof"]:
                    break
        finally:
            self.cdp.send("IO.close", {"handle": stream})
        # Same original body and response headers, delivered only after bounded
        # acquisition. No response/cookie headers leave this transient call.
        safe_headers = [h for h in headers_list if h["name"].lower() not in {"transfer-encoding", "content-length"}]
        safe_headers.append({"name": "Content-Length", "value": str(len(body))})
        self.cdp.send("Fetch.fulfillRequest", {"requestId": request_id, "responseCode": status, "responseHeaders": safe_headers, "body": base64.b64encode(body).decode("ascii")})
