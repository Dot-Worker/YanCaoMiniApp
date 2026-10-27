"""Offline adapter for authorized, already-loaded public product field snapshots.

This module never connects to a browser or source. Operator-supplied identity and
observation metadata are attestations, not proof that arbitrary JSON is genuine.
"""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from .dom import Node
from .parser import BARCODES, MEASUREMENTS, TEXT_FIELDS, product_record
from .policy import PolicyError, canonical_url, utcnow
from .schema import validate

MAX_SNAPSHOT_BYTES = 64 * 1024
ACCESS_METHOD = "imported_loaded_browser_field_snapshot"


def _blocked():
    raise PolicyError("Snapshot does not satisfy the reviewed field-only import contract")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _blocked()
        result[key] = value
    return result


def read_snapshot(path):
    """Read one bounded regular file, rejecting symlinks/FIFOs and duplicate keys."""
    path = Path(path)
    if path.is_symlink():
        _blocked()
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            _blocked()
        raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        _blocked()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=lambda _: _blocked())
    except (UnicodeError, ValueError, RecursionError):
        _blocked()
    return value, sha256(raw).hexdigest()


def _text(value, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > 256 or (not value and not allow_empty):
        _blocked()
    # Field snapshots are plain scalars, never an HTML/comment/URL transport.
    if value != value.strip() or re.search(r"[<>\x00-\x1f\x7f]|https?://|www\.|(?:cookie|session|token|password)\s*[=:]", value, re.I):
        _blocked()
    return value


def _asset(url, *, numeric):
    if not isinstance(url, str) or len(url) > 500 or canonical_url(url, asset=True) != url:
        _blocked()
    if ("/thumb/genpic/" in url) != numeric:
        _blocked()
    return url


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?(?:Z|\+00:00)", value):
        _blocked()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _blocked()
    if stamp > datetime.now(timezone.utc):
        _blocked()
    return value.replace("Z", "+00:00")


def parse_snapshot(value, *, source_url, source_id, name, observed_at, input_sha256, input_format="loaded_browser_product_fields_v1"):
    """Convert strict field JSON via the existing shared product fact constructor."""
    if not isinstance(source_id, str) or not re.fullmatch(r"[1-9][0-9]{0,12}", source_id):
        _blocked()
    if not isinstance(source_url, str) or source_url != "https://www.yanyue.cn/product/" + source_id or canonical_url(source_url) != source_url:
        _blocked()
    if input_format not in {"loaded_browser_product_fields_v1", "loaded_browser_product_envelope_v1"}:
        _blocked()
    name = _text(name)
    if re.search(r"价格|多少钱|评论|评测|购买|优惠|促销", name):
        _blocked()  # A product name is required, not the page's commerce SEO title.
    observed_at = _timestamp(observed_at)
    if not isinstance(input_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
        _blocked()
    if not isinstance(value, dict) or set(value) != {"fields", "photo"}:
        _blocked()
    rows = value["fields"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= len(TEXT_FIELDS) + len(MEASUREMENTS) + len(BARCODES):
        _blocked()
    pairs = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"label", "text", "images"}:
            _blocked()
        label = row["label"]
        if not isinstance(label, str):
            _blocked()
        label = label.rstrip(":：")
        if label not in TEXT_FIELDS.keys() | MEASUREMENTS.keys() | BARCODES.keys() or label in pairs:
            _blocked()  # Reject, not silently retain/drop commerce/comments/unknown fields.
        text = _text(row["text"], allow_empty=True)
        urls = row["images"]
        if not isinstance(urls, list) or len(urls) > 8:
            _blocked()
        if label in TEXT_FIELDS:
            if urls:
                _blocked()
        elif text and not (label in BARCODES and text == "更多" and urls):
            _blocked()  # No OCR, numeric guesses, manually asserted values or barcode text.
        checked = [_asset(url, numeric=True) for url in urls]
        if len(set(checked)) != len(checked):
            _blocked()
        node = Node("span", children=[text])
        node.children.extend(Node("img", {"src": url}, parent=node) for url in checked)
        pairs[label] = node
    if not all(label in pairs and pairs[label].text for label in ("品牌", "类型")):
        _blocked()
    images = []
    if value["photo"] is not None:
        asset = _asset(value["photo"], numeric=False)
        images.append({"source_asset_url": asset, "source_page_url": source_url, "purpose": "package_identification",
                       "author_credit": None, "rights_status": "user_asserted_full_authorization",
                       "rights_note": "User declared full authorization for relevant third-party images and original text; not independently verified per author.",
                       "code_license_applies": False, "allow_derivatives": None, "publication_status": "quarantined_pending_review"})
    result = product_record(name=name, aliases=[], pairs=pairs, images=images, url=source_url,
                            observed_at=observed_at, access_method=ACCESS_METHOD)
    result.update(source_access_method=ACCESS_METHOD, source_content_sha256=None, source_time_precision="millisecond" if "." in observed_at else "second")
    result["product"]["snapshot_provenance"] = {
        "input_format": input_format, "input_sha256": input_sha256,
        "source_host": urlsplit(source_url).hostname, "source_time_precision": "millisecond" if "." in observed_at else "second",
        "imported_at": utcnow(), "identity_metadata_basis": "operator_attested_from_loaded_page",
        "original_html_sha256": None, "network_requests_during_import": 0,
    }
    validate(result["product"], "product.schema.json")
    return result


def import_snapshot(store, filename, **metadata):
    value, digest = read_snapshot(filename)
    metadata = {k: v for k, v in metadata.items() if v is not None}
    input_format = "loaded_browser_product_fields_v1"
    if isinstance(value, dict) and set(value) == {"source_url", "product_name", "observed_at", "snapshot"}:
        source_url = value["source_url"]
        if not isinstance(source_url, str) or not re.fullmatch(r"https://www\.yanyue\.cn/product/[1-9][0-9]{0,12}", source_url):
            _blocked()
        embedded = {"source_url": source_url, "source_id": source_url.rsplit("/", 1)[1],
                    "name": value["product_name"], "observed_at": value["observed_at"]}
        for key, supplied in metadata.items():
            if key not in embedded or (key == "observed_at" and _timestamp(supplied) != _timestamp(embedded[key])) or (key != "observed_at" and supplied != embedded[key]):
                _blocked()
        metadata = embedded
        value = value["snapshot"]
        input_format = "loaded_browser_product_envelope_v1"
    if set(metadata) != {"source_url", "source_id", "name", "observed_at"}:
        _blocked()
    result = parse_snapshot(value, input_sha256=digest, input_format=input_format, **metadata)
    product = result["product"]
    existing = store.db.execute("SELECT document FROM products WHERE source_id=?", (product["source_id"],)).fetchone()
    already = False
    if existing:
        old = json.loads(existing[0])
        # An unchanged re-import is idempotent; never replace a different last-good record.
        proposed = json.loads(json.dumps(product))
        if "snapshot_provenance" in old:
            proposed["snapshot_provenance"]["imported_at"] = old["snapshot_provenance"]["imported_at"]
        if proposed != old:
            raise PolicyError("Existing product requires reviewed reconciliation; last-good record retained")
        already = True
    else:
        # No queue insertion/completion, source attempt, pause reset or network budget update.
        store.save_page(result, update_job=False)
    return {"imported_products": 0 if already else 1, "already_imported": already, "source_id": product["source_id"],
            "source_url": product["source_url"], "observed_at": product["observed_at"],
            "network_executed": False, "source_requests": 0, "images_downloaded": 0,
            "original_html_hash_available": False, "input_sha256": digest,
            "source_jobs_updated": False, "coverage": store.coverage()}


def browser_index_status(store, filename):
    """Validate an observed single-brand index and compare with stored records.

Read-only: do not turn index links into fake detail records or completed jobs.
The input file remains the durable source of this aggregate index evidence.
"""
    value, digest = read_snapshot(filename)
    expected_keys = {"observed_date", "brand_id", "method", "scope", "expected_records", "pages", "products"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        _blocked()
    if value["method"] != "current_cloud_browser_rendered_dom" or value["scope"] != "single_brand_index_only":
        _blocked()
    if type(value["brand_id"]) is not int or not 1 <= value["brand_id"] <= 10**13-1:
        _blocked()
    if type(value["expected_records"]) is not int or not 0 <= value["expected_records"] <= 5000:
        _blocked()
    if type(value["pages"]) is not int or not 1 <= value["pages"] <= 500:
        _blocked()
    observed_date = value["observed_date"]
    if not isinstance(observed_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed_date):
        _blocked()
    try:
        date = datetime.strptime(observed_date, "%Y-%m-%d").date()
    except ValueError:
        _blocked()
    if date > datetime.now(timezone.utc).date():
        _blocked()
    products = value["products"]
    if not isinstance(products, list) or len(products) != value["expected_records"]:
        _blocked()
    ids = set()
    for item in products:
        if not isinstance(item, dict) or set(item) != {"name", "url"}:
            _blocked()
        _text(item["name"])
        url = item["url"]
        if not isinstance(url, str) or not re.fullmatch(r"https://www\.yanyue\.cn/product/[1-9][0-9]{0,12}", url) or canonical_url(url) != url:
            _blocked()
        source_id = url.rsplit("/", 1)[1]
        if source_id in ids:
            _blocked()
        ids.add(source_id)
    stored = {row[0] for row in store.db.execute("SELECT source_id FROM products")} & ids
    return {"scope": "single_brand_index_only", "brand_source_id": str(value["brand_id"]),
            "observed_date": observed_date, "source_time_precision": "day", "input_sha256": digest,
            "operator_reported_listing_pages_observed": value["pages"], "operator_reported_expected_records": value["expected_records"],
            "unique_indexed_products": len(ids), "stored_detail_records": len(stored),
            "stored_detail_source_ids": sorted(stored, key=int), "indexed_products_without_detail_records": len(ids-stored),
            "network_executed": False, "source_jobs_updated": False, "index_stored_in_database": False,
            "whole_website_complete": False, "publication_ready": False}
