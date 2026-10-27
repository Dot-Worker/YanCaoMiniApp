"""Offline boundaries for root's authorized already-loaded product442 snapshot."""
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from yanyue.checkpoint import export_checkpoint, restore_checkpoint, read_checkpoint
from yanyue.cli import main
from yanyue.policy import PolicyError
from yanyue.schema import validate
from yanyue.snapshot_import import import_snapshot, parse_snapshot, read_snapshot, MAX_SNAPSHOT_BYTES, browser_index_status
from yanyue.store import Store, dump_json

FIXTURE = Path(__file__).parent / 'fixtures/current-product-442-fields-20261027.json'
META = dict(source_url='https://www.yanyue.cn/product/442', source_id='442', name='中华(硬)', observed_at='2026-10-27T05:13:19Z')


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.value, self.digest = read_snapshot(FIXTURE)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def parse(self, value=None, **metadata):
        return parse_snapshot(self.value if value is None else value, input_sha256=self.digest, **(META | metadata))

    def test_actual_snapshot_preserves_exact_identity_and_observation(self):
        p = self.parse()['product']
        self.assertEqual((p['id'], p['source_id'], p['name']), ('yanyue:442', '442', '中华(硬)'))
        self.assertEqual(p['observed_at'], '2026-10-27T05:13:19+00:00')
        self.assertEqual(p['snapshot_provenance']['source_host'], 'www.yanyue.cn')
        self.assertEqual(p['snapshot_provenance']['input_sha256'], sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertIsNone(self.parse()['source_content_sha256'])
        self.assertIsNone(p['snapshot_provenance']['original_html_sha256'])
        self.assertEqual(p['source_access_method'], 'imported_loaded_browser_field_snapshot')
        self.assertEqual(p['fields']['brand_name']['value'], '中华')
        self.assertEqual(p['fields']['type']['value'], '烤烟型')

    def test_all_numeric_and_barcode_image_evidence_remains_unread(self):
        p = self.parse()['product']
        for fact in list(p['measurements'].values()) + p['barcodes']:
            self.assertIsNone(fact['value'])
            self.assertIsNone(fact['raw_value'])
            self.assertIsNone(fact['raw_unit'])
            self.assertEqual((fact['verification_status'], fact['extraction_method']), ('needs_review', 'image_not_read'))
        self.assertEqual(len(p['barcodes'][0]['evidence_urls']), 2)
        self.assertEqual(len(p['images']), 1)
        self.assertIsNone(p['original_text'])

    def test_unknown_top_keys_price_comment_and_raw_html_are_rejected(self):
        for key in ('comments', 'price', 'html', 'cookies', 'url'):
            with self.subTest(key=key), self.assertRaises(PolicyError):
                self.parse(self.value | {key: 'secret'})
        for label in ('价格:', '网友评论:', '排名:', '购买渠道:'):
            value = deepcopy(self.value)
            value['fields'].append(dict(label=label, text='not imported', images=[]))
            with self.subTest(label=label), self.assertRaises(PolicyError):
                self.parse(value)

    def test_unknown_row_keys_and_duplicate_labels_are_rejected(self):
        for modification in ('extra', 'duplicate'):
            value = deepcopy(self.value)
            if modification == 'extra':
                value['fields'][0]['verified'] = True
            else:
                value['fields'][1]['label'] = value['fields'][0]['label']
            with self.assertRaises(PolicyError): self.parse(value)

    def test_external_secret_noncanonical_urls_rejected_everywhere(self):
        for url in ('https://evil.example/product/442', META['source_url']+'?loading=secret', META['source_url']+'#x', META['source_url']+'/', 'https://www.yanyue.cn:443/product/442', 'https://www.yanyue.cn/product/441'):
            with self.subTest(url=url), self.assertRaises(PolicyError): self.parse(source_url=url)
        for url in ('https://evil.example/image.png', self.value['photo']+'?token=secret', self.value['photo']+'#x', self.value['photo'].replace('https:', 'http:'), '/thumb/genpic/a.png'):
            for target in ('photo', 'numeric'):
                value = deepcopy(self.value)
                if target == 'photo': value['photo'] = url
                else: value['fields'][2]['images'] = [url]
                with self.subTest(target=target,url=url), self.assertRaises(PolicyError): self.parse(value)

    def test_source_id_name_and_timestamp_must_be_explicit_and_valid(self):
        for metadata in ({'source_id': '441'}, {'source_id': 442}, {'name': '中华(硬) 价格多少钱'}, {'name': '<script>x</script>'}, {'observed_at':'2026-10-27'}, {'observed_at':'2026-02-30T01:02:03Z'}, {'observed_at':'2999-01-01T00:00:00Z'}, {'observed_at':'2026-10-27T05:13:19+08:00'}):
            with self.subTest(metadata=metadata), self.assertRaises(PolicyError): self.parse(**metadata)

    def test_forged_numeric_text_ocr_and_values_rejected(self):
        for i, text in ((2,'11mg'), (3,'1mg'), (13,'6901028075763')):
            value = deepcopy(self.value); value['fields'][i]['text'] = text
            with self.assertRaises(PolicyError): self.parse(value)
        value = deepcopy(self.value); value['fields'][2]['value'] = 11
        with self.assertRaises(PolicyError): self.parse(value)
        value = deepcopy(self.value); value['fields'][2]['images'] = []
        p=self.parse(value)['product']; fact=p['measurements']['tar_mg']
        self.assertIsNone(fact['value']); self.assertEqual(fact['verification_status'],'missing')

    def test_image_role_and_text_injection_rejected(self):
        value=deepcopy(self.value); value['photo']=value['fields'][2]['images'][0]
        with self.assertRaises(PolicyError): self.parse(value)
        value=deepcopy(self.value); value['fields'][2]['images']=[value['photo']]
        with self.assertRaises(PolicyError): self.parse(value)
        for text in ('<b>中华</b>', 'https://evil.example', 'cookie=secret', '中华\n评论'):
            value=deepcopy(self.value); value['fields'][0]['text']=text
            with self.assertRaises(PolicyError): self.parse(value)

    def test_bounded_json_duplicate_keys_nonfinite_and_invalid_utf8(self):
        for raw in (b'{"fields":[],"fields":[],"photo":null}', b'{"fields":NaN,"photo":null}', b'\xff', b' '*(MAX_SNAPSHOT_BYTES+1)):
            path=self.root/'invalid.json'; path.write_bytes(raw)
            with self.assertRaises(PolicyError): read_snapshot(path)

    def test_symlink_and_fifo_are_rejected_without_blocking(self):
        link=self.root/'link.json'; link.symlink_to(FIXTURE)
        with self.assertRaises(PolicyError): read_snapshot(link)
        fifo=self.root/'fifo'; os.mkfifo(fifo)
        with self.assertRaises(PolicyError): read_snapshot(fifo)

    def test_import_does_not_send_requests_or_create_source_jobs(self):
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')), patch('yanyue.cli.BrowserFetcher', side_effect=AssertionError('browser forbidden')):
            result=import_snapshot(self.store,FIXTURE,**META)
        self.assertEqual(result['imported_products'],1)
        self.assertFalse(result['network_executed']); self.assertEqual(result['source_requests'],0)
        self.assertEqual(result['coverage']['products_parsed'],1)
        self.assertEqual(result['coverage']['queue'],{})
        self.assertEqual(result['coverage']['assets'],{'pending':11})
        for flag in ('whole_website_complete','field_review_complete','publication_ready'):
            self.assertFalse(result['coverage'][flag])

    def test_existing_paused_queue_and_failure_budget_are_byte_preserved(self):
        self.store.enqueue({'kind':'product','url':META['source_url']})
        self.store.attempted(META['source_url']); self.store.fail(META['source_url'],'source_http_403',pause=True)
        self.store.pause('source_http_403')
        self.store.put_meta('budget_summary', {'max_requests':5,'requests':2,'max_bytes':10485760,'bytes':73,'max_seconds':180})
        before_queue=[tuple(r) for r in self.store.db.execute('SELECT * FROM queue')]
        before_meta=[tuple(r) for r in self.store.db.execute('SELECT * FROM metadata ORDER BY key')]
        import_snapshot(self.store,FIXTURE,**META)
        self.assertEqual(before_queue,[tuple(r) for r in self.store.db.execute('SELECT * FROM queue')])
        self.assertEqual(before_meta,[tuple(r) for r in self.store.db.execute('SELECT * FROM metadata ORDER BY key')])
        self.assertEqual(self.store.coverage()['pause']['reason'],'source_http_403')

    def test_identical_import_is_idempotent_and_preserves_import_time(self):
        import_snapshot(self.store,FIXTURE,**META)
        original=self.store.db.execute('SELECT document FROM products').fetchone()[0]
        with patch('yanyue.snapshot_import.utcnow', return_value='2026-10-28T01:00:00+00:00'):
            result=import_snapshot(self.store,FIXTURE,**META)
        self.assertTrue(result['already_imported']); self.assertEqual(result['imported_products'],0)
        self.assertEqual(original,self.store.db.execute('SELECT document FROM products').fetchone()[0])

    def test_different_existing_record_is_not_overwritten(self):
        import_snapshot(self.store,FIXTURE,**META)
        original=self.store.db.execute('SELECT document FROM products').fetchone()[0]
        with self.assertRaises(PolicyError): import_snapshot(self.store,FIXTURE,**(META|{'name':'different'}))
        self.assertEqual(original,self.store.db.execute('SELECT document FROM products').fetchone()[0])

    def test_checkpoint_roundtrip_preserves_snapshot_and_source_pause(self):
        self.store.pause('source_http_403')
        import_snapshot(self.store,FIXTURE,**META)
        export_checkpoint(self.store,self.root/'export')
        checkpoint=read_checkpoint(self.root/'export')
        self.assertEqual(checkpoint['products'][0]['snapshot_provenance']['input_sha256'],self.digest)
        restored=Store(self.root/'restored')
        try:
            report=restore_checkpoint(restored,self.root/'export')
            self.assertEqual(report['products_parsed'],1); self.assertEqual(report['queue'],{})
            self.assertEqual(report['pause']['reason'],'source_http_403')
            self.assertEqual(report['assets'],{'pending':11})
        finally: restored.close()

    def test_checkpoint_semantics_reject_forged_review_date_and_host(self):
        p=self.parse()['product']
        mutations=[lambda p:p['snapshot_provenance'].update(source_host='evil.example'),
                   lambda p:p['measurements']['tar_mg'].update(value=11),
                   lambda p:p['measurements']['tar_mg'].update(verification_status='source_reported'),
                   lambda p:p['fields']['brand_name'].update(observed_at='2026-10-26T01:00:00+00:00'),
                   lambda p:p.pop('snapshot_provenance'),
                   lambda p:p.update(source_access_method='normal_browser_dom')]
        for mutation in mutations:
            changed=deepcopy(p);mutation(changed)
            with self.assertRaises(PolicyError):validate(changed,'product.schema.json')

    def test_cli_import_is_offline_and_failures_are_sanitized(self):
        args=['import-browser-snapshot','--state-dir',str(self.root/'cli'),'--input',str(FIXTURE),'--source-url',META['source_url'],'--source-id','442','--name',META['name'],'--observed-at',META['observed_at']]
        out=io.StringIO()
        with redirect_stdout(out),patch('yanyue.cli.BrowserFetcher',side_effect=AssertionError('no browser')):
            status=main(args)
        self.assertEqual(status,0); self.assertEqual(json.loads(out.getvalue())['source_requests'],0)
        args[args.index('--source-url')+1]+='?token=DO_NOT_LOG'
        out=io.StringIO()
        with redirect_stdout(out):status=main(args)
        self.assertEqual(status,2); self.assertNotIn('DO_NOT_LOG',out.getvalue())

    def test_three_real_envelopes_preserve_milliseconds_and_missing_fields(self):
        for source_id, expected_format, fraction in [('3358','中支','436'),('3363','中支','617'),('4231','细支','340')]:
            path=FIXTURE.parent/f'browser-product-{source_id}.json'
            result=import_snapshot(self.store,path)
            self.assertEqual(result['imported_products'],1)
            p=json.loads(self.store.db.execute('SELECT document FROM products WHERE source_id=?',(source_id,)).fetchone()[0])
            self.assertIn('.'+fraction+'+00:00',p['observed_at'])
            self.assertEqual(p['snapshot_provenance']['source_time_precision'],'millisecond')
            self.assertEqual(p['snapshot_provenance']['input_format'],'loaded_browser_product_envelope_v1')
            self.assertEqual(p['snapshot_provenance']['input_sha256'],sha256(path.read_bytes()).hexdigest())
            self.assertEqual(p['fields']['cigarette_format']['value'],expected_format)
            self.assertIsNone(p['measurements']['filter_length_mm']['value'])
            self.assertEqual(p['measurements']['filter_length_mm']['verification_status'],'missing')
            for fact in list(p['measurements'].values())+p['barcodes']:
                self.assertIsNone(fact['value'])
        p=json.loads(self.store.db.execute("SELECT document FROM products WHERE source_id='3363'").fetchone()[0])
        self.assertEqual(p['fields']['secondary_color']['verification_status'],'missing')
        p=json.loads(self.store.db.execute("SELECT document FROM products WHERE source_id='4231'").fetchone()[0])
        self.assertEqual(p['measurements']['length_mm']['verification_status'],'missing')
        export_checkpoint(self.store,self.root/'ms-export')
        restored=Store(self.root/'ms-restored')
        try:
            self.assertEqual(restore_checkpoint(restored,self.root/'ms-export')['products_parsed'],3)
        finally:restored.close()

    def test_envelope_metadata_conflict_and_unknown_labels_fail_closed(self):
        path=FIXTURE.parent/'browser-product-3358.json'
        with self.assertRaises(PolicyError):import_snapshot(self.store,path,source_id='442')
        with self.assertRaises(PolicyError):import_snapshot(self.store,path,observed_at='2026-10-27T05:17:14Z')
        value=json.loads(path.read_text());value['snapshot']['fields'][2]['label']='unknown:'
        changed=self.root/'unknown.json';changed.write_text(json.dumps(value))
        with self.assertRaises(PolicyError):import_snapshot(self.store,changed)
        value=json.loads(path.read_text());value['cookies']='secret';changed.write_text(json.dumps(value))
        with self.assertRaises(PolicyError):import_snapshot(self.store,changed)
        self.assertEqual(self.store.coverage()['products_parsed'],0)

    def test_four_detail_records_are_distinct_from_56_index_links(self):
        import_snapshot(self.store,FIXTURE,**META)
        for source_id in ('3358','3363','4231'):
            import_snapshot(self.store,FIXTURE.parent/f'browser-product-{source_id}.json')
        report=browser_index_status(self.store,FIXTURE.parent/'brand13-browser-index-20261027.json')
        self.assertEqual((report['unique_indexed_products'],report['stored_detail_records'],report['indexed_products_without_detail_records']),(56,4,52))
        self.assertEqual(report['stored_detail_source_ids'],['442','3358','3363','4231'])
        self.assertEqual(report['source_time_precision'],'day')
        self.assertFalse(report['index_stored_in_database']);self.assertFalse(report['source_jobs_updated'])
        self.assertEqual(self.store.coverage()['queue'],{})
        self.assertEqual(self.store.coverage()['products_parsed'],4)

    def test_index_does_not_accept_duplicates_external_links_or_fake_counts(self):
        source=FIXTURE.parent/'brand13-browser-index-20261027.json'
        for case in ('duplicate','external','count','unknown','bad_date'):
            value=json.loads(source.read_text())
            if case=='duplicate':value['products'][1]=value['products'][0]
            elif case=='external':value['products'][0]['url']='https://evil.example/product/442'
            elif case=='count':value['expected_records']=57
            elif case=='unknown':value['comments']=[]
            else:value['observed_date']='2026-02-30'
            path=self.root/'badindex.json';path.write_text(json.dumps(value))
            with self.subTest(case=case),self.assertRaises(PolicyError):browser_index_status(self.store,path)
        self.assertEqual(self.store.coverage()['queue'],{})

    def test_cli_envelope_does_not_require_guessed_identity(self):
        args=['import-browser-snapshot','--state-dir',str(self.root/'envelope-cli'),'--input',str(FIXTURE.parent/'browser-product-3358.json')]
        out=io.StringIO()
        with redirect_stdout(out): status=main(args)
        self.assertEqual(status,0);self.assertEqual(json.loads(out.getvalue())['source_id'],'3358')


if __name__=='__main__': unittest.main()
