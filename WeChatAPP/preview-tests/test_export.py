"""Small public, synthetic-only tests for the frozen-data exporter helpers."""
import hashlib
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('preview_export',Path(__file__).parents[1]/'preview-tools/build_data.py')
export=importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

class ExportHelpers(unittest.TestCase):
    def test_read_pinned_rejects_modified_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.json';path.write_text('{"sample":true}')
            with self.assertRaises(ValueError):export.read_pinned(path,'0'*64)
    def test_read_pinned_accepts_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.json';path.write_text('{"sample":true}')
            expected=hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(export.read_pinned(path,expected),{'sample':True})
    def test_whitelist_omits_unrequested_fields(self):
        result=export.project_field({'value':None,'price':99,'original_text':'synthetic'}, {},'610')
        self.assertNotIn('price',result);self.assertNotIn('original_text',result)
        self.assertIsNone(result['value'])
    def test_shared_url_does_not_attach_another_product_image(self):
        assets={'source-example':{'path':'/assets/reviewed/sample.png','productId':'610'}}
        field={'value':None,'evidence_urls':['source-example']}
        self.assertEqual(export.project_field(field,assets,'999')['evidence_paths'],[])
        self.assertEqual(export.project_field(field,assets,'610')['evidence_paths'],['/assets/reviewed/sample.png'])
    def test_zero_is_preserved(self):
        result=export.project_field({'value':0,'raw_unit':'mg'}, {},'610')
        self.assertEqual(result['value'],0)
    def test_validation_is_not_removed_by_optimized_python(self):
        with self.assertRaisesRegex(ValueError,'stop'):
            export.require(False,'stop')
    def test_null_raw_unit_and_context_unit_are_not_merged(self):
        result=export.project_field({'value':20,'raw_unit':None,'expected_unit':'支'}, {},'610')
        self.assertIsNone(result['raw_unit']);self.assertEqual(result['expected_unit'],'支')
    def test_private_review_is_projected_to_minimal_machine_provenance(self):
        candidate={'source_product_id':'610','target_field':'nicotine_mg','evidence_sha256':'a'*64,'evidence_source_asset_url':'https://example.invalid/image.png','source_snapshot_sha256':'b'*64,'source_snapshot_observed_at':'2026-10-27','unit_basis':'visible_image_text','evidence_variant_index':0,'evidence_variant_count':1,'candidate_status':'internal-only'}
        evidence={'manifest_sha256':'synthetic-only','candidate':candidate,'review_method':'internal-only','reviewed_at':'2026-10-27','source_quality':'source_reported_not_manufacturer_verified'}
        result=export.project_field({'value':1,'image_review':evidence}, {},'610')
        self.assertNotIn('image_review',result)
        public=result['review_provenance']
        self.assertNotIn('candidate',public);self.assertNotIn('review_method',public);self.assertNotIn('candidate_status',public)
        self.assertEqual(public['target_field'],'nicotine_mg')
        expected=hashlib.sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(public['image_review_sha256'],expected)

