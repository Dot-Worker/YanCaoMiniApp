"""Offline, hash-pinned product610 image-review gate. No network/browser imports.

This deliberately admits ONE independently reviewed manifest, not arbitrary OCR or
operator-supplied values. A new review profile requires a separately reviewed code
change. JSON-only checkpoints cannot preserve this evidence; use explicit bundles.
"""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import fcntl
import os
from pathlib import Path
import re
import stat
import sqlite3
import warnings
from .policy import PolicyError, canonical_url
from .store import dump_json

PROFILE = "product610_independent_visual_review_20261027"
MANIFEST_SHA = "423946df3bc973d6d93349ded43ac95e2ef4ee6d9497a2f37cbd56e6f3c4c15e"
PUBLIC_PROFILE_SHA = "0c6be63aaaa0d8933e199208996a1ff252bfb19f3cb7cb54ef484d2083a488dc"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_JSON_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 24 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[0-9a-f]{64}\.(?:png|webp|json)")


def blocked(message="Image review evidence rejected"):
    # Never include untrusted values, filesystem paths or underlying OS errors.
    raise PolicyError(message)


def digest(raw):
    return sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            blocked("Duplicate JSON key")
        result[key] = value
    return result


def loads(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=lambda _: blocked("Nonfinite JSON"))
    except (UnicodeError, ValueError, RecursionError):
        blocked("Invalid evidence JSON")


@contextmanager
def directory(path):
    """Anchor every path component with openat/O_NOFOLLOW; no resolve/check-open race."""
    fd = None
    try:
        p = Path(path).absolute()
        if ".." in p.parts:
            blocked("Unsafe directory")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for part in p.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError:
        blocked("Unsafe or unavailable evidence directory")
    finally:
        if fd is not None:
            os.close(fd)


