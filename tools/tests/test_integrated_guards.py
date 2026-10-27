"""Offline integration guards; no real runtime state or private review records."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from yanyue import cli, reviewed_images
from yanyue.policy import PolicyError
from yanyue.store import Store

class IntegratedGuards(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'state')
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def test_shared_store_lock_rejects_symlink_without_creating_target(self):
        target=self.root/'outside';(self.store.root/'worker.lock').symlink_to(target)
        with self.assertRaises(PolicyError):
            with self.store.exclusive():pass
        self.assertFalse(target.exists())
    def test_shared_store_lock_rejects_hardlink_without_modification(self):
        target=self.root/'outside';target.write_bytes(b'unchanged')
        os.link(target,self.store.root/'worker.lock')
        with self.assertRaises(PolicyError):
            with self.store.exclusive():pass
        self.assertEqual(target.read_bytes(),b'unchanged')
    def test_shared_store_lock_rejects_fifo_without_blocking(self):
        os.mkfifo(self.store.root/'worker.lock')
        with self.assertRaises(PolicyError):
            with self.store.exclusive():pass
    def test_new_and_legacy_lock_entrypoints_exclude_each_other(self):
        other=Store(self.store.root)
        try:
            with self.store.exclusive():
                with self.assertRaises(PolicyError):
                    with reviewed_images.exclusive(other):pass
            with reviewed_images.exclusive(other):
                with self.assertRaises(PolicyError):
                    with self.store.exclusive():pass
        finally:other.close()
    def test_snapshot_cli_refuses_unsafe_lock_before_import(self):
        target=self.root/'outside';(self.store.root/'worker.lock').symlink_to(target)
        fixture=Path(__file__).parent/'fixtures/browser-product-3358.json'
        output=StringIO()
        with redirect_stdout(output):
            code=cli.main(['import-browser-snapshot','--state-dir',str(self.store.root),'--input',str(fixture)])
        self.assertEqual(code,2)
        self.assertFalse(target.exists())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM products').fetchone()[0],0)
    def test_public_policy_cannot_masquerade_as_private_import_manifest(self):
        public=Path(reviewed_images.__file__).resolve().parents[2]/'data/reviewed-image-profile-product610.json'
        with self.assertRaises(PolicyError):reviewed_images.profile(public.read_bytes())
        with self.assertRaises(PolicyError):reviewed_images.profile(b'{"synthetic":true}')
    def test_public_profile_contains_only_machine_policy(self):
        policy=reviewed_images.profile()
        self.assertEqual(policy['schema_version'],'reviewed_image_public_policy_1')
        self.assertEqual(len(policy['facts']),9)
        self.assertEqual(len(policy['assets']),9)
        text=json.dumps(policy)
        for forbidden in ('candidate_status','pixel_review_method','raw_json','reviewed82_checkpoint','/workspace/','/home/','metadata_file'):
            self.assertNotIn(forbidden,text)

if __name__=='__main__':unittest.main()
