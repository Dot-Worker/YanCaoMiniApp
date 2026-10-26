"""Offline DOM/response compatibility only. No browser or source connection."""
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from yanyue.browser_gate import BrowserGate
from yanyue.fetch import BrowserFetcher
from yanyue.policy import ORIGIN, Pause, canonical_url, classify_document
from test_transport import FakeCDP, FakeTransport

FIX=Path(__file__).parent/'fixtures'
TRANSITION='Unable to retrieve content because the page is navigating and changing the content.'
HEADER='<header><form><input name="account"><input name="password" type="password"></form></header>'

class MockPage:
    def __init__(self, values, clock=None):
        self.values=deque(values);self.url=ORIGIN+'/tobacco';self.gotos=0;self.waits=0;self.clock=clock
    def goto(self,*args,**kwargs):self.gotos+=1
    def content(self):
        value=self.values.popleft() if len(self.values)>1 else self.values[0]
        if callable(value):value=value()
        if isinstance(value,Exception):raise value
        return value
    def wait_for_timeout(self,ms):
        self.waits+=1
        if self.clock is not None:self.clock[0]+=ms/1000

def browser_for(page):
    browser=BrowserFetcher(SimpleNamespace(allowed=lambda *args:None,budget=SimpleNamespace(check_time=lambda:None)))
    browser.page=page;browser.gate=SimpleNamespace(target=ORIGIN+'/tobacco',error=None)
    return browser

class CompatibilityTests(unittest.TestCase):
    def test_observed_loader_visible_words_do_not_become_product_content(self):
        fixture=FIX/'loader-visible-sanitized.html'
        self.assertEqual(classify_document(fixture.read_text()),'loader')
    def test_public_header_login_widgets_are_not_login_walls(self):
        for name in ['index.html','brand-13.html','product-442.html','gallery-442-structure.html']:
            with self.subTest(fixture=name):
                self.assertEqual(classify_document(HEADER+(FIX/name).read_text()),'content')
    def test_empty_or_hidden_public_markers_do_not_bypass_login_pause(self):
        examples=['<div id="brands"></div>', '<div id="basicinfo"></div>', '<div id="zine_content"><h3><span class="d-block">name</span></h3><div id="basicinfo"></div></div>', '<div hidden>'+(FIX/'brand-13.html').read_text()+'</div>']
        for html in examples:
            with self.subTest(html=html[:40]):self.assertEqual(classify_document(HEADER+html),'login')
    def test_visible_challenge_wins_over_public_content(self):
        html=(FIX/'brand-13.html').read_text()+'<p>人机验证：请输入验证码</p>'
        self.assertEqual(classify_document(html),'challenge')
    def test_script_style_identifiers_do_not_masquerade_as_visible_challenge(self):
        html='<html><script>const captchaEnabled=false;</script><style>.captcha{color:red}</style><p>ordinary text</p></html>'
        self.assertEqual(classify_document(html),'content')
    def test_mime_whitespace_is_accepted_without_changing_original_body(self):
        body='<html>原文</html>'.encode();import base64
        cdp=FakeCDP([{'data':base64.b64encode(body).decode(),'base64Encoded':True,'eof':True}]);gate=BrowserGate(cdp,FakeTransport(),frame_id='main',target=ORIGIN+'/tobacco')
        gate.handle({'requestId':'r','responseStatusCode':200,'responseHeaders':[{'name':'Content-Type','value':'Text/Html ; charset=utf-8'}]})
        self.assertIsNone(gate.error)
        self.assertEqual(base64.b64decode(cdp.calls[-1][1]['body']),body)
    def test_http403_stops_before_any_body_or_js_processing(self):
        cdp=FakeCDP();gate=BrowserGate(cdp,FakeTransport(),frame_id='main',target=ORIGIN+'/tobacco')
        gate.handle({'requestId':'r','responseStatusCode':403,'responseHeaders':[{'name':'Content-Type','value':'text/html ; charset=utf-8'}]})
        self.assertEqual(gate.error.reason,'source_http_403')
        self.assertEqual([c[0] for c in cdp.calls],['Fetch.failRequest'])
    def test_same_navigation_dom_transition_is_waited_not_retried(self):
        page=MockPage([(FIX/'loader.html').read_text(),RuntimeError(TRANSITION),'<p>ready</p>']);browser=browser_for(page)
        self.assertIn('ready',browser.fetch(ORIGIN+'/tobacco').html)
        self.assertEqual(page.gotos,1);self.assertEqual(page.waits,2)
    def test_repeated_dom_transition_has_twenty_second_deadline(self):
        clock=[0.0];page=MockPage([RuntimeError(TRANSITION)],clock);browser=browser_for(page)
        with patch('yanyue.fetch.time.monotonic',side_effect=lambda:clock[0]),self.assertRaises(Pause) as caught:browser.fetch(ORIGIN+'/tobacco')
        self.assertEqual(caught.exception.reason,'normal_page_navigation_did_not_settle')
        self.assertEqual(page.gotos,1);self.assertLessEqual(clock[0],20)
    def test_unrelated_content_error_is_not_retried(self):
        page=MockPage([RuntimeError('Target page has been closed')]);browser=browser_for(page)
        with self.assertRaises(Pause) as caught:browser.fetch(ORIGIN+'/tobacco')
        self.assertEqual(caught.exception.reason,'normal_browser_navigation_failed')
        self.assertEqual(page.gotos,1);self.assertEqual(page.waits,0)
    def test_http_pause_during_transition_is_not_swallowed(self):
        page=MockPage([None]);browser=browser_for(page)
        def fail():browser.gate.error=Pause('source_http_403');return RuntimeError(TRANSITION)
        page.values=deque([fail])
        with self.assertRaises(Pause) as caught:browser.fetch(ORIGIN+'/tobacco')
        self.assertEqual(caught.exception.reason,'source_http_403')
        self.assertEqual(page.waits,0);self.assertEqual(page.gotos,1)
    def test_gallery_classification_does_not_expand_phase_one_route_scope(self):
        from yanyue.policy import PolicyError
        with self.assertRaises(PolicyError):canonical_url('/gallery/442')

if __name__=='__main__':unittest.main()
