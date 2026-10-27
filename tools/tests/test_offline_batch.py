from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from offline_batch import import_batch as batch

API=batch.adapter_api()
importer, Store, atomic_json, PolicyError, utcnow=API
FIXTURES=batch.ADAPTER/'tools/tests/fixtures'


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.inputs=self.root/'input';self.inputs.mkdir()
        self.state=self.root/'state';self.report=self.root/'report.json'

    def tearDown(self):self.temporary.cleanup()

    def add(self,source_id='3358',value=None):
        p=self.inputs/f'product-{source_id}.json'
        p.write_bytes((FIXTURES/f'browser-product-{source_id}.json').read_bytes() if value is None else json.dumps(value).encode())
        return p

    def run_batch(self,**kwargs):
        return batch.run_batch(self.inputs,self.state,self.report,batch_complete=True,**kwargs)

    def test_three_files_are_serial_exact_hash_and_incremental(self):
        originals={sid:self.add(sid).read_bytes() for sid in ('3358','3363','4231')}
        with patch('socket.create_connection',side_effect=AssertionError('no network')):
            result=self.run_batch()
        self.assertEqual((result['imported'],result['already_imported'],result['rejected']),(3,0,0))
        self.assertEqual(result['processed_files'],3);self.assertEqual(result['source_requests'],0)
        self.assertEqual(result['coverage']['products_parsed'],3)
        for item in result['files']:
            self.assertEqual(item['input_sha256'],sha256(originals[item['source_id']]).hexdigest())
            self.assertEqual((self.inputs/item['file']).read_bytes(),originals[item['source_id']])
        again=batch.run_batch(self.inputs,self.state,self.root/'again.json',batch_complete=True)
        self.assertEqual((again['imported'],again['already_imported'],again['rejected']),(0,3,0))

    def test_unknown_field_fails_one_file_without_fake_values_or_batch_abort(self):
        self.add('3358');value=json.loads((FIXTURES/'browser-product-3363.json').read_text())
        value['snapshot']['fields'][2]['label']='新字段:';bad=self.add('3363',value)
        self.add('4231');result=self.run_batch()
        self.assertEqual((result['imported'],result['rejected']),(2,1))
        error=[item for item in result['files'] if item['status']=='rejected'][0]
        self.assertEqual(error['input_sha256'],sha256(bad.read_bytes()).hexdigest())
        self.assertNotIn('新字段',self.report.read_text())
        self.assertEqual(result['coverage']['products_parsed'],2)

    def test_filename_identity_mismatch_is_rejected(self):
        self.add('3363',json.loads((FIXTURES/'browser-product-3358.json').read_text()))
        self.assertEqual(self.run_batch()['rejected'],1)

    def test_captured_bytes_not_mutable_source_are_imported(self):
        source=self.add('3358');original=source.read_bytes()
        def mutate_after_capture(store,captured,**metadata):
            source.write_bytes(b'{"changed":true}')
            return importer(store,captured,**metadata)
        with patch.object(batch,'adapter_api',return_value=(mutate_after_capture,*API[1:])):
            result=self.run_batch()
        self.assertEqual(result['imported'],1)
        self.assertEqual(result['files'][0]['input_sha256'],sha256(original).hexdigest())
        s=Store(self.state)
        try:
            p=json.loads(s.db.execute('SELECT document FROM products').fetchone()[0])
            self.assertEqual(p['snapshot_provenance']['input_sha256'],sha256(original).hexdigest())
        finally:s.close()

    def test_pause_queue_budget_preserved_exactly(self):
        self.add();store=Store(self.state)
        store.enqueue({'kind':'product','url':'https://www.yanyue.cn/product/3358'})
        store.attempted('https://www.yanyue.cn/product/3358')
        store.fail('https://www.yanyue.cn/product/3358','source_http_403',pause=True)
        store.pause('source_http_403')
        store.put_meta('budget_summary',{'max_requests':5,'requests':2,'max_bytes':10485760,'bytes':73,'max_seconds':180})
        before_q=[tuple(r) for r in store.db.execute('SELECT * FROM queue')]
        before_m=[tuple(r) for r in store.db.execute('SELECT * FROM metadata ORDER BY key')]
        store.close();result=self.run_batch();self.assertTrue(result['source_state_unchanged'])
        store=Store(self.state)
        try:
            self.assertEqual(before_q,[tuple(r) for r in store.db.execute('SELECT * FROM queue')])
            self.assertEqual(before_m,[tuple(r) for r in store.db.execute('SELECT * FROM metadata ORDER BY key')])
        finally:store.close()

    def test_explicit_batch_completion_and_new_report_required(self):
        self.add()
        with self.assertRaises(batch.BatchError):batch.run_batch(self.inputs,self.state,self.report)
        self.assertFalse(self.state.exists())
        self.report.write_text('keep')
        with self.assertRaises(batch.BatchError):self.run_batch()
        self.assertEqual(self.report.read_text(),'keep')

    def test_report_and_temporary_paths_cannot_overlap_input_or_state(self):
        self.add()
        for report in (self.state/'state.sqlite',self.state/'state.sqlite-wal',self.state/'worker.lock',self.state/'cache/report.json',self.inputs/'report.json'):
            with self.subTest(report=report),self.assertRaises(batch.BatchError):
                batch.run_batch(self.inputs,self.state,report,batch_complete=True)
            self.assertFalse(self.state.exists())
        temporary=self.report.with_suffix(self.report.suffix+'.tmp')
        temporary.write_text('do not replace')
        with self.assertRaises(batch.BatchError):self.run_batch()
        self.assertEqual(temporary.read_text(),'do not replace')
        temporary.unlink();temporary.symlink_to(self.inputs/'product-3358.json')
        with self.assertRaises(batch.BatchError):self.run_batch()
        self.assertFalse(self.state.exists())

    def test_fixed_temporary_link_created_mid_batch_is_never_followed(self):
        source=self.add();original=source.read_bytes()
        fixed=self.report.with_suffix(self.report.suffix+'.tmp')
        def plant_link(store,captured,**metadata):
            fixed.symlink_to(source)
            return importer(store,captured,**metadata)
        with patch.object(batch,'adapter_api',return_value=(plant_link,*API[1:])):
            result=self.run_batch()
        self.assertEqual(result['imported'],1)
        self.assertEqual(source.read_bytes(),original)
        self.assertTrue(fixed.is_symlink())
        self.assertTrue(self.report.is_file())

    def test_report_namespace_guard_resolves_parent_directory_alias(self):
        self.add();alias=self.root/'alias';alias.symlink_to(self.inputs,target_is_directory=True)
        with self.assertRaises(batch.BatchError):
            batch.run_batch(self.inputs,self.state,alias/'report.json',batch_complete=True)
        self.assertFalse(self.state.exists())

    def test_unknown_files_ignored_never_loaded(self):
        self.add();(self.inputs/'cookies.json').write_text('DO_NOT_READ')
        (self.inputs/'product-001.json').write_text('DO_NOT_READ')
        result=self.run_batch();self.assertEqual(result['selected_files'],1)
        self.assertNotIn('DO_NOT_READ',self.report.read_text())

    def test_symlink_fifo_and_oversized_files_fail_boundedly(self):
        (self.inputs/'product-3358.json').symlink_to(FIXTURES/'browser-product-3358.json')
        os.mkfifo(self.inputs/'product-3363.json')
        (self.inputs/'product-4231.json').write_bytes(b'x'*(batch.MAX_FILE_BYTES+1))
        result=self.run_batch();self.assertEqual(result['rejected'],3)
        self.assertEqual(result['coverage']['products_parsed'],0)
        self.assertTrue(all(item['input_sha256'] is None for item in result['files']))

    def test_file_count_cap_blocks_before_store_mutation(self):
        for sid in range(1,batch.MAX_FILES+2):(self.inputs/f'product-{sid}.json').write_text('{}')
        with self.assertRaises(batch.BatchError):self.run_batch()
        self.assertFalse(self.state.exists())

    def test_repository_layout_uses_one_existing_adapter(self):
        expected_root=Path(__file__).resolve().parents[2]
        self.assertEqual(batch.ADAPTER,expected_root)
        self.assertEqual(Path(importer.__module__.replace('.', '/')).parts[0],'yanyue')
        import yanyue.snapshot_import
        self.assertEqual(Path(yanyue.snapshot_import.__file__).resolve(),expected_root/'tools/yanyue/snapshot_import.py')
        pins=json.loads((batch.HERE/'ADAPTER_SHA256.json').read_text())
        self.assertEqual(len(pins),21)
        self.assertIn("tools/yanyue/reviewed_images.py",pins)
        self.assertIn("data/reviewed-image-profile-product610.json",pins)
        for relative,digest in pins.items():
            self.assertEqual(sha256((expected_root/relative).read_bytes()).hexdigest(),digest)

    def test_adapter_code_hash_mismatch_blocks_before_execution(self):
        with patch.object(batch,'ADAPTER',self.root):
            with self.assertRaises((batch.BatchError,OSError)):batch.adapter_api()

    def test_cli_nonzero_for_partial_failure_without_raw_error_text(self):
        self.add();value=json.loads((FIXTURES/'browser-product-3363.json').read_text())
        value['source_url']+='?token=DO_NOT_LOG';self.add('3363',value)
        output=io.StringIO()
        with redirect_stdout(output):status=batch.main(['--input-dir',str(self.inputs),'--state-dir',str(self.state),'--output',str(self.report),'--batch-complete'])
        self.assertEqual(status,2);self.assertNotIn('DO_NOT_LOG',output.getvalue()+self.report.read_text())

    def test_conflicting_existing_record_stays_last_good(self):
        path=self.add();self.run_batch()
        value=json.loads(path.read_text());value['product_name']='different';path.write_text(json.dumps(value))
        result=batch.run_batch(self.inputs,self.state,self.root/'again.json',batch_complete=True)
        self.assertEqual(result['rejected'],1)
        store=Store(self.state)
        try:self.assertEqual(json.loads(store.db.execute('SELECT document FROM products').fetchone()[0])['name'],'中华(金中支)')
        finally:store.close()


if __name__=='__main__':unittest.main()
