"""Build test-only image inputs in a temporary directory.

No actual audit manifest, download metadata, 82-record checkpoint or source image
bytes are packaged here. Synthetic pins are installed only by unittest.mock in
the caller; the real production gate rejects this generated manifest.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from PIL import Image
from yanyue import reviewed_images as review
from yanyue.checkpoint import export_checkpoint
from yanyue.snapshot_import import import_snapshot
from yanyue.store import Store, dump_json


def build(root):
    root=Path(root);root.mkdir()
    images=root/'images';images.mkdir()
    source=Path(__file__).parent/'fixtures/public-reviewed/product-610-fields.json'
    snapshot=source.read_bytes();(root/'snapshot.json').write_bytes(snapshot)
    policy=review.profile();manifest={'generated_at':'2026-10-27T06:50:47+00:00','source_snapshot':deepcopy(policy['source_snapshot']), 'assets':[], 'numeric_and_barcode_candidates':[]}
    by_url={}
    for index,asset in enumerate(policy['assets']):
        im=Image.new('RGB',(index+2,2),(index*23,19,37));buf=BytesIO()
        im.save(buf,format=asset['decoded_format']);raw=buf.getvalue();h=sha256(raw).hexdigest()
        ext='.png' if asset['decoded_format']=='PNG' else '.webp';name=h+ext
        (images/name).write_bytes(raw)
        metadata=dump_json({'source_url':asset['source_asset_url'],'sha256':h,'source_product_id':'610'}).encode()
        metadata_name='synthetic-download-'+h+'.json';(images/metadata_name).write_bytes(metadata)
        fields=[r['expected_fact']['source_label'] for r in policy['facts'] if asset['source_asset_url'] in r['expected_fact']['evidence_urls']]
        a={'source_asset_url':asset['source_asset_url'],'local_content_addressed_file':name,'sha256':h,'bytes':len(raw),**review.decode_image(raw),
           'metadata_file':metadata_name,'metadata_sha256':sha256(metadata).hexdigest(),'source_page_url':policy['source_url'],
           'download_observed_at':'2026-10-27T06:39:34Z','retrieval_method_reported':'synthetic_test_only_no_download',
           'linked_fields_from_frozen_snapshot':fields or ['package_identification'],'pixel_review_method':'synthetic_test_only_no_visual_claim'}
        manifest['assets'].append(a);by_url[a['source_asset_url']]=a
    for rule in policy['facts']:
        fact=rule['expected_fact'];asset=by_url[fact['evidence_urls'][0]]
        manifest['numeric_and_barcode_candidates'].append({'source_product_id':'610','source_label':fact['source_label'],
          'target_field':fact.get('kind',rule['selector']),'evidence_source_asset_url':asset['source_asset_url'],'evidence_sha256':asset['sha256'],
          'image_display_text':fact['raw_value'],'candidate_value':fact['value'],'unit_printed_on_image':fact['raw_unit'],
          'field_context_expected_unit':fact['expected_unit'],'unit_basis':rule['unit_basis'],'source_quality':'source_reported_not_manufacturer_verified',
          'source_snapshot_sha256':policy['source_snapshot']['sha256'],'source_snapshot_observed_at':policy['source_snapshot']['observed_at'],
          'evidence_variant_index':rule['evidence_variant_index'],'evidence_variant_count':rule['evidence_variant_count']})
    # reviewed_product emits barcode variants in candidate order; machine policy
    # intentionally lists measurements first and barcode slots in observed order.
    raw=(dump_json(manifest)+'\n').encode();(root/'review-manifest.json').write_bytes(raw)
    store=Store(root/'temporary-state')
    try:
        import_snapshot(store,root/'snapshot.json')
        import_snapshot(store,Path(__file__).parent/'fixtures/browser-product-3358.json')
        store.pause('source_execution_not_authorized_for_offline_import')
        export_checkpoint(store,root/'temporary-checkpoint')
        baseline=(root/'temporary-checkpoint/checkpoint.json').read_bytes()
        (root/'baseline-checkpoint.json').write_bytes(baseline)
    finally:store.close()
    return manifest,raw,policy


def policy_for(manifest, policy, manifest_sha):
    policy=deepcopy(policy);policy['private_manifest_sha256']=manifest_sha
    for rule in policy['facts']:
        fact=rule['expected_fact'];key=fact.get('kind',rule['selector'])
        candidate=next(c for c in manifest['numeric_and_barcode_candidates'] if c['target_field']==key and c['evidence_variant_index']==rule['evidence_variant_index'])
        provenance=review.review_provenance(candidate,manifest)
        rule['image_review_sha256']=review.digest(dump_json(provenance).encode())
    return policy
