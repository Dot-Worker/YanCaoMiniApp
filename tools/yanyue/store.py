"""Durable local queue and last-good data. No raw HTML, cookies or credentials."""
from __future__ import annotations
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import fcntl
from .policy import ORIGIN, PolicyError, canonical_url, utcnow


def dump_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        f.write(dump_json(value) + "\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "state.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',metadata TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,error_code TEXT,observed_at TEXT,cache_path TEXT);
        CREATE TABLE IF NOT EXISTS edges(parent_url TEXT NOT NULL,child_url TEXT NOT NULL,PRIMARY KEY(parent_url,child_url));
        CREATE TABLE IF NOT EXISTS products(source_id TEXT PRIMARY KEY,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS brands(source_id TEXT PRIMARY KEY,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(url TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT 'pending',document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS brand_reports(url TEXT PRIMARY KEY,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,document TEXT NOT NULL);
        ''')
        if "source_content_sha256" not in {r[1] for r in self.db.execute("PRAGMA table_info(queue)")}:
            self.db.execute("ALTER TABLE queue ADD COLUMN source_content_sha256 TEXT")
        if "observed_precision" not in {r[1] for r in self.db.execute("PRAGMA table_info(queue)")}:
            self.db.execute("ALTER TABLE queue ADD COLUMN observed_precision TEXT")
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def exclusive(self):
        lock = (self.root / "worker.lock").open("a+")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PolicyError("Another worker holds this state directory")
            yield
        finally:
            lock.close()

    def put_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, dump_json(value)))
        self.db.commit()

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def enqueue(self, item, parent=None):
        url = canonical_url(item["url"])
        kind = item["kind"]
        expected = "index" if url.endswith("/tobacco") else "product" if "/product/" in url else "brand"
        if kind != expected:
            raise PolicyError("Queue kind does not match canonical URL")
        self.db.execute("INSERT OR IGNORE INTO queue(url,kind,metadata) VALUES (?,?,?)", (url, kind, dump_json(item)))
        if parent:
            self.db.execute("INSERT OR IGNORE INTO edges VALUES (?,?)", (canonical_url(parent), url))

    def seed(self):
        self.enqueue({"url": ORIGIN + "/tobacco", "kind": "index"})
        self.db.commit()

    def next(self, phase="all", only_brand=None):
        kinds = ("index", "brand") if phase == "discover" else ("product",) if phase == "products" else ("index", "brand", "product")
        rows = self.db.execute("SELECT * FROM queue WHERE status='pending' AND kind IN (%s) ORDER BY CASE kind WHEN 'index' THEN 0 WHEN 'brand' THEN 1 ELSE 2 END,rowid" % ",".join("?" * len(kinds)), kinds)
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            brand = item["metadata"].get("brand_source_id") or item["metadata"].get("brand", {}).get("source_id")
            if only_brand is None or brand == only_brand:
                return item
        return None

    def attempted(self, url):
        self.db.execute("UPDATE queue SET attempts=attempts+1 WHERE url=?", (url,))
        self.db.commit()

    def save_page(self, result):
        url = canonical_url(result["source_url"])
        body_hash = sha256(dump_json(result).encode()).hexdigest()
        url_hash = sha256(url.encode()).hexdigest()
        relative = f"cache/{url_hash}/{body_hash}.json"
        atomic_json(self.root / relative, result)
        with self.db:
            for item in result["discovered"]:
                self.enqueue(item, parent=url)
            for brand in result.get("brands", []):
                existing = self.db.execute("SELECT document FROM brands WHERE source_id=?", (brand["source_id"],)).fetchone()
                if existing:
                    brand["source_groups"] = sorted(set(brand["source_groups"]) | set(json.loads(existing[0])["source_groups"]))
                self.db.execute("INSERT OR REPLACE INTO brands VALUES (?,?)", (brand["source_id"], dump_json(brand)))
            if result["kind"] == "brand":
                report = {k: result[k] for k in ("source_url", "brand_source_id", "brand_name", "reported_product_count", "reported_page_count", "observed_page", "observed_page_items", "observed_at")}
                self.db.execute("INSERT OR REPLACE INTO brand_reports VALUES (?,?)", (url, dump_json(report)))
            product = result.get("product")
            if product:
                from .schema import validate
                validate(product, "product.schema.json")
                self.db.execute("INSERT OR REPLACE INTO products VALUES (?,?)", (product["source_id"], dump_json(product)))
                assets = list(product["images"])
                for field in list(product["measurements"].values()) + product["barcodes"]:
                    assets += [{"source_asset_url": u, "source_page_url": url, "purpose": "fact_evidence", "publication_status": "quarantined_pending_review", "rights_status": "user_asserted_full_authorization", "code_license_applies": False} for u in field["evidence_urls"]]
                for asset in assets:
                    asset_url = canonical_url(asset["source_asset_url"], asset=True)
                    # One URL may evidence multiple fields; per-field linkage stays in product.
                    self.db.execute("INSERT OR IGNORE INTO assets(url,document) VALUES (?,?)", (asset_url, dump_json(asset)))
            self.db.execute("UPDATE queue SET status='complete',error_code=NULL,observed_at=?,cache_path=?,source_content_sha256=?,observed_precision=? WHERE url=?", (result["observed_at"], relative, result.get("source_content_sha256"), result.get("source_time_precision", "second"), url))

    def fail(self, url, code, *, pause=False):
        self.db.execute("UPDATE queue SET status=?,error_code=? WHERE url=?", ("paused" if pause else "failed", code, url))
        self.db.commit()

    def pause(self, reason, retry_after=None):
        self.put_meta("pause", {"reason": reason, "retry_after": retry_after, "paused_at": utcnow()})

    def clear_pause(self):
        pause = self.meta("pause")
        if pause and pause.get("retry_after") and pause["retry_after"] > utcnow():
            raise PolicyError("Retry-After has not elapsed")
        self.db.execute("DELETE FROM metadata WHERE key='pause'")
        self.db.execute("UPDATE queue SET status='pending' WHERE status='paused'")
        self.db.commit()

    def coverage(self):
        by_kind = {}
        for row in self.db.execute("SELECT kind,status,count(*) AS n FROM queue GROUP BY kind,status"):
            by_kind.setdefault(row["kind"], {})[row["status"]] = row["n"]
        for counts in by_kind.values():
            counts["discovered"] = sum(counts.values())
        assets = {r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM assets GROUP BY status")}
        brand_counts = []
        reports = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM brand_reports")]
        for brand_id in sorted({r["brand_source_id"] for r in reports}):
            pages = [r for r in reports if r["brand_source_id"] == brand_id]
            expected = sorted({r["reported_product_count"] for r in pages if r["reported_product_count"] is not None})
            source_pages = {r["source_url"] for r in pages}
            discovered_products = {r[0] for r in self.db.execute("SELECT child_url FROM edges WHERE parent_url IN (%s)" % ",".join("?" * len(source_pages)), tuple(source_pages)) if "/product/" in r[0]}
            outstanding = 0
            for row in self.db.execute("SELECT url,status,metadata FROM queue WHERE kind='brand'"):
                meta = json.loads(row["metadata"])
                if (meta.get("brand_source_id") or meta.get("brand", {}).get("source_id") or row["url"].rsplit("/", 1)[-1]) == brand_id and row["status"] != "complete":
                    outstanding += 1
            brand_counts.append({"source_id": brand_id, "source_reported_product_counts": expected, "unique_product_links_discovered": len(discovered_products), "listing_pages_outstanding": outstanding, "count_mismatch": bool(len(expected) != 1 or (not outstanding and len(discovered_products) != expected[0]))})
        return {"schema_version": "1.0", "scope": "observed_public_traditional_tobacco_catalog_links_only", "whole_website_complete": False,
                "queue": by_kind, "brand_counts": brand_counts, "brands_observed": self.db.execute("SELECT count(*) FROM brands").fetchone()[0],
                "products_parsed": self.db.execute("SELECT count(*) FROM products").fetchone()[0], "assets": assets,
                "pause": self.meta("pause"), "source_reported_not_manufacturer_certified": True,
                "field_review_complete": False, "publication_ready": False}
