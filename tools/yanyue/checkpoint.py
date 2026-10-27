"""Offline, strict JSON-only artifact export/restore; never browser/storage state."""
from pathlib import Path
from contextlib import nullcontext
import json
import os
import re
from .policy import PolicyError, utcnow
from .schema import validate
from .store import atomic_json, dump_json

MAX_CHECKPOINT_BYTES = 20 * 1024 * 1024
AUTHORIZATION = {"status": "user_asserted_full_authorization", "scope": "relevant_third_party_images_and_original_encyclopedia_text", "independently_verified": False, "code_license_applies": False}


def export_checkpoint(store, output):
    directory = Path(output)
    if directory.is_symlink() or (directory.exists() and any(directory.iterdir())):
        raise PolicyError("Checkpoint output must be a new or empty non-symlink directory")
    checkpoint = checkpoint_value(store)
    from .reviewed_images import has_review, table_exists
    if any(has_review(p) for p in checkpoint["products"]) or table_exists(store):
        raise PolicyError("Reviewed image state requires an explicit evidence bundle")
    validate(checkpoint, "checkpoint.schema.json")
    if len(dump_json(checkpoint).encode()) + 1 > MAX_CHECKPOINT_BYTES:
        raise PolicyError("Checkpoint exceeds the 20 MiB expanded artifact limit")
    atomic_json(directory / "checkpoint.json", checkpoint)
    return {"output": str(directory / "checkpoint.json"), "products": len(checkpoint["products"]), "queue": len(checkpoint["queue"]), "images_included": False}


def checkpoint_value(store):
    jobs = []
    for row in store.db.execute("SELECT * FROM queue ORDER BY url"):
        job = {k: row[k] for k in ("url", "kind", "status", "attempts", "error_code", "observed_at", "source_content_sha256", "observed_precision")}
        job["metadata"] = json.loads(row["metadata"])
        jobs.append(job)
    commit = os.environ.get("GITHUB_SHA")
    if commit and not re.fullmatch("[0-9a-f]{40}", commit):
        raise PolicyError("Invalid source commit metadata")
    checkpoint = {"schema_version": "1.0", "created_at": utcnow(), "source_commit": commit,
                  "authorization": AUTHORIZATION, "queue": jobs,
                  "edges": [dict(r) for r in store.db.execute("SELECT parent_url,child_url FROM edges ORDER BY parent_url,child_url")],
                  "products": [json.loads(r[0]) for r in store.db.execute("SELECT document FROM products ORDER BY source_id")],
                  "brands": [json.loads(r[0]) for r in store.db.execute("SELECT document FROM brands ORDER BY source_id")],
                  "brand_reports": [json.loads(r[0]) for r in store.db.execute("SELECT document FROM brand_reports ORDER BY url")],
                  "pause": store.meta("pause"), "last_result": store.meta("last_result"), "budget_summary": store.meta("budget_summary"),
                  "images_included": False, "whole_website_complete": False, "publication_ready": False}
    catalog = store.db.execute("SELECT value FROM metadata WHERE key='catalog_evidence'").fetchone()
    if catalog is not None:
        checkpoint["catalog_evidence"] = json.loads(catalog[0])
    return checkpoint


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("Duplicate checkpoint JSON key")
        result[key] = value
    return result


def read_checkpoint(input_path):
    path = Path(input_path)
    if path.is_dir():
        if path.is_symlink() or {p.name for p in path.iterdir()} != {"checkpoint.json"}:
            raise PolicyError("Checkpoint directory may contain only checkpoint.json")
        path = path / "checkpoint.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CHECKPOINT_BYTES:
        raise PolicyError("Checkpoint must be a bounded regular JSON file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeError):
        raise PolicyError("Invalid checkpoint JSON") from None
    return validate_checkpoint_value(value)


def validate_checkpoint_value(value):
    validate(value, "checkpoint.schema.json")
    from .reviewed_images import has_review
    if any(has_review(p) for p in value["products"]):
        raise PolicyError("JSON-only checkpoint cannot restore reviewed image evidence")
    commit = os.environ.get("GITHUB_SHA")
    if commit and value["source_commit"] != commit:
        raise PolicyError("Checkpoint source commit does not match the current workflow commit")
    urls = {j["url"] for j in value["queue"]}
    from .catalog import catalog_edges
    explicit_memberships = catalog_edges(value)
    if len(urls) != len(value["queue"]) or any((e["parent_url"] not in urls or e["child_url"] not in urls) and (e["parent_url"], e["child_url"]) not in explicit_memberships for e in value["edges"]):
        raise PolicyError("Checkpoint has duplicate jobs or disconnected evidence edges")
    return value


def restore_checkpoint(store, input_path):
    return restore_checkpoint_value(store, read_checkpoint(input_path))


def restore_checkpoint_value(store, value, *, _transaction_owned=True):
    if not _transaction_owned and not store.db.in_transaction:
        raise PolicyError("Shared restore requires an active transaction")
    validate_checkpoint_value(value)
    if store.db.execute("SELECT count(*) FROM queue").fetchone()[0] or store.db.execute("SELECT count(*) FROM products").fetchone()[0]:
        raise PolicyError("Restore requires an empty state directory; never overwrite last-good state")
    with (store.db if _transaction_owned else nullcontext()):
        for job in value["queue"]:
            if job["url"] != job["metadata"]["url"] or job["kind"] != job["metadata"]["kind"]:
                raise PolicyError("Checkpoint job metadata does not match its identity")
            store.enqueue(job["metadata"])
            store.db.execute("UPDATE queue SET status=?,attempts=?,error_code=?,observed_at=?,source_content_sha256=?,observed_precision=? WHERE url=?", (job["status"], job["attempts"], job["error_code"], job["observed_at"], job["source_content_sha256"], job["observed_precision"], job["url"]))
        store.db.executemany("INSERT INTO edges VALUES (?,?)", [(e["parent_url"], e["child_url"]) for e in value["edges"]])
        for table in ("products", "brands"):
            for document in value[table]:
                store.db.execute(f"INSERT INTO {table} VALUES (?,?)", (document["source_id"], dump_json(document)))
        for report in value["brand_reports"]:
            store.db.execute("INSERT INTO brand_reports VALUES (?,?)", (report["source_url"], dump_json(report)))
        for key in ("pause", "last_result", "budget_summary"):
            if value[key] is not None:
                store.db.execute("INSERT INTO metadata VALUES (?,?)", (key, dump_json(value[key])))
        if "catalog_evidence" in value:
            store.db.execute("INSERT INTO metadata VALUES (?,?)", ("catalog_evidence", dump_json(value["catalog_evidence"])))
        # Third-party image binaries never travel in this checkpoint. Re-create
        # only the safe pending evidence queue from the validated product records.
        for p in value["products"]:
            assets = list(p["images"])
            for fact in list(p["measurements"].values()) + p["barcodes"]:
                assets += [{"source_asset_url": u, "source_page_url": p["source_url"], "purpose": "fact_evidence", "publication_status": "quarantined_pending_review", "rights_status": "user_asserted_full_authorization", "code_license_applies": False} for u in fact["evidence_urls"]]
            for asset in assets:
                store.db.execute("INSERT OR IGNORE INTO assets(url,document) VALUES (?,?)", (asset["source_asset_url"], dump_json(asset)))
    return store.coverage()
