"""Offline tests for fixed-target diagnostic identity and safe output."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yanyue.checkpoint import export_checkpoint
from yanyue.store import Store

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/checkpoint_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("checkpoint_diagnostics", SCRIPT)
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)


class CheckpointDiagnosticsTests(unittest.TestCase):
    def metadata(self):
        run = {"id": DIAG.RUN_ID, "run_attempt": 1, "event": "workflow_dispatch", "status": "completed",
               "conclusion": "failure", "head_branch": "main", "head_sha": DIAG.SOURCE_SHA,
               "path": ".github/workflows/data-pipeline.yml", "repository": {"full_name": DIAG.REPOSITORY},
               "head_repository": {"full_name": DIAG.REPOSITORY}}
        artifact = {"id": DIAG.ARTIFACT_ID, "name": DIAG.ARTIFACT_NAME, "expired": False,
                    "size_in_bytes": DIAG.ARTIFACT_BYTES, "digest": DIAG.ARTIFACT_DIGEST,
                    "workflow_run": {"id": DIAG.RUN_ID, "head_sha": DIAG.SOURCE_SHA, "head_branch": "main"}}
        return run, artifact

    def checkpoint(self, directory):
        store = Store(Path(directory) / "state")
        store.pause("source_http_403")
        store.put_meta("last_result", {"pages_completed_this_run": 0, "stop_reason": "source_http_403", "finished_at": "2026-10-26T18:54:00+00:00"})
        store.put_meta("budget_summary", {"max_requests": 5, "requests": 1, "max_bytes": 10485760, "bytes": 0, "max_seconds": 180})
        with patch.dict(os.environ, {"GITHUB_SHA": DIAG.SOURCE_SHA}):
            export_checkpoint(store, Path(directory) / "public")
        store.close()
        return Path(directory) / "public"

    def test_exact_provenance_only(self):
        run, artifact = self.metadata()
        self.assertEqual(DIAG.verify_provenance(run, artifact), {"provenance_validated": True})
        for key, bad in {"head_sha": "a" * 40, "run_attempt": 2, "id": 12, "event": "push", "head_branch": "other", "path": "other.yml"}.items():
            altered = copy.deepcopy(run)
            altered[key] = bad
            with self.assertRaises(ValueError):
                DIAG.verify_provenance(altered, artifact)
        for key, bad in {"id": 12, "size_in_bytes": 664, "digest": "sha256:bad", "expired": True, "name": "other"}.items():
            altered = copy.deepcopy(artifact)
            altered[key] = bad
            with self.assertRaises(ValueError):
                DIAG.verify_provenance(run, altered)

    def test_summary_has_only_allowlisted_scalars_no_source_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkpoint(directory)
            with patch("socket.getaddrinfo", side_effect=AssertionError("network_forbidden")), patch("socket.create_connection", side_effect=AssertionError("network_forbidden")):
                output = DIAG.summarize(root)
            self.assertEqual(output["pause_reason"], "source_http_403")
            self.assertEqual(output["requests"], 1)
            self.assertTrue(all(isinstance(v, (bool, int, str)) or v is None for v in output.values()))
            text = json.dumps(output)
            self.assertNotIn("https://", text)
            self.assertNotIn("finished_at", text)
            self.assertNotIn("source_commit", {k for k in output})

    def test_reject_wrong_sha_budget_extra_file_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkpoint(directory)
            file = root / "checkpoint.json"
            original = file.read_text()
            value = json.loads(original)
            value["source_commit"] = "a" * 40
            file.write_text(json.dumps(value))
            with self.assertRaises(ValueError): DIAG.summarize(root)
            value = json.loads(original)
            value["budget_summary"]["max_requests"] = 6
            file.write_text(json.dumps(value))
            with self.assertRaises(ValueError): DIAG.summarize(root)
            file.write_text(original)
            (root / "extra").write_text("nothing")
            with self.assertRaises(ValueError): DIAG.summarize(root)
            (root / "extra").unlink()
            link = Path(directory) / "link"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError): DIAG.summarize(link)

    def test_unknown_reason_is_redacted(self):
        self.assertEqual(DIAG.safe_reason("synthetic_private_sentinel"), "unrecognized_code_redacted")
        self.assertIsNone(DIAG.safe_reason(None))

    def test_invalid_payload_does_not_leak_in_failure_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkpoint(directory)
            (root / "checkpoint.json").write_text('{"private":"synthetic_private_sentinel"}')
            capture = io.StringIO()
            with patch("sys.argv", [str(SCRIPT), "summary", "--input", str(root)]), contextlib.redirect_stdout(capture):
                self.assertEqual(DIAG.main(), 2)
            self.assertEqual(json.loads(capture.getvalue()), {"diagnostic_error": "validation_failed_no_payload_logged"})

    def test_duplicate_and_oversize_json_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "metadata.json"
            file.write_text('{"a":1,"a":2}')
            with self.assertRaises(ValueError): DIAG.read_json(file, 100)
            file.write_text("x" * 101)
            with self.assertRaises(ValueError): DIAG.read_json(file, 100)


if __name__ == "__main__":
    unittest.main()
