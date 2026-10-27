"""Bridge the native client's offline checks into existing push/PR Python CI.

Node is required, never silently skipped. No browser, build service, account,
source request or WeChat deployment is started by these checks.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

REPO_ROOT=Path(__file__).resolve().parents[2]
ROOT=REPO_ROOT/'WeChatAPP'

class NativePreviewCI(unittest.TestCase):
    def run_check(self, command):
        result=subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},
                              capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        return result.stdout+'\n'+result.stderr
    def node(self):
        binary=shutil.which('node')
        self.assertIsNotNone(binary,'Node.js is required for offline native-preview checks; install Node before running this suite.')
        return binary
    def test_project_root_is_only_wechatapp(self):
        import json
        self.assertFalse((REPO_ROOT/'project.config.json').exists(), 'Do not import the collector repository root as a mini program')
        self.assertFalse((REPO_ROOT/'miniprogram').exists(), 'Native client must live under WeChatAPP')
        self.assertTrue((ROOT/'project.config.json').is_file())
        self.assertEqual(json.loads((ROOT/'project.config.json').read_text())['miniprogramRoot'], 'miniprogram/')
    def test_native_node_behavior_suite(self):
        files=sorted((ROOT/'preview-tests').glob('*.test.js'))
        self.assertTrue(files,'Native preview behavior tests are missing')
        output=self.run_check([self.node(),'--test','--test-reporter=tap',*(str(f) for f in files)])
        self.assertIn('# fail 0',output)
    def test_native_python_exporter_suite(self):
        self.assertTrue(list((ROOT/'preview-tests').glob('test_*.py')), 'Native exporter tests are missing')
        output=self.run_check([sys.executable,'-m','unittest','discover','-s','preview-tests','-p','test_*.py','-v'])
        self.assertRegex(output, r'Ran [1-9][0-9]* tests? in ', 'Native exporter suite must execute tests')
    def test_native_structure_assets_and_style_guards(self):
        self.node()
        self.run_check([sys.executable,'preview-tests/validate_static.py'])

if __name__=='__main__':unittest.main()
