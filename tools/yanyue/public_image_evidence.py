"""Read-only verification of published product610 rules and original bytes.

Not an import gate: no OCR, reconstructed private provenance, state or network.
"""
from __future__ import annotations
from copy import deepcopy
import os
from .policy import PolicyError, canonical_url
from . import reviewed_images as reviewed
from .snapshot_import import parse_snapshot, MAX_SNAPSHOT_BYTES
from .store import dump_json


def verify_public_evidence(snapshot_path, image_directory):
    rules = reviewed.profile()  # Pinned public policy, never the private manifest.
    raw = reviewed.read_file(snapshot_path, MAX_SNAPSHOT_BYTES)
    if reviewed.digest(raw) != rules['source_snapshot']['sha256']:
        reviewed.blocked('Public source snapshot hash mismatch')
    snap = reviewed.loads(raw)
    if (set(snap) != {'source_url', 'product_name', 'observed_at', 'snapshot'} or
            snap['source_url'] != rules['source_url'] or
            snap['observed_at'] != rules['source_snapshot']['observed_at']):
        reviewed.blocked('Public source snapshot identity mismatch')
    original = parse_snapshot(snap['snapshot'], source_url=snap['source_url'], source_id=rules['source_id'],
        name=snap['product_name'], observed_at=snap['observed_at'], input_sha256=reviewed.digest(raw),
        input_format='loaded_browser_product_envelope_v1')['product']
    before = deepcopy(original)
    assets = {a['sha256']: a for a in rules['assets']}
    names = {a['local_content_addressed_file'] for a in rules['assets']}
    if len(assets) != 9 or len(names) != 9 or len(rules['facts']) != 9:
        reviewed.blocked('Public policy scope differs from the single-product fixture')
    all_urls = {im['source_asset_url'] for im in original['images']}
    for f in list(original['measurements'].values()) + original['barcodes']:
        all_urls.update(f['evidence_urls'])
    if {a['source_asset_url'] for a in assets.values()} != all_urls:
        reviewed.blocked('Public asset set does not match the source snapshot')
    verified_assets = []
    # Anchored descriptors reject path replacement, links, FIFOs and unbounded data.
    with reviewed.directory(image_directory) as fd:
        if set(os.listdir(fd)) != names:
            reviewed.blocked('Public image directory is incomplete or has unknown entries')
        for sha, a in sorted(assets.items()):
            ext = '.png' if a['decoded_format'] == 'PNG' else '.webp'
            if (a['local_content_addressed_file'] != sha + ext or
                    canonical_url(a['source_asset_url'], asset=True) != a['source_asset_url'] or
                    not 0 < a['bytes'] <= reviewed.MAX_IMAGE_BYTES):
                reviewed.blocked('Invalid public asset binding')
            body = reviewed.read_at(fd, a['local_content_addressed_file'], a['bytes'])
            if len(body) != a['bytes'] or reviewed.digest(body) != sha:
                reviewed.blocked('Public image bytes differ from the reviewed original')
            if reviewed.decode_image(body) != {k: a[k] for k in ('decoded_format', 'decoded_mime', 'width', 'height', 'frames', 'mode')}:
                reviewed.blocked('Public image decode differs from the reviewed original')
            verified_assets.append(deepcopy(a))
    slots = set()
    verified_facts = []
    barcode_by_kind = {f['kind']: f for f in original['barcodes']}
    for rule in rules['facts']:
        slot = (rule['section'], rule['selector'])
        if slot in slots:
            reviewed.blocked('Repeated public fact slot')
        slots.add(slot)
        expected = rule['expected_fact']
        if rule['section'] == 'measurements':
            fact = original['measurements'][rule['selector']]
        elif rule['section'] == 'barcodes':
            fact = barcode_by_kind[expected['kind']]
        else:
            reviewed.blocked('Unsupported public fact section')
        index, count = rule['evidence_variant_index'], rule['evidence_variant_count']
        a = assets[rule['evidence_sha256']]
        if (type(index) is not int or type(count) is not int or not 0 <= index < count or
                len(fact['evidence_urls']) != count or fact['evidence_urls'][index] != a['source_asset_url'] or
                expected['evidence_urls'] != [a['source_asset_url']]):
            reviewed.blocked('Public evidence variant does not match its source field')
        for key in ('source_label', 'source_url', 'observed_at', 'expected_unit'):
            if fact[key] != expected[key]:
                reviewed.blocked('Public fact context differs from its source field')
        if (fact['value'] is not None or fact['raw_value'] is not None or fact['raw_unit'] is not None or
                (fact['verification_status'], fact['extraction_method']) != ('needs_review', 'image_not_read')):
            reviewed.blocked('Public source fixture is not the original unread record')
        basis = rule['unit_basis']
        if ((basis == 'visible_image_text' and (expected['raw_unit'] is None or expected['raw_unit'] != expected['expected_unit'])) or
                (basis in {'field_label_context_only', 'not_applicable'} and expected['raw_unit'] is not None) or
                basis not in {'visible_image_text', 'field_label_context_only', 'not_applicable'}):
            reviewed.blocked('Public unit basis is inconsistent')
        verified_facts.append({k: deepcopy(rule[k]) for k in ('section', 'selector', 'expected_fact', 'evidence_sha256',
                                'evidence_variant_index', 'evidence_variant_count', 'unit_basis')})
    if original != before:
        reviewed.blocked('Read-only verification changed the input record')
    return {'schema_version':'public_image_evidence_verification_1', 'source_id':rules['source_id'],
        'source_url':rules['source_url'], 'observed_at':snap['observed_at'],
        'verification_scope':'pinned_public_policy_source_bindings_and_local_original_bytes_only',
        'public_policy_sha256':reviewed.PUBLIC_PROFILE_SHA, 'source_snapshot_sha256':reviewed.digest(raw),
        'assets_verified':verified_assets, 'published_reviewed_facts':verified_facts,
        'source_quality':'source_reported_not_manufacturer_verified', 'manufacturer_verified':False,
        'new_visual_review_performed':False, 'private_review_provenance_validated':False,
        'writes_database':False, 'state_changed':False, 'source_requests':0, 'images_downloaded':0,
        'whole_website_complete':False, 'publication_ready':False}


def main(argv=None):
    import argparse
    class SafeArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            raise PolicyError('Invalid public evidence arguments')
    parser = SafeArgumentParser(description='Offline read-only verification; no database, download or import option')
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--images', required=True)
    try:
        args = parser.parse_args(argv)
        print(dump_json(verify_public_evidence(args.snapshot, args.images)))
        return 0
    except Exception:
        # Never log untrusted paths, payloads, OS exceptions or tracebacks.
        print(dump_json({'error':'public_image_evidence_rejected','source_requests':0,'state_changed':False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