def synthetic_review_fixture():
    measures={}
    approved={'tar_mg':(10,'mg'),'nicotine_mg':(1,'mg'),'carbon_monoxide_mg':(10,'mg'),'length_mm':(84,'mm'),'sticks_per_pack':(20,'支'),'packs_per_carton':(10,'盒')}
    candidates=[]
    for key in export.MEASURE_KEYS:
        unit=approved.get(key,(None,'mm'))[1]
        field={'value':None,'raw_value':None,'raw_unit':None,'expected_unit':unit,'source_label':key,'source_url':'https://example.invalid/product/610','observed_at':'2026-10-27','verification_status':'needs_review','extraction_method':'image_not_read','evidence_urls':['https://example.invalid/'+key]}
        measures[key]=field
        if key in approved:
            value=approved[key][0]
            printed=unit if unit in ('mg','mm') else None
            candidates.append({'target_field':key,'source_product_id':'610','source_label':key,'candidate_value':value,'field_context_expected_unit':unit,'image_display_text':str(value)+(printed or ''),'unit_printed_on_image':printed,'evidence_source_asset_url':field['evidence_urls'][0],'source_quality':'source_reported_not_manufacturer_verified'})
    barcodes=[]
    for kind,values in [('pack',['001','002']),('carton',['003'])]:
        urls=['https://example.invalid/barcode/'+v for v in values]
        barcodes.append({'kind':kind,'value':None,'raw_value':None,'raw_unit':None,'expected_unit':None,'source_label':kind,'source_url':'https://example.invalid/product/610','observed_at':'2026-10-27','verification_status':'needs_review','extraction_method':'image_not_read','evidence_urls':urls})
        for value,url in zip(values,urls):
            candidates.append({'target_field':kind,'source_product_id':'610','source_label':kind,'candidate_value':value,'field_context_expected_unit':None,'image_display_text':value,'unit_printed_on_image':None,'evidence_source_asset_url':url,'source_quality':'source_reported_not_manufacturer_verified'})
    base={'source_id':'610','name':'Synthetic review test only','fields':{'type':{'value':'synthetic'}},'images':[],'measurements':measures,'barcodes':barcodes}
    manifest={'generated_at':'2026-10-27T00:00:00Z','numeric_and_barcode_candidates':candidates}
    reviewed=deepcopy(base)
    for candidate in candidates:
        target=candidate['target_field']
        if target in measures:
            reviewed['measurements'][target]=export.expected_reviewed_field(measures[target],candidate,target,manifest)
    reviewed['barcodes']=[]
    for original in barcodes:
        for candidate in candidates:
            if candidate['target_field']==original['kind']:
                reviewed['barcodes'].append(export.expected_reviewed_field(original,candidate,original['kind'],manifest))
    return base,reviewed,manifest

class ReviewBinding(unittest.TestCase):
    def test_exact_reconstruction_is_accepted(self):
        base,reviewed,manifest=synthetic_review_fixture()
        self.assertEqual(export.validate_reviewed_product(base,reviewed,manifest),reviewed)
    def test_swapped_complete_measurement_objects_are_rejected(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['length_mm'],reviewed['measurements']['sticks_per_pack']=reviewed['measurements']['sticks_per_pack'],reviewed['measurements']['length_mm']
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_missing_reviewed_value_is_rejected(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['length_mm']=deepcopy(base['measurements']['length_mm'])
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_unapproved_null_field_cannot_be_relabelled(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['circumference_mm']['verification_status']='manually_read_image'
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_text_image_and_identity_changes_are_rejected(self):
        for key,value in [('fields',{}),('images',[{'path':'fake.png'}]),('name','changed')]:
            with self.subTest(key=key):
                base,reviewed,manifest=synthetic_review_fixture();reviewed[key]=value
                with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_barcode_kind_and_variant_order_cannot_be_rewritten(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['barcodes'][0]['kind']='carton'
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
        base,reviewed,manifest=synthetic_review_fixture();reviewed['barcodes'].reverse()
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_review_metadata_is_not_arbitrary(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['tar_mg']['image_review']['reviewed_at']='fake-date'
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_target_key_is_bound_before_reconstruction(self):
        base,reviewed,manifest=synthetic_review_fixture()
        candidate=manifest['numeric_and_barcode_candidates'][0]
        with self.assertRaises(ValueError):export.expected_reviewed_field(base['measurements']['tar_mg'],candidate,'length_mm',manifest)
    def test_reviewed_field_cannot_acquire_extra_unapproved_data(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['tar_mg']['invented_field']='not allowed'
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_numeric_boolean_type_change_is_rejected(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['nicotine_mg']['value']=True
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)
    def test_unknown_measurement_key_is_rejected(self):
        base,reviewed,manifest=synthetic_review_fixture()
        reviewed['measurements']['new_number']=reviewed['measurements']['tar_mg']
        with self.assertRaises(ValueError):export.validate_reviewed_product(base,reviewed,manifest)

if __name__=='__main__':unittest.main()