@contextmanager
def exclusive(store):
    """Use the existing lock name without following legacy lock-path links."""
    lockfd = None
    with directory(store.root) as rootfd:
        try:
            lockfd = os.open("worker.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=rootfd)
            info = os.fstat(lockfd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                blocked("Unsafe worker lock")
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except OSError:
            blocked("Unsafe or busy worker lock")
        finally:
            if lockfd is not None:
                os.close(lockfd)


def read_at(fd, name, limit):
    """Bounded single-descriptor read; only regular non-hardlinked input files."""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in {".", ".."}:
        blocked("Unsafe evidence filename")
    try:
        filefd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(filefd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                blocked("Evidence must be a bounded regular file")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > limit or len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                blocked("Evidence changed during read")
            return raw
    except OSError:
        blocked("Unsafe or missing evidence file")


def read_file(path, limit=MAX_JSON_BYTES):
    p = Path(path)
    with directory(p.parent) as fd:
        return read_at(fd, p.name, limit)


def write_at(fd, name, raw):
    """No replacement: an existing name must contain identical verified bytes."""
    if not NAME.fullmatch(name) or digest(raw) != name.split(".")[0]:
        blocked("Invalid content-addressed destination")
    try:
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    except FileExistsError:
        if read_at(fd, name, max(len(raw), 1)) != raw:
            blocked("Existing content-addressed file conflicts")
        return False
    except OSError:
        blocked("Cannot create evidence file")
    try:
        with os.fdopen(out, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    except OSError:
        # A partial, unreferenced file is not downloadable evidence; never overwrite it.
        blocked("Evidence write did not complete")
    return True


def child_directory(fd, name):
    try:
        os.mkdir(name, mode=0o700, dir_fd=fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    except OSError:
        blocked("Unsafe evidence destination")


def decode_image(raw):
    from PIL import Image
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        blocked("Image exceeds byte limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as im:
                if im.format not in {"PNG", "WEBP"} or im.width * im.height > MAX_PIXELS or im.width < 1 or im.height < 1 or getattr(im, "n_frames", 1) != 1:
                    blocked("Unsupported image format, size or frame count")
                details = {"decoded_format": im.format, "decoded_mime": Image.MIME[im.format],
                           "width": im.width, "height": im.height, "frames": 1, "mode": im.mode}
                im.verify()
            with Image.open(BytesIO(raw)) as im:
                im.load()  # Decode the SAME captured bytes, never reopen the source path.
                if getattr(im, "n_frames", 1) != 1:
                    blocked("Multiframe image rejected")
        return details
    except PolicyError:
        raise
    except Exception:
        blocked("Image failed full decode")


def profile(raw=None):
    """Public rules never authorize a new import; private imports keep the old pin."""
    if raw is None:
        raw = read_file(Path(__file__).resolve().parents[2] / "data/reviewed-image-profile-product610.json")
        expected_sha = PUBLIC_PROFILE_SHA
    else:
        expected_sha = MANIFEST_SHA
    if digest(raw) != expected_sha:
        blocked("Unapproved review profile or manifest")
    return loads(raw)


def has_review(product):
    return any("image_review" in f for f in list(product["fields"].values()) + list(product["measurements"].values()) + product["barcodes"])


def review_provenance(candidate, manifest):
    asset = next(a for a in manifest["assets"] if a["sha256"] == candidate["evidence_sha256"])
    return {"profile_id": PROFILE, "manifest_sha256": MANIFEST_SHA,
            "reviewed_at": manifest["generated_at"], "review_method": asset["pixel_review_method"],
            "source_quality": "source_reported_not_manufacturer_verified",
            "candidate": deepcopy(candidate)}


def reviewed_product(original, manifest):
    p = deepcopy(original)
    if p["source_id"] != "610" or p.get("snapshot_provenance", {}).get("input_sha256") != manifest["source_snapshot"]["sha256"]:
        blocked("Review belongs to a different product or snapshot")
    barcode_groups = {b["kind"]: b for b in p["barcodes"]}
    if len(barcode_groups) != len(p["barcodes"]):
        blocked("Ambiguous pre-review barcode groups")
    p["barcodes"] = []
    for c in manifest["numeric_and_barcode_candidates"]:
        key = c["target_field"]
        if key in {"pack", "carton"}:
            fact = deepcopy(barcode_groups[key])
            if fact["evidence_urls"][c["evidence_variant_index"]] != c["evidence_source_asset_url"] or len(fact["evidence_urls"]) != c["evidence_variant_count"]:
                blocked("Barcode evidence ordering mismatch")
            fact["evidence_urls"] = [c["evidence_source_asset_url"]]
            p["barcodes"].append(fact)
        else:
            fact = p["measurements"][key]
            if fact["evidence_urls"] != [c["evidence_source_asset_url"]]:
                blocked("Field URL does not match reviewed evidence")
        if fact["value"] is not None or fact["raw_value"] is not None or fact["raw_unit"] is not None or fact["verification_status"] != "needs_review" or fact["extraction_method"] != "image_not_read":
            blocked("Nonempty field requires separate reconciliation")
        if fact["source_label"] != c["source_label"] or fact["expected_unit"] != c["field_context_expected_unit"] or fact["source_url"] != "https://www.yanyue.cn/product/610":
            blocked("Review source label, unit or product mismatch")
        fact.update(value=c["candidate_value"], raw_value=c["image_display_text"], raw_unit=c["unit_printed_on_image"],
                    verification_status="manually_read_image", extraction_method="manual_image_review",
                    image_review=review_provenance(c, manifest))
    return p


def unreview_product(product):
    """Reconstruct only the known profile's unresolved facts; retain all other data."""
    p = deepcopy(product)
    for f in p["measurements"].values():
        if "image_review" in f:
            f.pop("image_review")
            f.update(value=None, raw_value=None, raw_unit=None, verification_status="needs_review", extraction_method="image_not_read")
    groups = {}
    for fact in p["barcodes"]:
        f = deepcopy(fact)
        if "image_review" not in f:
            blocked("Incomplete reviewed barcode set")
        f.pop("image_review")
        f.update(value=None, raw_value=None, raw_unit=None, verification_status="needs_review", extraction_method="image_not_read")
        if f["kind"] in groups:
            groups[f["kind"]]["evidence_urls"] += f["evidence_urls"]
        else:
            groups[f["kind"]] = f
    p["barcodes"] = list(groups.values())
    return p


def validate_reviewed_product(product):
    """Exact public fact rules plus opaque hashes bind every private review field.

    The approved private provenance is not distributed. Its canonical digest
    includes unknown keys, review time, method and the entire candidate object;
    changing or omitting any part is rejected, not ignored or re-created.
    """
    try:
        rules = profile()
        if product["source_id"] != rules["source_id"] or product["source_url"] != rules["source_url"] or product["snapshot_provenance"]["input_sha256"] != rules["source_snapshot"]["sha256"]:
            blocked("Reviewed product identity or snapshot mismatch")
        expected_slots = {(r["section"], r["selector"]) for r in rules["facts"]}
        actual_slots = {(section, str(key)) for section in ("fields", "measurements", "barcodes")
                        for key, fact in (enumerate(product[section]) if section == "barcodes" else product[section].items())
                        if "image_review" in fact}
        if actual_slots != expected_slots or len(product["barcodes"]) != 3:
            blocked("Reviewed field set differs from approved profile")
        for rule in rules["facts"]:
            key = int(rule["selector"]) if rule["section"] == "barcodes" else rule["selector"]
            fact = product[rule["section"]][key]
            public_fact = {k: v for k, v in fact.items() if k != "image_review"}
            if public_fact != rule["expected_fact"] or digest(dump_json(fact["image_review"]).encode()) != rule["image_review_sha256"]:
                blocked("Reviewed values or provenance do not match approved profile")
    except (KeyError, IndexError, TypeError, ValueError):
        blocked("Invalid reviewed product")


def inputs(manifest_raw, snapshot_raw, image_files, metadata_files):
    m = profile(manifest_raw)
    snap = loads(snapshot_raw)
    if digest(snapshot_raw) != m["source_snapshot"]["sha256"] or snap["source_url"] != "https://www.yanyue.cn/product/610" or snap["observed_at"] != m["source_snapshot"]["observed_at"]:
        blocked("Frozen source snapshot mismatch")
    expected_names = {a["local_content_addressed_file"] for a in m["assets"]}
    if set(image_files) != expected_names or set(metadata_files) != {a["metadata_file"] for a in m["assets"]}:
        blocked("Evidence set is incomplete or has unknown entries")
    bindings = {f["label"].rstrip(":："): f["images"] for f in snap["snapshot"]["fields"]}
    bindings["package_identification"] = [snap["snapshot"]["photo"]]
    for a in m["assets"]:
        raw = image_files[a["local_content_addressed_file"]]
        meta_raw = metadata_files[a["metadata_file"]]
        if a["local_content_addressed_file"] != a["sha256"] + (".png" if a["decoded_format"] == "PNG" else ".webp") or digest(raw) != a["sha256"] or len(raw) != a["bytes"]:
            blocked("Image bytes do not match reviewed hash")
        if decode_image(raw) != {k: a[k] for k in ("decoded_format", "decoded_mime", "width", "height", "frames", "mode")}:
            blocked("Actual image MIME or dimensions mismatch")
        if digest(meta_raw) != a["metadata_sha256"]:
            blocked("Download metadata hash mismatch")
        meta = loads(meta_raw)
        if meta["source_url"] != a["source_asset_url"] or meta["sha256"] != a["sha256"] or meta["source_product_id"] != "610":
            blocked("Download source association mismatch")
        if canonical_url(a["source_asset_url"], asset=True) != a["source_asset_url"] or any(a["source_asset_url"] not in bindings[label] for label in a["linked_fields_from_frozen_snapshot"]):
            blocked("Asset is not bound to the frozen source")
    for c in m["numeric_and_barcode_candidates"]:
        if bindings[c["source_label"]][c["evidence_variant_index"]] != c["evidence_source_asset_url"]:
            blocked("Field source evidence mismatch")
    return m, snap


def original_from_snapshot(snap, sha, imported_at):
    from .snapshot_import import parse_snapshot
    p = parse_snapshot(snap["snapshot"], source_url=snap["source_url"], source_id="610", name=snap["product_name"],
                       observed_at=snap["observed_at"], input_sha256=sha, input_format="loaded_browser_product_envelope_v1")["product"]
    p["snapshot_provenance"]["imported_at"] = imported_at
    return p


def import_record(manifest, original):
    return {"profile_id": PROFILE, "manifest_sha256": MANIFEST_SHA,
            "snapshot_sha256": manifest["source_snapshot"]["sha256"], "original_product": original}


def asset_record(original, a):
    result = deepcopy(original)
    result.update(sha256=a["sha256"], storage_path="assets/" + a["local_content_addressed_file"],
                  bytes=a["bytes"], decoded_mime=a["decoded_mime"], width=a["width"], height=a["height"], frames=1,
                  publication_status="quarantined_pending_review",
                  offline_review_import={"profile_id": PROFILE, "manifest_sha256": MANIFEST_SHA,
                    "download_metadata_sha256": a["metadata_sha256"], "source_product_id": "610",
                    "source_page_url": a["source_page_url"], "source_snapshot_sha256": profile()["source_snapshot"]["sha256"],
                    "download_observed_at": a["download_observed_at"], "retrieval_method_reported": a["retrieval_method_reported"],
                    "http_content_type_observed": False, "actual_http_request_count": None,
                    "cache_or_new_http_request": "not_observed", "original_bytes_unchanged": True})
    return result


def original_asset(document):
    doc = deepcopy(document)
    for key in ("sha256", "storage_path", "bytes", "decoded_mime", "width", "height", "frames", "offline_review_import"):
        doc.pop(key, None)
    return doc


def table_exists(store):
    return store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviewed_image_imports'").fetchone() is not None


def apply_bytes(store, manifest_raw, snapshot_raw, image_files, metadata_files, *, _already_locked=False, _transaction_owned=True):
    """All content prevalidated before files or database are changed; source controls untouched."""
    from .schema import validate
    if not _transaction_owned and not store.db.in_transaction:
        blocked("Shared application requires an active transaction")
    m, snap = inputs(manifest_raw, snapshot_raw, image_files, metadata_files)
    with (nullcontext() if _already_locked else exclusive(store)), directory(store.root) as rootfd:
        if _transaction_owned:
            store.db.execute("BEGIN IMMEDIATE")
        try:
            row = store.db.execute("SELECT document FROM products WHERE source_id='610'").fetchone()
            if row is None:
                blocked("Product610 is absent")
            current = loads(row[0].encode())
            original = original_from_snapshot(snap, m["source_snapshot"]["sha256"], current["snapshot_provenance"]["imported_at"])
            proposed = reviewed_product(original, m)
            validate(proposed, "product.schema.json")
            record = import_record(m, original)
            existing = store.db.execute("SELECT document FROM reviewed_image_imports WHERE source_id='610'").fetchone() if table_exists(store) else None
            already = existing is not None
            if already:
                if loads(existing[0].encode()) != record or current != proposed:
                    blocked("Existing reviewed record conflicts")
            elif current != original:
                blocked("Existing product has nonempty or conflicting facts")
            changes = []
            known = known_assets([loads(r[0].encode()) for r in store.db.execute("SELECT document FROM products")])
            for a in m["assets"]:
                ar = store.db.execute("SELECT status,document FROM assets WHERE url=?", (a["source_asset_url"],)).fetchone()
                if ar is None or ar[0] not in ({"downloaded"} if already else {"pending"}):
                    blocked("Expected pending URL or identical imported cache is absent")
                doc = loads(ar[1].encode())
                before = original_asset(doc) if already else doc
                if before.get("source_asset_url") != a["source_asset_url"] or "storage_path" in before or "sha256" in before:
                    blocked("Asset association or existing hash conflicts")
                if dump_json(before) not in known.get(a["source_asset_url"], set()):
                    blocked("Pending asset source document is not linked to a stored product")
                desired = asset_record(before, a)
                if already and doc != desired:
                    blocked("Existing asset provenance conflicts")
                changes.append((a["source_asset_url"], desired))
            if already:
                # Missing files are not silently repaired and cannot still count as downloaded.
                _verify_state_files(store, m, manifest_raw, snapshot_raw, image_files, metadata_files)
                if _transaction_owned:
                    store.db.rollback()
                return {"already_imported": True, "products_changed": 0, "new_cached_assets": 0, "source_requests": 0}
            assetfd = child_directory(rootfd, "assets")
            evidencefd = None
            try:
                evidencefd = child_directory(rootfd, "review_evidence")
                # Reject conflicting preexisting files before writing any new bytes.
                all_writes = [(assetfd, name, raw) for name, raw in image_files.items()]
                all_writes += [(evidencefd, digest(raw) + ".json", raw) for raw in [manifest_raw, snapshot_raw, *metadata_files.values()]]
                for fd, name, raw in all_writes:
                    try:
                        os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if read_at(fd, name, max(len(raw), 1)) != raw:
                        blocked("Existing evidence file conflicts")
                for fd, name, raw in all_writes:
                    write_at(fd, name, raw)
            finally:
                os.close(assetfd)
                if evidencefd is not None:
                    os.close(evidencefd)
            _verify_state_files(store, m, manifest_raw, snapshot_raw, image_files, metadata_files)
            # Safe crash behavior: file writes precede a single SQLite commit. Orphans
            # may remain on failure, but there is never a committed reference to unwritten bytes.
            store.db.execute("CREATE TABLE IF NOT EXISTS reviewed_image_imports(source_id TEXT PRIMARY KEY,document TEXT NOT NULL)")
            store.db.execute("INSERT INTO reviewed_image_imports VALUES ('610',?)", (dump_json(record),))
            store.db.execute("UPDATE products SET document=? WHERE source_id='610'", (dump_json(proposed),))
            store.db.executemany("UPDATE assets SET status='downloaded',document=? WHERE url=?", [(dump_json(doc), url) for url, doc in changes])
            if _transaction_owned:
                store.db.commit()
            return {"already_imported": False, "products_changed": 1, "reviewed_measurements": 6, "reviewed_barcode_variants": 3,
                    "new_cached_assets": 9, "image_bytes": sum(map(len, image_files.values())), "source_requests": 0,
                    "source_controls_changed": False, "manufacturer_verified": False, "publication_ready": False}
        except Exception:
            if _transaction_owned:
                store.db.rollback()
            raise


def _verify_state_files(store, m, manifest_raw, snapshot_raw, image_files, metadata_files):
    with directory(store.root / "assets") as fd:
        for name, expected in image_files.items():
            if read_at(fd, name, MAX_IMAGE_BYTES) != expected:
                blocked("Cached image is missing or changed")
    with directory(store.root / "review_evidence") as fd:
        for expected in [manifest_raw, snapshot_raw, *metadata_files.values()]:
            if read_at(fd, digest(expected) + ".json", MAX_JSON_BYTES) != expected:
                blocked("Review evidence is missing or changed")


def import_reviewed_images(store, manifest_path, snapshot_path, image_directory):
    manifest_raw = read_file(manifest_path)
    m = profile(manifest_raw)
    snapshot_raw = read_file(snapshot_path, 64 * 1024)
    with directory(image_directory) as fd:
        images = {a["local_content_addressed_file"]: read_at(fd, a["local_content_addressed_file"], MAX_IMAGE_BYTES) for a in m["assets"]}
        metadata = {a["metadata_file"]: read_at(fd, a["metadata_file"], MAX_JSON_BYTES) for a in m["assets"]}
    return apply_bytes(store, manifest_raw, snapshot_raw, images, metadata)


def known_assets(products):
    known = {}
    for p in products:
        docs = list(p["images"])
        for fact in list(p["measurements"].values()) + p["barcodes"]:
            docs += [{"source_asset_url": u, "source_page_url": p["source_url"], "purpose": "fact_evidence",
                      "publication_status": "quarantined_pending_review", "rights_status": "user_asserted_full_authorization",
                      "code_license_applies": False} for u in fact["evidence_urls"]]
        for d in docs:
            known.setdefault(d["source_asset_url"], set()).add(dump_json(d))
    return known


def check_base_assets(rows, products):
    known = known_assets(products)
    if not isinstance(rows, list) or len(rows) != len(known):
        blocked("Bundle asset reference set mismatch")
    seen = set()
    for r in rows:
        if not isinstance(r, dict) or set(r) != {"url", "document"} or r["url"] in seen or r["url"] not in known or dump_json(r["document"]) not in known[r["url"]]:
            blocked("Bundle contains an unbound asset document")
        seen.add(r["url"])
    if seen != set(known):
        blocked("Bundle has missing asset references")


def no_private_paths(value):
    if isinstance(value, dict):
        for k, v in value.items():
            no_private_paths(k)
            no_private_paths(v)
    elif isinstance(value, list):
        for v in value:
            no_private_paths(v)
    elif isinstance(value, str) and (value.startswith(("/", "file:", "~", "\\")) or re.search(r"(?:/workspace/|/home/|/tmp/|[A-Z]:\\)", value)):
        blocked("Private filesystem data must not enter a bundle")


def state_evidence(store):
    with directory(store.root / "review_evidence") as fd:
        manifest_raw = read_at(fd, MANIFEST_SHA + ".json", MAX_JSON_BYTES)
        m = profile(manifest_raw)
        snapshot_raw = read_at(fd, m["source_snapshot"]["sha256"] + ".json", 64 * 1024)
        metadata = {a["metadata_file"]: read_at(fd, a["metadata_sha256"] + ".json", MAX_JSON_BYTES) for a in m["assets"]}
    with directory(store.root / "assets") as fd:
        images = {a["local_content_addressed_file"]: read_at(fd, a["local_content_addressed_file"], MAX_IMAGE_BYTES) for a in m["assets"]}
    inputs(manifest_raw, snapshot_raw, images, metadata)
    return m, manifest_raw, snapshot_raw, images, metadata


def export_bundle(store, output):
    from .checkpoint import checkpoint_value, validate_checkpoint_value, MAX_CHECKPOINT_BYTES
    from .schema import validate
    with exclusive(store):
        store.db.execute("BEGIN")
        try:
            m, manifest_raw, snapshot_raw, images, metadata = state_evidence(store)
            rows = store.db.execute("SELECT source_id,document FROM reviewed_image_imports").fetchall() if table_exists(store) else []
            if len(rows) != 1 or rows[0][0] != "610":
                blocked("Unsupported review profile set")
            record = loads(rows[0][1].encode())
            current = checkpoint_value(store)
            validate(current, "checkpoint.schema.json")
            product = next(p for p in current["products"] if p["source_id"] == "610")
            original = original_from_snapshot(loads(snapshot_raw), m["source_snapshot"]["sha256"], product["snapshot_provenance"]["imported_at"])
            if record != import_record(m, original) or product != reviewed_product(original, m):
                blocked("Stored review provenance mismatch")
            current["products"] = [original if p["source_id"] == "610" else p for p in current["products"]]
            validate_checkpoint_value(current)  # Core is deliberately unresolved; bundle replays the approved review.
            base_assets = []
            reviewed = {a["source_asset_url"]: a for a in m["assets"]}
            for r in store.db.execute("SELECT url,status,document FROM assets ORDER BY url"):
                d = loads(r["document"].encode())
                if r["url"] in reviewed:
                    base = original_asset(d)
                    if r["status"] != "downloaded" or d != asset_record(base, reviewed[r["url"]]):
                        blocked("Downloaded asset metadata conflicts")
                else:
                    base = d
                    if r["status"] != "pending":
                        blocked("Unbundled asset state cannot be represented")
                base_assets.append({"url": r["url"], "document": base})
            check_base_assets(base_assets, current["products"])
            checkpoint_raw = (dump_json(current) + "\n").encode()
            if len(checkpoint_raw) > MAX_CHECKPOINT_BYTES:
                blocked("Core checkpoint exceeds limit")
            files = dict(images)
            for raw in [manifest_raw, snapshot_raw, checkpoint_raw, *metadata.values()]:
                files[digest(raw) + ".json"] = raw
            bundle = {"schema_version": "reviewed_image_bundle_1", "profile_id": PROFILE,
                      "checkpoint_file": digest(checkpoint_raw) + ".json", "manifest_file": MANIFEST_SHA + ".json",
                      "snapshot_file": digest(snapshot_raw) + ".json",
                      "metadata_files": {name: digest(raw) + ".json" for name, raw in metadata.items()},
                      "files": [{"name": name, "sha256": digest(raw), "bytes": len(raw)} for name, raw in sorted(files.items())],
                      "base_asset_rows": base_assets, "import_record": record,
                      "images_included": True, "manufacturer_verified": False, "publication_ready": False}
            no_private_paths(bundle)
            no_private_paths(current)
            raw_bundle = (dump_json(bundle) + "\n").encode()
            if sum(map(len, files.values())) + len(raw_bundle) > MAX_BUNDLE_BYTES:
                blocked("Evidence bundle exceeds expanded limit")
        finally:
            store.db.rollback()  # Read-only transaction; source controls and records unchanged.
        out = Path(output)
        with directory(out.parent) as parentfd:
            try:
                os.mkdir(out.name, mode=0o700, dir_fd=parentfd)
            except FileExistsError:
                pass
            except OSError:
                blocked("Cannot create bundle directory")
        with directory(out) as fd:
            if os.listdir(fd):
                blocked("Bundle output must be an empty directory")
            for name, raw in files.items():
                write_at(fd, name, raw)
            try:
                f = os.open("bundle.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(f, "wb") as stream:
                    stream.write(raw_bundle)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
            except OSError:
                blocked("Bundle finalization failed")
    return {"images_included": True, "image_files": 9, "file_count": len(files), "source_requests": 0,
            "products": len(current["products"]), "publication_ready": False}


def read_bundle(path):
    from .checkpoint import validate_checkpoint_value, MAX_CHECKPOINT_BYTES
    with directory(path) as fd:
        raw = read_at(fd, "bundle.json", MAX_JSON_BYTES)
        b = loads(raw)
        keys = {"schema_version", "profile_id", "checkpoint_file", "manifest_file", "snapshot_file", "metadata_files", "files", "base_asset_rows", "import_record", "images_included", "manufacturer_verified", "publication_ready"}
        if not isinstance(b, dict) or set(b) != keys or b["schema_version"] != "reviewed_image_bundle_1" or b["profile_id"] != PROFILE or b["images_included"] is not True or b["manufacturer_verified"] is not False or b["publication_ready"] is not False:
            blocked("Unsupported bundle manifest")
        no_private_paths(b)
        if not isinstance(b["files"], list) or not 1 <= len(b["files"]) <= 32:
            blocked("Invalid bundle file count")
        files = {}
        total = len(raw)
        for entry in b["files"]:
            if not isinstance(entry, dict) or set(entry) != {"name", "sha256", "bytes"} or not isinstance(entry["name"], str) or not NAME.fullmatch(entry["name"]) or entry["name"] in files or type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= MAX_CHECKPOINT_BYTES or entry["sha256"] != entry["name"].split(".")[0]:
                blocked("Invalid bundle file entry")
            total += entry["bytes"]
            if total > MAX_BUNDLE_BYTES:
                blocked("Bundle exceeds expanded limit")
            data = read_at(fd, entry["name"], entry["bytes"])
            if len(data) != entry["bytes"] or digest(data) != entry["sha256"]:
                blocked("Bundle file hash mismatch")
            files[entry["name"]] = data
        if set(os.listdir(fd)) != set(files) | {"bundle.json"}:
            blocked("Bundle contains unexpected files")
    try:
        if b["manifest_file"] != MANIFEST_SHA + ".json":
            blocked("Unapproved manifest reference")
        manifest_raw = files[b["manifest_file"]]
        m = profile(manifest_raw)
        snapshot_raw = files[b["snapshot_file"]]
        if b["snapshot_file"] != m["source_snapshot"]["sha256"] + ".json":
            blocked("Unexpected frozen snapshot")
        images = {a["local_content_addressed_file"]: files[a["local_content_addressed_file"]] for a in m["assets"]}
        expected_meta = {a["metadata_file"]: a["metadata_sha256"] + ".json" for a in m["assets"]}
        if b["metadata_files"] != expected_meta:
            blocked("Unexpected metadata file mapping")
        metadata = {name: files[ref] for name, ref in expected_meta.items()}
        if set(files) != set(images) | set(expected_meta.values()) | {b["manifest_file"], b["snapshot_file"], b["checkpoint_file"]}:
            blocked("Bundle has unreferenced files")
        checkpoint = loads(files[b["checkpoint_file"]])
        no_private_paths(checkpoint)
        validate_checkpoint_value(checkpoint)
        inputs(manifest_raw, snapshot_raw, images, metadata)
        product = next(p for p in checkpoint["products"] if p["source_id"] == "610")
        original = original_from_snapshot(loads(snapshot_raw), m["source_snapshot"]["sha256"], product["snapshot_provenance"]["imported_at"])
        if product != original or b["import_record"] != import_record(m, original):
            blocked("Bundle base product or provenance conflicts")
        check_base_assets(b["base_asset_rows"], checkpoint["products"])
    except (KeyError, StopIteration, TypeError, IndexError):
        blocked("Bundle evidence reference mismatch")
    return b, checkpoint, manifest_raw, snapshot_raw, images, metadata


def restore_bundle(store, path):
    from .checkpoint import restore_checkpoint_value
    b, checkpoint, manifest_raw, snapshot_raw, images, metadata = read_bundle(path)
    # All bundle bytes, source associations and values are verified before mutating
    # even the empty destination. Re-apply is the same gate, not a privileged bypass.
    with exclusive(store):
        if any(store.db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("queue", "products", "assets", "metadata", "brands", "brand_reports", "edges", "runs")) or table_exists(store):
            blocked("Bundle restore requires completely empty state")
        store.db.execute("BEGIN IMMEDIATE")
        try:
            restore_checkpoint_value(store, checkpoint, _transaction_owned=False)
            for r in b["base_asset_rows"]:
                store.db.execute("UPDATE assets SET document=? WHERE url=?", (dump_json(r["document"]), r["url"]))
            result = apply_bytes(store, manifest_raw, snapshot_raw, images, metadata,
                                 _already_locked=True, _transaction_owned=False)
            store.db.commit()
        except Exception:
            store.db.rollback()
            raise
        result.update(restored_products=len(checkpoint["products"]), image_files_restored=9)
        return result



def main(argv=None):
    import argparse
    from .store import Store
    class SafeArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            raise PolicyError("Invalid offline image evidence arguments")
    parser = SafeArgumentParser(description="Offline reviewed-image evidence; no download or network option")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--manifest", required=True)
    imp.add_argument("--snapshot", required=True)
    imp.add_argument("--images", required=True)
    exp = sub.add_parser("export-bundle")
    exp.add_argument("--output", required=True)
    res = sub.add_parser("restore-bundle")
    res.add_argument("--input", required=True)
    for p in (imp, exp, res):
        p.add_argument("--state-dir", required=True)
    store = None
    try:
        args = parser.parse_args(argv)
        # Parent components and existing database checked before Store's legacy
        # initialization. Callers must use an isolated, existing private directory.
        with directory(args.state_dir) as fd:
            if "state.sqlite" in os.listdir(fd):
                read_at(fd, "state.sqlite", 64 * 1024 * 1024)
        store = Store(args.state_dir)
        if args.command == "import":
            result = import_reviewed_images(store, args.manifest, args.snapshot, args.images)
        elif args.command == "export-bundle":
            result = export_bundle(store, args.output)
        else:
            result = restore_bundle(store, args.input)
        print(dump_json(result))
        return 0
    except (PolicyError, OSError, KeyError, ValueError, TypeError, sqlite3.Error):
        print(dump_json({"error": "offline_evidence_operation_rejected", "source_requests": 0}))
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
