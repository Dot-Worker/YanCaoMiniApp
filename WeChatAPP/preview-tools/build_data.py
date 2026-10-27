#!/usr/bin/env python3
"""Deterministic, offline, allowlisted export of one explicitly reviewed snapshot.
No crawling, source fetches, database writes, OCR, or automatic field propagation.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

CATALOG_SHA = '8ed413ea54eed9bb311b7a0d48c2801497a03f6a089ea28994bdfa972a9ed7d8'
CHECKPOINT_SHA = '7919b95519ae925f2c556bc2bc4a3ada4f26ede7571ec31428356750bafed517'
IMAGE_MANIFEST_SHA = '423946df3bc973d6d93349ded43ac95e2ef4ee6d9497a2f37cbd56e6f3c4c15e'
FIELD_KEYS = ('value','raw_value','raw_unit','expected_unit','source_label','source_url','observed_at','verification_status','extraction_method','evidence_urls')
TEXT_KEYS = ('brand_name','type','package_form','cigarette_format','primary_color','secondary_color')
MEASURE_KEYS = ('tar_mg','nicotine_mg','carbon_monoxide_mg','length_mm','circumference_mm','filter_length_mm','sticks_per_pack','packs_per_carton')

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_pinned(path, expected):
    if sha(path) != expected:
        raise ValueError('Frozen input checksum mismatch: ' + Path(path).name)
    return json.loads(Path(path).read_text())

def project_field(field, assets, product_id):
    result = {k:field[k] for k in FIELD_KEYS if k in field}
    if 'image_review' in field:
        review=field['image_review']
        candidate=review['candidate']
        canonical=json.dumps(review,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        # Keep only machine provenance; the complete internal review remains private.
        result['review_provenance']={
            'schema_version':'public_field_evidence_v1',
            'source_product_id':candidate['source_product_id'],
            'target_field':candidate['target_field'],
            'evidence_sha256':candidate['evidence_sha256'],
            'evidence_source_asset_url':candidate['evidence_source_asset_url'],
            'source_snapshot_sha256':candidate['source_snapshot_sha256'],
            'source_snapshot_observed_at':candidate['source_snapshot_observed_at'],
            'unit_basis':candidate['unit_basis'],
            'evidence_variant_index':candidate['evidence_variant_index'],
            'evidence_variant_count':candidate['evidence_variant_count'],
            'reviewed_at':review['reviewed_at'],
            'source_quality':review['source_quality'],
            'image_review_sha256':hashlib.sha256(canonical).hexdigest(),
        }
    # Never attach a reused image or value to another product merely by URL equality.
    result['evidence_paths'] = [assets[u]['path'] for u in field.get('evidence_urls', []) if u in assets and assets[u]['productId'] == product_id]
    return result

def same_json(left, right):
    return json.dumps(left, sort_keys=True, separators=(',',':'), allow_nan=False) == json.dumps(right, sort_keys=True, separators=(',',':'), allow_nan=False)

def expected_reviewed_field(original, candidate, target, manifest):
    """Reconstruct the only permitted mutation; bind image evidence to field meaning."""
    require(candidate['target_field'] == target, 'Reviewed candidate target does not match its field key or barcode kind')
    require(candidate['source_product_id'] == '610', 'Unexpected reviewed product ID')
    require(candidate['source_label'] == original['source_label'], 'Source field label mismatch')
    require(candidate['field_context_expected_unit'] == original['expected_unit'], 'Source field context unit mismatch')
    require(candidate['evidence_source_asset_url'] in original['evidence_urls'], 'Evidence was not linked by the frozen field')
    expected=deepcopy(original)
    expected.update({
        'value':candidate['candidate_value'],
        'raw_value':candidate['image_display_text'],
        'raw_unit':candidate['unit_printed_on_image'],
        'verification_status':'manually_read_image',
        'extraction_method':'manual_image_review',
        'evidence_urls':[candidate['evidence_source_asset_url']],
        'image_review':{
            'candidate':deepcopy(candidate),
            'manifest_sha256':IMAGE_MANIFEST_SHA,
            'profile_id':'product610_independent_visual_review_20261027',
            'review_method':'independent_assistant_view_image_original_detail',
            'reviewed_at':manifest['generated_at'],
            'source_quality':candidate['source_quality'],
        },
    })
    return expected

def validate_reviewed_product(base, reviewed, manifest):
    """Require exact equality to a reconstruction from the two pinned inputs.

    This also rejects renamed/swapped fields, missing approved readings, new fields,
    altered text/image metadata, and forged review status on unapproved empty fields.
    """
    candidates=manifest['numeric_and_barcode_candidates']
    targets={c['target_field'] for c in candidates}
    require(len(candidates)==9 and targets=={'tar_mg','nicotine_mg','carbon_monoxide_mg','length_mm','sticks_per_pack','packs_per_carton','pack','carton'}, 'Unexpected fixed review target set')
    expected=deepcopy(base)
    for key,original in base['measurements'].items():
        matches=[c for c in candidates if c['target_field']==key]
        require(len(matches)<=1, 'Duplicate measurement candidate')
        if matches:
            expected['measurements'][key]=expected_reviewed_field(original,matches[0],key,manifest)
    barcodes=[]
    for original in base['barcodes']:
        kind=original['kind']
        matches=[c for c in candidates if c['target_field']==kind]
        require(len(matches)==(2 if kind=='pack' else 1), 'Unexpected barcode variant count')
        for candidate in matches:
            barcodes.append(expected_reviewed_field(original,candidate,kind,manifest))
    expected['barcodes']=barcodes
    require(same_json(reviewed,expected), 'Product610 differs from the exact approved field-bound reconstruction')
    return expected

def build(args):
    catalog=read_pinned(args.catalog, CATALOG_SHA)
    checkpoint=read_pinned(args.checkpoint, CHECKPOINT_SHA)
    require(len(catalog['brands']) == 522 and len(checkpoint['products']) == 82, 'Reviewed export invariant failed at validation 37')
    base={p['source_id']:p for p in checkpoint['products']}
    summary={p['source_id']:p for p in catalog['products']}
    require(set(base) == set(summary), 'Reviewed export invariant failed at validation 40')
    products=dict(base)
    asset_map={}
    assets=[]
    manifest=None
    if args.include_reviewed_images:
        if not args.image_application_audit_confirmed:
            raise ValueError('Independent image application audit must be confirmed explicitly')
        manifest=read_pinned(args.image_manifest,IMAGE_MANIFEST_SHA)
        require(manifest['independent_review_completed'] is True, 'Reviewed export invariant failed at validation 49')
        require(manifest['image_bytes_reencoded_or_modified'] is False, 'Reviewed export invariant failed at validation 50')
        require(manifest['reviewed82_checkpoint_sha256'] == CHECKPOINT_SHA, 'Reviewed export invariant failed at validation 51')
        require(not manifest['potential_sensitive_metadata_files'], 'Reviewed export invariant failed at validation 52')
        db_path=Path(args.reviewed_db).resolve()
        with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as connection:
            reviewed={sid:json.loads(doc) for sid,doc in connection.execute('SELECT source_id,document FROM products')}
        require(set(reviewed) == set(base), 'Reviewed export invariant failed at validation 56')
        for pid in base:
            if pid != '610':
                require(same_json(reviewed[pid],base[pid]), 'Unexpected change outside product610')
        reviewed610=validate_reviewed_product(base['610'],reviewed['610'],manifest)
        require(sum(v['value'] is not None for v in reviewed610['measurements'].values()) == 6, 'Reviewed export invariant failed at validation 61')
        require([(b['kind'],b['value']) for b in reviewed610['barcodes']] == [('pack','6901028184243'),('pack','6901028179058'),('carton','6901028184250')], 'Reviewed export invariant failed at validation 62')
        # Only fields authorized by the visual review may replace base fields.
        products['610']=dict(base['610'],measurements=reviewed610['measurements'],barcodes=reviewed610['barcodes'])
        for a in manifest['assets']:
            filename=a['local_content_addressed_file']
            require(re.fullmatch(r'[a-f0-9]{64}\.(png|webp)',filename), 'Reviewed export invariant failed at validation 67')
            require(a['source_product_id'] == '610', 'Reviewed export invariant failed at validation 68')
            path=Path(args.asset_dir)/filename
            require(sha(path) == a['sha256'] and path.stat().st_size == a['bytes'], 'Reviewed export invariant failed at validation 70')
            require(a['original_bytes_unchanged'] and a['full_decode_verified'], 'Reviewed export invariant failed at validation 71')
            require(a['metadata_safety_summary']['potential_sensitive_metadata_detected'] is False, 'Reviewed export invariant failed at validation 72')
            record={ 'path':'/assets/reviewed/'+filename, 'sha256':a['sha256'], 'bytes':a['bytes'], 'productId':'610', 'sourceAssetUrl':a['source_asset_url'], 'sourcePageUrl':a['source_page_url'], 'label':' / '.join(a['linked_fields_from_frozen_snapshot']), 'purpose':'package_identification' if 'package_identification' in a['linked_fields_from_frozen_snapshot'] else 'field_evidence', 'width':a['width'], 'height':a['height'], 'displayWidth':a['width']*2, 'displayHeight':a['height']*2, 'reviewDate':manifest['generated_at'][:10], 'rightsStatus':a['rights_status'], 'codeLicenseApplies':False }
            assets.append(record)
            asset_map[a['source_asset_url']]=record
        require(len(assets) == 9 and sum(a['bytes'] for a in assets) == 22656, 'Reviewed export invariant failed at validation 76')
        # Each reviewed non-null value must retain one matching, local evidence image.
        for field in list(products['610']['measurements'].values())+products['610']['barcodes']:
            if field['value'] is None:
                continue
            require(field['verification_status'] == 'manually_read_image', 'Reviewed export invariant failed at validation 81')
            review=field['image_review']
            require(review['manifest_sha256'] == IMAGE_MANIFEST_SHA, 'Reviewed export invariant failed at validation 83')
            evidence=review['candidate']
            require(evidence in manifest['numeric_and_barcode_candidates'], 'Field candidate is not in the pinned visual review manifest')
            require(field['raw_value'] == evidence['image_display_text'], 'Raw transcription does not match reviewed image text')
            require(field['raw_unit'] == evidence['unit_printed_on_image'], 'Raw unit differs from reviewed visible unit')
            require(field['expected_unit'] == evidence['field_context_expected_unit'], 'Expected unit differs from reviewed context')
            require(evidence['source_product_id'] == '610', 'Reviewed export invariant failed at validation 85')
            require(evidence['candidate_value'] == field['value'], 'Reviewed export invariant failed at validation 86')
            require(asset_map[evidence['evidence_source_asset_url']]['sha256'] == evidence['evidence_sha256'], 'Reviewed export invariant failed at validation 87')
    memberships={pid:[] for pid in base}
    for m in catalog['memberships']:
        if m['product_source_id'] in memberships:
            memberships[m['product_source_id']].append(m)
    brand_rows=[]
    for b in catalog['brands']:
        brand_rows.append({'id':b['source_id'],'name':b['name'],'englishName':None,'sourceUrl':b['source_url'],'availableCount':b['available_detail_records'],'sourceGroups':None,'sourceGroupsStatus':b['source_groups_status'],'listingSummary':b['listing_summary'],'identityEvidence':b['identity_evidence']})
    product_rows=[]
    for s in catalog['products']:
        p=products[s['source_id']]
        require(p['name'] == s['name'] and p['source_url'] == s['source_url'], 'Reviewed export invariant failed at validation 98')
        relations=memberships[p['source_id']]
        require(set(s['brand_source_ids']) == {m['brand_source_id'] for m in relations}, 'Reviewed export invariant failed at validation 100')
        local=[a for a in assets if a['productId'] == p['source_id']]
        main=[a for a in local if a['purpose'] == 'package_identification']
        product_rows.append({'id':p['source_id'],'name':p['name'],'brandIds':s['brand_source_ids'],'sourceUrl':p['source_url'],'observedAt':p['observed_at'],'manufacturerVerified':False,'sourceContributorType':p['source_contributor_type'],'detailSnapshotSha256':s['detail_snapshot_sha256'],'brandEvidence':relations,'fields':{k:project_field(p['fields'][k],asset_map,p['source_id']) for k in TEXT_KEYS},'measurements':{k:project_field(p['measurements'][k],asset_map,p['source_id']) for k in MEASURE_KEYS},'barcodes':[dict(project_field(b,asset_map,p['source_id']),kind=b['kind']) for b in p['barcodes']],'imagePath':main[0]['path'] if main else '', 'evidence':[a for a in local if a['purpose']=='field_evidence']})
    data={'schemaVersion':'local_preview_v1','snapshotDate':'2026-10-27','previewOnly':True,'wholeWebsiteComplete':False,'manufacturerVerified':False,'sourceContributorType':'user_contributed','stats':{'products':len(product_rows),'brands':len(brand_rows),'brandsWithDetails':sum(b['availableCount']>0 for b in brand_rows),'indexedRelationships':catalog['counts']['indexed_relationships'],'unreadIndexedRelationships':catalog['counts']['indexed_relationships_without_details'],'productsWithImage':sum(bool(p['imagePath']) for p in product_rows),'localImages':len(assets),'transcribedNumericFields':6 if assets else 0},'brands':brand_rows,'products':product_rows}
    # Reject private path leakage and forbidden source payload keys, not their negation in documentation.
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    require(not re.search(r'/workspace/|/home/|[A-Z]:\\|Bearer\s|gh[pousr]_',encoded), 'Reviewed export invariant failed at validation 107')
    for banned in ('price','prices','sales','rank','recommendation','original_text','description','cookie','token','password','image_review','candidate','candidate_status','review_method','profile_id'):
        require(not re.search(r'"'+banned+r'"\s*:',encoded,re.I), 'Reviewed export invariant failed at validation 109')
    out=Path(args.output)
    (out/'miniprogram/data').mkdir(parents=True,exist_ok=True)
    asset_out=out/'miniprogram/assets/reviewed'
    asset_out.mkdir(parents=True,exist_ok=True)
    for old in asset_out.iterdir():
        if old.is_file() and re.fullmatch(r'[a-f0-9]{64}\.(png|webp)',old.name): old.unlink()
    for a in assets:
        shutil.copyfile(Path(args.asset_dir)/Path(a['path']).name,asset_out/Path(a['path']).name)
    (out/'miniprogram/data/catalog.js').write_text('// Generated by preview-tools/build_data.py from pinned, reviewed offline inputs.\nmodule.exports = '+encoded+';\n')
    (out/'miniprogram/data/asset-manifest.json').write_text(json.dumps({'schemaVersion':'local_reviewed_assets_v1','assets':assets},ensure_ascii=False,indent=2)+'\n')
    provenance={'catalogSha256':CATALOG_SHA,'checkpointSha256':CHECKPOINT_SHA,'imageReviewManifestSha256':IMAGE_MANIFEST_SHA if assets else None,'scope':'frozen_82_product_local_preview','imageScope':'product610_only' if assets else 'not_included','networkCallsDuringExport':0,'privateAccountDataIncluded':False,'originalImageBytesPreserved':True,'noAutomaticSharedUrlPropagation':True,'publicationReady':False,'wechatDeveloperToolsRun':False,'physicalDeviceRun':False,'stats':data['stats']}
    (out/'miniprogram/data/provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'outputFiles':['miniprogram/data/catalog.js','miniprogram/data/asset-manifest.json','miniprogram/data/provenance.json'],'stats':data['stats'],'catalogJsSha256':sha(out/'miniprogram/data/catalog.js')},ensure_ascii=False,indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True)
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--output',default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument('--include-reviewed-images',action='store_true')
    parser.add_argument('--image-application-audit-confirmed',action='store_true')
    parser.add_argument('--image-manifest')
    parser.add_argument('--reviewed-db')
    parser.add_argument('--asset-dir')
    args=parser.parse_args()
    if args.include_reviewed_images and not all((args.image_manifest,args.reviewed_db,args.asset_dir)):
        parser.error('Image export requires manifest, reviewed database and asset directory')
    build(args)
