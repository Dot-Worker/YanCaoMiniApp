"""Strict schemas for a reviewable artifact boundary (never raw browser state)."""
from pathlib import Path
import math
from datetime import datetime
from urllib.parse import urlsplit
import json
from .policy import PolicyError, canonical_url

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "data"


def validate(value, filename):
    try:
        import jsonschema
    except ImportError:
        raise PolicyError("Install tools/requirements-test.txt for schema validation") from None
    schema = json.loads((SCHEMA_ROOT / filename).read_text())
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError:
        # Do not leak source text, source URL query values or arbitrary checkpoint payloads.
        raise PolicyError("Artifact does not satisfy the strict data contract") from None
    check_urls(value)
    validate_semantics(value, filename)


def check_urls(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise PolicyError("Nonfinite numeric value")
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"source_url", "source_page_url", "url", "parent_url", "child_url"} and child is not None:
                if canonical_url(child) != child:
                    raise PolicyError("Noncanonical source URL")
            elif key == "source_asset_url" and child is not None:
                if canonical_url(child, asset=True) != child:
                    raise PolicyError("Noncanonical asset URL")
            elif key == "evidence_urls":
                for url in child:
                    if canonical_url(url, asset=True) != url:
                        raise PolicyError("Noncanonical evidence URL")
            else:
                check_urls(child)
    elif isinstance(value, list):
        for child in value:
            check_urls(child)


def validate_semantics(value, filename):
    products = [value] if filename == "product.schema.json" else value.get("products", [])
    for p in products:
        expected_url = "https://www.yanyue.cn/product/" + p["source_id"]
        if p["id"] != "yanyue:" + p["source_id"] or p["source_url"] != expected_url:
            raise PolicyError("Product identity mismatch")
        for fact in list(p["fields"].values()) + list(p["measurements"].values()) + p["barcodes"]:
            if fact["evidence_urls"] and fact["value"] is not None:
                raise PolicyError("Image evidence has not passed the implemented manual-review import gate")
            if fact["source_url"] != expected_url:
                raise PolicyError("Field evidence is attached to the wrong product")
        if p["source_access_method"] == "imported_loaded_browser_field_snapshot":
            provenance = p["snapshot_provenance"]
            if provenance["source_host"] != urlsplit(expected_url).hostname:
                raise PolicyError("Snapshot source host mismatch")
            if provenance["source_time_precision"] != ("millisecond" if "." in p["observed_at"] else "second"):
                raise PolicyError("Snapshot time precision mismatch")
            try:
                datetime.fromisoformat(p["observed_at"])
                datetime.fromisoformat(provenance["imported_at"])
            except ValueError:
                raise PolicyError("Invalid snapshot observation date") from None
            for fact in list(p["fields"].values()) + list(p["measurements"].values()) + p["barcodes"]:
                if fact["observed_at"] != p["observed_at"]:
                    raise PolicyError("Snapshot observation date mismatch")
            for fact in list(p["measurements"].values()) + p["barcodes"]:
                if fact["value"] is not None or fact["raw_value"] is not None or fact["raw_unit"] is not None:
                    raise PolicyError("Snapshot numeric evidence has not been reviewed")
                expected = ("needs_review", "image_not_read") if fact["evidence_urls"] else ("missing", "not_available")
                if (fact["verification_status"], fact["extraction_method"]) != expected:
                    raise PolicyError("Snapshot numeric review state mismatch")
        if p["original_text"] is not None:
            raise PolicyError("Original text awaits an observed scoped parser and review")
        for image in p["images"]:
            if image["source_page_url"] != expected_url:
                raise PolicyError("Image evidence is attached to the wrong product")
    if filename != "checkpoint.schema.json":
        return
    product_urls = {p["source_url"] for p in products}
    if len(product_urls) != len(products):
        raise PolicyError("Duplicate product identities")
    jobs = value["queue"]
    for job in jobs:
        if job["url"] != job["metadata"]["url"] or job["kind"] != job["metadata"]["kind"]:
            raise PolicyError("Queue identity mismatch")
        expected = "index" if job["url"].endswith("/tobacco") else "product" if "/product/" in job["url"] else "brand"
        if job["kind"] != expected:
            raise PolicyError("Queue kind does not match source path")
        if job["kind"] == "product" and job["status"] == "complete" and job["url"] not in product_urls:
            raise PolicyError("Completed product has no last-good record")
    for brand in value["brands"]:
        if brand["source_url"] != "https://www.yanyue.cn/sort/" + brand["source_id"]:
            raise PolicyError("Brand identity mismatch")
    budget = value["budget_summary"]
    if budget and (budget["requests"] > budget["max_requests"] or budget["bytes"] > budget["max_bytes"]):
        raise PolicyError("Budget counters exceed run limits")
