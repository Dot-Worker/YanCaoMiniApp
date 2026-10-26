import copy
import json
from pathlib import Path
import tempfile
import unittest
from yanyue.checkpoint import export_checkpoint, read_checkpoint, restore_checkpoint
from yanyue.parser import parse_page
from yanyue.policy import PolicyError
from yanyue.store import Store

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.s=Store(self.root/'state')
        url='https://www.yanyue.cn/product/442'
        self.s.enqueue({'kind':'product','url':url})
        self.s.save_page(parse_page((Path(__file__).parent/'fixtures/product-442.html').read_text(),url=url))
        export_checkpoint(self.s,self.root/'out')
        self.good=json.loads((self.root/'out/checkpoint.json').read_text())
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def test_roundtrip_preserves_hash_without_browser_state(self):
        other=Store(self.root/'restored')
        try:
            restore_checkpoint(other,self.root/'out')
            self.assertEqual(other.coverage()['products_parsed'],1)
            self.assertIsNone(other.next())
            self.assertEqual(len(other.db.execute('SELECT source_content_sha256 FROM queue').fetchone()[0]),64)
            self.assertFalse(self.good['images_included'])
        finally:other.close()
    def test_bad_fields_and_identities(self):
        for change in [lambda x:x['products'][0].update(id='yanyue:999'),lambda x:x['products'][0]['measurements']['tar_mg'].update(value='11mg'),lambda x:x['products'][0]['barcodes'][0].update(value=12345),lambda x:x.update(products=[]),lambda x:x.update(cookie='bad'),lambda x:x['queue'][0].update(url=x['queue'][0]['url']+'/')]:
            value=copy.deepcopy(self.good);change(value);path=self.root/'bad.json';path.write_text(json.dumps(value))
            with self.assertRaises(PolicyError):read_checkpoint(path)
    def test_nonfinite_and_symlink(self):
        value=copy.deepcopy(self.good);value['products'][0]['measurements']['tar_mg']['value']=float('nan')
        path=self.root/'bad.json';path.write_text(json.dumps(value))
        with self.assertRaises(PolicyError):read_checkpoint(path)
        link=self.root/'link.json';link.symlink_to(self.root/'out/checkpoint.json')
        with self.assertRaises(PolicyError):read_checkpoint(link)

if __name__=='__main__':unittest.main()
