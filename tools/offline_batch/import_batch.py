"""Thin offline batch driver for the hash-pinned repository snapshot adapter.

No browser/source API imports. Only after a batch is declared complete, capture
one bounded local file at a time and import its unchanged bytes serially.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parents[1]  # repository root; use the one existing tools/yanyue package
FILE_RE = re.compile(r"product-([1-9][0-9]{0,12})\.json\Z")
MAX_FILES = 100
MAX_FILE_BYTES = 64 * 1024


class BatchError(ValueError):
    pass


def adapter_api():
    """Check the frozen adapter's code/schema hashes before importing it."""
    hashes = json.loads((HERE / "ADAPTER_SHA256.json").read_text())
    for relative, expected in hashes.items():
        path = ADAPTER / relative
        if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != expected:
            raise BatchError("Frozen adapter hash mismatch")
    expected_package = (ADAPTER / "tools/yanyue").resolve()
    for name, module in tuple(sys.modules.items()):
        if name == "yanyue" or name.startswith("yanyue."):
            location = getattr(module, "__file__", None)
            if location is None or not Path(location).resolve().is_relative_to(expected_package):
                raise BatchError("A different adapter is already imported")
    sys.path.insert(0, str(ADAPTER / "tools"))
    from yanyue.snapshot_import import import_snapshot
    from yanyue.store import Store, atomic_json
    from yanyue.policy import PolicyError, utcnow
    return import_snapshot, Store, atomic_json, PolicyError, utcnow


def capture_file(path):
    """Bounded exact-byte snapshot; no symlink/FIFO/device reads or JSON rewrite."""
    if path.is_symlink():
        raise BatchError("Input is not a regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise BatchError("Input is not a regular file")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise BatchError("Input exceeds the per-file byte cap")
    return data


def write_report(output, report):
    """Exclusive random temporary file; never follow a planted fixed .tmp link."""
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + output.name + ".", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def run_batch(input_dir, state_dir, output, *, batch_complete=False):
    if not batch_complete:
        raise BatchError("An explicit completed-batch acknowledgement is required")
    directory, output = Path(input_dir), Path(output)
    if directory.is_symlink() or not directory.is_dir():
        raise BatchError("Batch input must be a non-symlink directory")
    # Reports (including atomic_json's .tmp sibling) must never share the
    # source or SQLite namespaces, even if those directories do not exist yet.
    source_root = directory.resolve()
    state_root = Path(state_dir).resolve()
    temporary_report = output.with_suffix(output.suffix + ".tmp")
    for report_path in (output, temporary_report):
        resolved = report_path.resolve()
        if resolved.is_relative_to(source_root) or resolved.is_relative_to(state_root):
            raise BatchError("Report path conflicts with input or state namespace")
        if report_path.is_symlink() or report_path.exists():
            raise BatchError("Use a new report and temporary path; never overwrite files")
    files = []
    for path in directory.iterdir():
        if FILE_RE.fullmatch(path.name):
            files.append(path)
            if len(files) > MAX_FILES:
                raise BatchError("Batch file count is outside the reviewed cap")
    if not files:
        raise BatchError("Batch file count is outside the reviewed cap")
    files.sort(key=lambda p: int(FILE_RE.fullmatch(p.name)[1]))
    importer, Store, atomic_json, PolicyError, utcnow = adapter_api()
    report = {"schema_version":"1.0", "scope":"completed_local_snapshot_batch_only", "started_at":utcnow(),
              "finished_at":None, "batch_declared_complete":True, "selected_files":len(files),
              "max_files":MAX_FILES, "max_bytes_per_file":MAX_FILE_BYTES,
              "captured_bytes":0, "imported":0, "already_imported":0, "rejected":0,
              "interrupted":False, "network_executed":False, "source_requests":0,
              "source_jobs_updated":False, "images_downloaded":0, "files":[],
              "whole_website_complete":False, "publication_ready":False}
    store = Store(state_dir)
    try:
        with store.exclusive():
            before_pause = store.meta("pause")
            # The importer already guarantees no queue/metadata edits. Keep an
            # independent exact snapshot here to catch future contract drift.
            before_queue = [tuple(r) for r in store.db.execute("SELECT * FROM queue ORDER BY url")]
            before_meta = [tuple(r) for r in store.db.execute("SELECT * FROM metadata ORDER BY key")]
            write_report(output, report)
            try:
                for source in files:
                    source_id = FILE_RE.fullmatch(source.name)[1]
                    entry = {"file":source.name, "source_id":source_id, "input_sha256":None,
                             "input_bytes":None, "status":"rejected", "reason":None}
                    try:
                        data = capture_file(source)
                        digest = sha256(data).hexdigest()
                        entry.update(input_sha256=digest, input_bytes=len(data))
                        report["captured_bytes"] += len(data)
                        # Private immutable-by-convention copy: changes to the
                        # source path after capture cannot change imported bytes.
                        with tempfile.TemporaryDirectory(prefix="yanyue-offline-batch-") as temporary:
                            captured = Path(temporary) / source.name
                            captured.write_bytes(data)
                            result = importer(store, captured, source_id=source_id)
                        if result["input_sha256"] != digest:
                            raise BatchError("Imported input hash mismatch")
                        status = "already_imported" if result["already_imported"] else "imported"
                        entry.update(status=status, observed_at=result["observed_at"])
                        report[status] += 1
                    except (PolicyError, BatchError, OSError, UnicodeError, ValueError):
                        entry["reason"] = "snapshot_rejected_or_local_file_unavailable"
                        report["rejected"] += 1
                    except Exception:
                        entry["reason"] = "unexpected_offline_import_error"
                        report["rejected"] += 1
                    report["files"].append(entry)
                    write_report(output, report)
            except KeyboardInterrupt:
                report["interrupted"] = True
            report["finished_at"] = utcnow()
            report["processed_files"] = len(report["files"])
            report["coverage"] = store.coverage()
            report["source_state_unchanged"] = (
                before_pause == store.meta("pause") and
                before_queue == [tuple(r) for r in store.db.execute("SELECT * FROM queue ORDER BY url")] and
                before_meta == [tuple(r) for r in store.db.execute("SELECT * FROM metadata ORDER BY key")])
            write_report(output, report)
            return report
    finally:
        store.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir",required=True)
    parser.add_argument("--state-dir",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--batch-complete",action="store_true",help="Operator has declared the local source batch complete")
    args=parser.parse_args(argv)
    try:
        report=run_batch(args.input_dir,args.state_dir,args.output,batch_complete=args.batch_complete)
        print(json.dumps({key:report[key] for key in ("selected_files","processed_files","imported","already_imported","rejected","interrupted","source_requests","source_state_unchanged")},ensure_ascii=False))
        return 2 if report["rejected"] or report["interrupted"] or not report["source_state_unchanged"] else 0
    except Exception:
        # Local source names and parser text never escape in a traceback.
        print(json.dumps({"error":"offline_batch_blocked","network_executed":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
