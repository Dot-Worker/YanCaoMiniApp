"""Synthetic small directory/listings + actual sanitized product field fixtures.

These miniature listing documents are mock evidence, never claimed as complete
source pages. Actual 522/82 integration is performed separately on root's files.
"""
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from yanyue.catalog import make_evidence,derive_catalog,export_view,install_catalog,catalog_edges,read_evidence_file
from yanyue.catalog_cli import initialize,ReadonlyStore,write_new_export
from yanyue.checkpoint import export_checkpoint,read_checkpoint,restore_checkpoint
from yanyue.policy import PolicyError
from yanyue.schema import validate
from yanyue.snapshot_import import import_snapshot
from yanyue.store import Store,dump_json

FIX=Path(__file__).parent/'fixtures'

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        source=Store(self.root/'source')
        import_snapshot(source,FIX/'current-product-442-fields-20261027.json',source_url='https://www.yanyue.cn/product/442',source_id='442',name='中华(硬)',observed_at='2026-10-27T05:13:19Z')
        for sid in ('511','5914'):import_snapshot(source,FIX/f'catalog-product-{sid}.json')
        source.pause('source_execution_not_authorized_for_offline_import')
        self.original_products=[json.loads(r[0]) for r in source.db.execute('SELECT document FROM products ORDER BY source_id')]
        self.original_pause=source.meta('pause')
        export_checkpoint(source,self.root/'base');source.close()
        self.cp=self.root/'base/checkpoint.json'
        self.pin=patch('yanyue.catalog_cli.BASE_CHECKPOINT_SHA256',sha256(self.cp.read_bytes()).hexdigest());self.pin.start()
        directory={'source_url':'https://www.yanyue.cn/tobacco','observed_at':'2026-10-27T05:48:18.728Z','scope':'traditional_tobacco_brand_directory_only','link_count':2,
                   'unique_brands':[{'name':'中华','url':'https://www.yanyue.cn/sort/13'},{'name':'黄鹤楼','url':'https://www.yanyue.cn/sort/131'}]}
        one=[{'name':p['name'],'url':p['source_url']} for p in self.original_products if p['source_id'] in {'442','5914'}]
        two=[{'name':p['name'],'url':p['source_url']} for p in self.original_products if p['source_id']=='511']
        index={'observed_date':'2026-10-27','brand_id':13,'method':'current_cloud_browser_rendered_dom','scope':'single_brand_index_only','expected_records':2,'pages':1,'products':one}
        page={'source_url':'https://www.yanyue.cn/sort/131','observed_at':'2026-10-27T05:49:45.965Z','brand_id':131,'total_text':'共1条记录,1/1页','products':two,'pagination':[]}
        self.directory=self.root/'directory.json';self.index=self.root/'index.json';self.page=self.root/'page.json'
        for p,v in ((self.directory,directory),(self.index,index),(self.page,page)):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
        self.evidence=self.make();self.target=self.root/'catalog'

    def tearDown(self):self.pin.stop();self.temp.cleanup()
    def make(self):return make_evidence(self.directory,[self.index],[self.page])
    def init(self):return initialize(self.cp,self.target,self.evidence)
    def view(self,**kwargs):
        store=ReadonlyStore(self.target)
        try:return export_view(store,**kwargs)
        finally:store.close()

    def test_explicit_memberships_leave_products_pause_jobs_and_reports_unchanged(self):
        with patch('socket.create_connection',side_effect=AssertionError('offline')):result=self.init()
        self.assertEqual((result['brands'],result['memberships']),(2,3));self.assertEqual(result['source_jobs_created'],0)
        store=ReadonlyStore(self.target)
        try:
            self.assertEqual([json.loads(r[0]) for r in store.db.execute('SELECT document FROM products ORDER BY source_id')],self.original_products)
            self.assertEqual(store.meta('pause'),self.original_pause)
            self.assertEqual(store.db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
            self.assertEqual(store.db.execute('SELECT count(*) FROM brand_reports').fetchone()[0],0)
        finally:store.close()

    def test_english_product_link_maps_without_chinese_name_prefix(self):
        self.init();view=self.view(brand_id='13',text='chunghwa')
        self.assertEqual([p['source_id'] for p in view['products']],['5914'])
        self.assertEqual(view['products'][0]['brand_source_ids'],['13'])
        self.assertEqual(len(view['memberships']),1)

    def test_raw_hash_pointer_and_original_time_precision_survive(self):
        self.init();view=self.view()
        identity=view['brands'][0]['identity_evidence']
        self.assertEqual(identity['observed_at'],'2026-10-27T05:48:18.728Z')
        self.assertEqual(identity['time_precision'],'millisecond')
        for m in view['memberships']:
            ev=m['listing_evidence'][0]
            raw=self.index.read_bytes() if m['brand_source_id']=='13' else self.page.read_bytes()
            self.assertEqual(ev['input_sha256'],sha256(raw).hexdigest())
            self.assertTrue(ev['product_pointer'].startswith('/products/'))
            if m['brand_source_id']=='13':
                self.assertEqual(ev['observed_at'],'2026-10-27');self.assertEqual(ev['time_precision'],'day');self.assertIsNone(ev['source_url'])
            else:self.assertEqual(ev['observed_at'],'2026-10-27T05:49:45.965Z')

    def test_no_current_classification_is_invented_from_prior_labels(self):
        self.init();view=self.view()
        self.assertTrue(all(b['source_groups'] is None for b in view['brands']))
        self.assertTrue(all(b['source_groups_status']=='not_collected_in_current_flat_directory' for b in view['brands']))
        counts={b['source_id']:b['listing_summary'] for b in view['brands']}
        self.assertIsNone(counts['13']['source_reported_product_count'])
        self.assertEqual(counts['13']['operator_reported_expected_records'],2)
        self.assertEqual(counts['131']['source_reported_product_count'],1)

    def test_identical_reimport_is_readonly_idempotent(self):
        self.init();db=self.target/'state.sqlite';before=db.read_bytes();names=set(self.target.iterdir())
        result=self.init()
        self.assertTrue(result['already_imported']);self.assertEqual(before,db.read_bytes());self.assertEqual(names,set(self.target.iterdir()))

    def test_conflict_rejected_before_any_last_good_update(self):
        self.init();db=self.target/'state.sqlite';before=db.read_bytes()
        value=json.loads(self.index.read_text());value['observed_date']='2026-10-26';self.index.write_text(json.dumps(value))
        with self.assertRaises(PolicyError):initialize(self.cp,self.target,self.make())
        self.assertEqual(before,db.read_bytes())

    def test_existing_source_database_cannot_be_used_as_catalog_target(self):
        db=self.root/'source/state.sqlite';before=db.read_bytes();names=set(db.parent.iterdir())
        with self.assertRaises(PolicyError):initialize(self.cp,db.parent,self.evidence)
        self.assertEqual(before,db.read_bytes());self.assertEqual(names,set(db.parent.iterdir()))

    def test_unknown_price_comment_fields_and_duplicate_json_keys_rejected(self):
        for key in ('price','comments','cookies','raw_html'):
            value=json.loads(self.page.read_text());value[key]='not retained';self.page.write_text(json.dumps(value))
            with self.assertRaises(PolicyError):self.make()
            value.pop(key);self.page.write_text(json.dumps(value))
        self.page.write_text('{"brand_id":131,"brand_id":13}')
        with self.assertRaises(PolicyError):self.make()

    def test_external_query_alias_and_conflicting_brand_page_are_rejected(self):
        original=json.loads(self.page.read_text())
        for changes in ({'source_url':original['source_url']+'?loading=secret'},{'source_url':'https://evil.example/sort/131'},{'brand_id':13},{'total_text':'共1条记录,2/2页'},{'observed_at':'2026-02-30T01:02:03.000Z'}):
            self.page.write_text(json.dumps(original|changes))
            with self.assertRaises(PolicyError):self.make()
        self.page.write_text(json.dumps(original))
        value=json.loads(self.directory.read_text());value['unique_brands'][0]['url']='https://res.yanyue.cn/sort/13';self.directory.write_text(json.dumps(value))
        with self.assertRaises(PolicyError):self.make()

    def test_missing_product_or_name_conflict_does_not_guess_relation(self):
        for remove in (True,False):
            evidence=deepcopy(self.evidence)
            doc=next(d for d in evidence['documents'] if d['format']=='brand_index');value=json.loads(doc['raw_json'])
            if remove:value['products'].pop();value['expected_records']-=1
            else:value['products'][0]['name']='different source name'
            doc['raw_json']=json.dumps(value);doc['sha256']=sha256(doc['raw_json'].encode()).hexdigest()
            with self.assertRaises(PolicyError):derive_catalog(self.original_products,evidence)

    def test_safe_observed_page_one_pagination_alias_is_not_a_new_job(self):
        value=json.loads(self.page.read_text());value['pagination']=[{'text':'1','url':'https://www.yanyue.cn/sort/sort/sortid/131/p/1'}];self.page.write_text(json.dumps(value))
        graph=derive_catalog(self.original_products,self.make());self.assertEqual(len(graph['memberships']),3)

    def test_checkpoint_roundtrip_retains_evidence_without_queue_fabrication(self):
        self.init();store=Store(self.target)
        try:export_checkpoint(store,self.root/'export')
        finally:store.close()
        value=read_checkpoint(self.root/'export')
        self.assertEqual(value['catalog_evidence'],self.evidence);self.assertEqual(value['queue'],[])
        restored=Store(self.root/'restored')
        try:
            restore_checkpoint(restored,self.root/'export')
            self.assertEqual(export_view(restored),self.view())
            self.assertEqual(restored.meta('pause'),self.original_pause)
        finally:restored.close()

    def checkpoint(self):
        self.init();store=Store(self.target)
        try:export_checkpoint(store,self.root/'export')
        finally:store.close()
        return json.loads((self.root/'export/checkpoint.json').read_text())

    def test_checkpoint_rejects_hash_pointer_edge_and_classification_tampering(self):
        value=self.checkpoint()
        mutations=[lambda v:v['catalog_evidence']['documents'][0].update(raw_json='{}'),
                   lambda v:v['catalog_evidence']['documents'][0].update(key='brand-index:131'),
                   lambda v:v['edges'].append({'parent_url':'https://www.yanyue.cn/sort/131','child_url':'https://www.yanyue.cn/product/5914'}),
                   lambda v:v['edges'].pop(),
                   lambda v:v['brands'][0].update(source_groups=['大陆品牌'])]
        for mutation in mutations:
            changed=deepcopy(value);mutation(changed)
            with self.assertRaises(PolicyError):validate(changed,'checkpoint.schema.json')

    def test_catalog_cannot_legalize_an_unproved_disconnected_edge(self):
        value=self.checkpoint();value.pop('catalog_evidence')
        path=self.root/'no-evidence.json';path.write_text(json.dumps(value))
        with self.assertRaises(PolicyError):read_checkpoint(path)

    def test_explicit_multiple_memberships_are_possible_not_name_guessed(self):
        directory=json.loads(self.directory.read_text());directory['unique_brands'].append({'name':'中华','url':'https://www.yanyue.cn/sort/999'});directory['link_count']+=1;self.directory.write_text(json.dumps(directory))
        index=json.loads(self.index.read_text());index['brand_id']=999;index['products']=[p for p in index['products'] if p['url'].endswith('/5914')];index['expected_records']=1
        other=self.root/'other-index.json';other.write_text(json.dumps(index))
        evidence=make_evidence(self.directory,[self.index,other],[self.page]);graph=derive_catalog(self.original_products,evidence)
        self.assertEqual([m['brand_source_id'] for m in graph['memberships'] if m['product_source_id']=='5914'],['13','999'])

    def test_input_symlink_fifo_oversize_and_unknown_format_fail(self):
        symlink=self.root/'link';symlink.symlink_to(self.directory)
        with self.assertRaises(PolicyError):read_evidence_file(symlink)
        fifo=self.root/'fifo';os.mkfifo(fifo)
        with self.assertRaises(PolicyError):read_evidence_file(fifo)
        oversized=self.root/'big';oversized.write_bytes(b'x'*(128*1024+1))
        with self.assertRaises(PolicyError):read_evidence_file(oversized)
        changed=deepcopy(self.evidence);changed['documents'][0]['format']='forum'
        with self.assertRaises(PolicyError):derive_catalog(self.original_products,changed)

    def test_exact_baseline_hash_and_export_path_prevent_overwrite(self):
        bad=self.root/'changed-checkpoint.json';bad.write_bytes(self.cp.read_bytes()+b' ')
        with self.assertRaises(PolicyError):initialize(bad,self.target,self.evidence)
        self.assertFalse(self.target.exists())
        self.init();view=self.view();existing=self.root/'existing';existing.write_text('keep')
        for path in (existing,self.target/'new.json',self.target/'state.sqlite'):
            with self.assertRaises(PolicyError):write_new_export(path,view,self.target)
        self.assertEqual(existing.read_text(),'keep')
        output=self.root/'catalog-view.json';write_new_export(output,view,self.target)
        self.assertEqual(json.loads(output.read_text()),view)

if __name__=='__main__':unittest.main()


class CatalogCLIArgumentPrivacy(unittest.TestCase):
    def test_argument_errors_are_stable_without_private_path_echo(self):
        from contextlib import redirect_stdout, redirect_stderr
        from yanyue import catalog_cli
        canary = '/synthetic/private/ARGUMENT_CANARY'
        cases = [[], ['query', '--state-dir', canary, '--unexpected', canary],
                 ['unknown-command', canary], ['query', '--state-dir']]
        for args in cases:
            with self.subTest(args=args):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(catalog_cli, 'ReadonlyStore', side_effect=AssertionError('No state access on argument errors')):
                    code = catalog_cli.main(args)
                self.assertEqual(code, 2)
                self.assertEqual(stderr.getvalue(), '')
                self.assertNotIn(canary, stdout.getvalue())
                self.assertEqual(json.loads(stdout.getvalue()), {'error':'offline_catalog_contract_or_conflict','network_executed':False})
