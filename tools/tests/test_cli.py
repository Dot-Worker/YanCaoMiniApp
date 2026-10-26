from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from yanyue.cli import main
from yanyue.fetch import BrowserFetcher, Transport
from yanyue.policy import Pause

class CLITests(unittest.TestCase):
    def test_default_plan_never_constructs_network_transport(self):
        with tempfile.TemporaryDirectory() as root, patch('yanyue.cli.Transport', side_effect=AssertionError('network constructed')), redirect_stdout(StringIO()) as output:
            code=main(['crawl','--state-dir',root])
        self.assertEqual(code,0)
        self.assertFalse(json.loads(output.getvalue())['network_executed'])
    def test_unexpected_initial_error_redacted_and_durably_paused(self):
        secret='SYNTHETIC_PRIVATE_QUERY_MUST_NOT_LEAK'
        with tempfile.TemporaryDirectory() as root, patch.object(Transport,'ensure_robots',side_effect=OSError(secret)), redirect_stdout(StringIO()) as output:
            code=main(['crawl','--state-dir',root,'--execute'])
            payload=output.getvalue()
            self.assertTrue((Path(root)/'coverage.json').exists())
        self.assertEqual(code,2)
        self.assertNotIn(secret,payload)
        self.assertEqual(json.loads(payload)['coverage']['pause']['reason'],'unexpected_source_operation_error')
    def test_source_pause_is_nonzero_and_not_a_success_claim(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Transport,'ensure_robots',side_effect=Pause('source_http_403')), redirect_stdout(StringIO()) as output:
            code=main(['crawl','--state-dir',root,'--execute'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.getvalue())['stop_reason'],'source_http_403')
    def test_async_close_and_auth_errors_do_not_escape(self):
        class Broken:
            def close(self): raise RuntimeError('SYNTHETIC_PRIVATE_QUERY_MUST_NOT_LEAK')
            def send(self,*args): raise RuntimeError('SYNTHETIC_PRIVATE_QUERY_MUST_NOT_LEAK')
        class Gate: error=None
        browser=BrowserFetcher(None);browser.gate=Gate();browser.cdp=Broken()
        browser._safe_close(Broken())
        self.assertEqual(browser.gate.error.reason,'blocked_browser_channel_close_failed')
        browser._cancel_auth({'requestId':'test'})
        self.assertEqual(browser.gate.error.reason,'browser_authentication_cancel_failed')

if __name__=='__main__':unittest.main()
