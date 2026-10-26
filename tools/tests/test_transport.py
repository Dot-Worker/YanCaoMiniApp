import base64
from io import BytesIO
import time
import unittest
from unittest.mock import patch
from yanyue.browser_gate import BrowserGate
from yanyue.fetch import Transport, decode_image
from yanyue.policy import Budget, BudgetExhausted, Pause, RateLimiter, PolicyError, walltime_deadline
from yanyue.robots import RobotsRules

class FakeCDP:
    def __init__(self, chunks=()): self.calls, self.chunks = [], list(chunks)
    def send(self, command, params=None):
        self.calls.append((command, params))
        if command == "Fetch.takeResponseBodyAsStream": return {"stream": "teststream"}
        if command == "IO.read": return self.chunks.pop(0)
        return {}

class FakeTransport:
    def __init__(self):
        self.budget = Budget(5, 1024, 30)
        self.limiter = RateLimiter(5, clock=lambda: 0, sleeper=lambda n: None)
        self.allowed_calls = []
    def allowed(self, url, origin): self.allowed_calls.append((url, origin))

class BrowserGateTests(unittest.TestCase):
    def gate(self, chunks=()):
        cdp, transport = FakeCDP(chunks), FakeTransport()
        return BrowserGate(cdp, transport, frame_id="main", target="https://www.yanyue.cn/tobacco"), cdp, transport
    def event(self, **extra):
        return dict({"requestId":"r1", "resourceType":"Document", "frameId":"main", "request":{"url":"https://www.yanyue.cn/tobacco", "method":"GET"}}, **extra)
    def test_http_redirect_rejected_before_follow_or_body(self):
        gate, cdp, transport = self.gate()
        gate.handle(self.event(responseStatusCode=302,responseHeaders=[{"name":"Location","value":"https://private.invalid/"}]))
        self.assertIsInstance(gate.error, Pause)
        self.assertEqual([x[0] for x in cdp.calls], ["Fetch.failRequest"])
    def test_non_document_and_private_navigation_never_sent(self):
        gate, cdp, transport = self.gate()
        gate.handle(self.event(resourceType="Image"))
        gate.handle(self.event(request={"url":"http://127.0.0.1/", "method":"GET"}))
        self.assertFalse(any(c[0] == "Fetch.continueRequest" for c in cdp.calls))
        self.assertEqual(transport.budget.requests, 0)
    def test_allowed_document_counted_and_response_intercepted(self):
        gate, cdp, transport = self.gate()
        with patch("yanyue.browser_gate.assert_public_dns"):
            gate.handle(self.event())
        self.assertEqual(transport.budget.requests, 1)
        self.assertEqual(cdp.calls[-1], ("Fetch.continueRequest", {"requestId":"r1","interceptResponse":True}))
    def test_unknown_length_stream_bounded(self):
        gate, cdp, transport = self.gate([{"data":"x"*1024,"eof":False},{"data":"x","eof":True}])
        gate.handle(self.event(responseStatusCode=200,responseHeaders=[{"name":"Content-Type","value":"text/html"}]))
        self.assertIsInstance(gate.error, BudgetExhausted)
        self.assertFalse(any(c[0] == "Fetch.fulfillRequest" for c in cdp.calls))
        self.assertLessEqual(transport.budget.bytes, 1024)
        self.assertTrue(any(c[0] == "IO.close" for c in cdp.calls))
    def test_compressed_response_rejected_before_body(self):
        gate, cdp, transport = self.gate()
        gate.handle(self.event(responseStatusCode=200,responseHeaders=[{"name":"Content-Type","value":"text/html"},{"name":"Content-Encoding","value":"gzip"},{"name":"Content-Length","value":"3"}]))
        self.assertIsInstance(gate.error, Pause)
        self.assertEqual([c[0] for c in cdp.calls], ["Fetch.failRequest"])
    def test_success_original_body_and_no_header_persistence(self):
        raw=b"<html>test</html>"
        gate, cdp, transport=self.gate([{"data":base64.b64encode(raw).decode(),"base64Encoded":True,"eof":True}])
        gate.handle(self.event(responseStatusCode=200,responseHeaders=[{"name":"Content-Type","value":"text/html"}]))
        self.assertIsNone(gate.error)
        self.assertEqual(base64.b64decode(cdp.calls[-1][1]["body"]), raw)
        self.assertEqual(transport.budget.bytes, len(raw))
    def test_status_pause_preserves_retry_after(self):
        gate, _, _ = self.gate()
        gate.handle(self.event(responseStatusCode=429,responseHeaders=[{"name":"Retry-After","value":"60"}]))
        self.assertEqual(gate.error.reason,"source_http_429")
        self.assertIsNotNone(gate.error.retry_after)

class PolicyTests(unittest.TestCase):
    def test_finite_minimum_interval(self):
        for n in [0,4.99,float('nan'),float('inf')]:
            with self.assertRaises(PolicyError): RateLimiter(n)
    def test_wall_deadline_interrupts_blocking_operation(self):
        started=time.monotonic()
        with self.assertRaises(BudgetExhausted), walltime_deadline(0.02): time.sleep(0.2)
        self.assertLess(time.monotonic()-started,0.15)
    def test_unsupported_robots_fail_closed(self):
        for path in ["/product/*","/product/442$"]:
            with self.assertRaises(Pause): RobotsRules(f"User-agent: *\nDisallow: {path}")
    def test_robots_fractional_and_longest_prefix(self):
        rules=RobotsRules("User-agent: *\nDisallow: /product/\nAllow: /product/442\nCrawl-delay: 5.5")
        self.assertEqual(rules.crawl_delay('*'),5.5)
        self.assertFalse(rules.can_fetch('*','https://www.yanyue.cn/product/443'))
        self.assertTrue(rules.can_fetch('*','https://www.yanyue.cn/product/442'))
    def test_image_decode_mime_and_animation(self):
        from PIL import Image
        buf=BytesIO();Image.new('RGB',(4,4)).save(buf,format='PNG')
        self.assertEqual(decode_image(buf.getvalue(),'image/png')['width'],4)
        with self.assertRaises(PolicyError):decode_image(buf.getvalue(),'image/jpeg')
        with self.assertRaises(PolicyError):decode_image(b'<html>bad</html>','text/html')

if __name__ == '__main__': unittest.main()
