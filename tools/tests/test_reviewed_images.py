"""Offline synthetic test-only inputs; production pins never admit them.

Real 82-record/9-original-image integration is independently verified outside the
public repository. These tests exercise failure/transaction paths, not a real
image review or full-source collection.
"""
from contextlib import redirect_stdout, ExitStack
from copy import deepcopy
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from yanyue.store import Store, dump_json
from yanyue.policy import PolicyError
from yanyue.schema import validate
from yanyue.checkpoint import restore_checkpoint, export_checkpoint, read_checkpoint, checkpoint_value
from yanyue import reviewed_images as review
from synthetic_review_inputs import build, policy_for

FIXTURE = Path(__file__).parent / "fixtures/reviewed-images"


class ReviewedImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.input = self.root / "inputs"
        manifest, manifest_raw, public_policy = build(self.input)
        # Synthetic inputs are NOT accepted by the unmodified production gate.
        with self.assertRaises(PolicyError): review.profile(manifest_raw)
        self.patches = ExitStack()
        synthetic_sha = review.digest(manifest_raw)
        self.patches.enter_context(patch.object(review, "MANIFEST_SHA", synthetic_sha))
        public_policy = policy_for(manifest, public_policy, synthetic_sha)
        def synthetic_profile(raw=None):
            if raw is None:
                return deepcopy(public_policy)
            if review.digest(raw) != synthetic_sha:
                raise PolicyError("Synthetic test profile mismatch")
            return review.loads(raw)
        self.patches.enter_context(patch.object(review, "profile", side_effect=synthetic_profile))
        self.store = Store(self.root / "state")
        restore_checkpoint(self.store, self.input / "baseline-checkpoint.json")
        self.m = review.profile((self.input / "review-manifest.json").read_bytes())
        self.network = patch("socket.socket", side_effect=AssertionError("Network forbidden in offline review test"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.patches.close()
        self.store.close()
        self.tmp.cleanup()

    def apply(self):
        return review.import_reviewed_images(self.store, self.input / "review-manifest.json", self.input / "snapshot.json", self.input / "images")

    def rows(self, table):
        return [tuple(r) for r in self.store.db.execute("SELECT * FROM " + table + " ORDER BY 1")]

    def product(self, source_id="610"):
        return json.loads(self.store.db.execute("SELECT document FROM products WHERE source_id=?", (source_id,)).fetchone()[0])

    def assert_unapplied(self):
        self.assertFalse(review.has_review(self.product()))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM assets WHERE status='downloaded'").fetchone()[0], 0)

    def test_apply_only_one_product_and_preserve_controls(self):
        controls = {t: self.rows(t) for t in ("queue", "edges", "brands", "brand_reports", "metadata", "runs")}
        products = dict(self.rows("products"))
        result = self.apply()
        self.assertEqual(result["image_bytes"], sum(a["bytes"] for a in self.m["assets"]))
        self.assertEqual(result["source_requests"], 0)
        for t, rows in controls.items():
            self.assertEqual(self.rows(t), rows)
        after = dict(self.rows("products"))
        self.assertEqual({k for k in products if products[k] != after[k]}, {"610"})
        p = self.product()
        self.assertEqual([b["value"] for b in p["barcodes"]], ["6901028184243", "6901028179058", "6901028184250"])
        self.assertTrue(all(isinstance(b["value"], str) for b in p["barcodes"]))
        self.assertEqual(p["measurements"]["sticks_per_pack"]["value"], 20)
        self.assertIsNone(p["measurements"]["sticks_per_pack"]["raw_unit"])
        self.assertEqual(p["measurements"]["sticks_per_pack"]["expected_unit"], "支")
        for key in ("filter_length_mm", "circumference_mm"):
            self.assertIsNone(p["measurements"][key]["value"])
        self.assertFalse(p["manufacturer_verified"])
        for a in self.m["assets"]:
            self.assertEqual((self.store.root / "assets" / a["local_content_addressed_file"]).read_bytes(), (self.input / "images" / a["local_content_addressed_file"]).read_bytes())
        validate(p, "product.schema.json")

    def test_idempotent_no_row_overwrite(self):
        self.apply()
        before = {t: self.rows(t) for t in ("products", "assets", "reviewed_image_imports", "metadata", "queue")}
        self.assertTrue(self.apply()["already_imported"])
        for t, rows in before.items():
            self.assertEqual(self.rows(t), rows)

    def test_unknown_changed_or_unreviewed_manifest_rejected(self):
        path = self.input / "review-manifest.json"
        mutations = [lambda m: m.update(unknown=True), lambda m: m.update(independent_review_completed=False),
                     lambda m: m["numeric_and_barcode_candidates"][0].update(source_product_id="611"),
                     lambda m: m["numeric_and_barcode_candidates"][0].update(target_field="filter_length_mm"),
                     lambda m: m["numeric_and_barcode_candidates"][0].update(candidate_value=11),
                     lambda m: m["assets"][0].update(source_asset_url="https://res.yanyue.cn/unknown.png")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                m = deepcopy(self.m); mutation(m); path.write_text(json.dumps(m))
                with self.assertRaises(PolicyError): self.apply()
                self.assert_unapplied()

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(PolicyError): review.loads(b'{"a":1,"a":2}')

    def test_wrong_snapshot_and_image_bytes_rejected(self):
        p = self.input / "snapshot.json"; original = p.read_bytes(); p.write_bytes(original + b" ")
        with self.assertRaises(PolicyError): self.apply()
        p.write_bytes(original)
        image = self.input / "images" / self.m["assets"][0]["local_content_addressed_file"]
        image.write_bytes(image.read_bytes()[:-1] + b"x")
        with self.assertRaises(PolicyError): self.apply()
        self.assert_unapplied()

    def test_metadata_hash_mismatch_rejected(self):
        p = self.input / "images" / self.m["assets"][0]["metadata_file"]
        p.write_bytes(p.read_bytes() + b" ")
        with self.assertRaises(PolicyError): self.apply()
        self.assert_unapplied()

    def test_file_and_parent_symlinks_rejected(self):
        p = self.input / "images" / self.m["assets"][0]["local_content_addressed_file"]
        target = self.root / "target"; p.rename(target); p.symlink_to(target)
        with self.assertRaises(PolicyError): self.apply()
        p.unlink(); target.rename(p)
        real = self.input / "actual"; (self.input / "images").rename(real); (self.input / "images").symlink_to(real, target_is_directory=True)
        with self.assertRaises(PolicyError): self.apply()
        self.assert_unapplied()

    def test_hardlink_and_path_traversal_rejected(self):
        p = self.input / "images" / self.m["assets"][0]["local_content_addressed_file"]
        os.link(p, self.root / "hardlink")
        with self.assertRaises(PolicyError): self.apply()
        with review.directory(self.input) as fd:
            with self.assertRaises(PolicyError): review.read_at(fd, "../secret", 10)

    def test_single_descriptor_read_detects_in_place_change(self):
        original = os.fstat
        counter = [0]
        def changed(fd):
            s = original(fd); counter[0] += 1
            if counter[0] == 2:
                from types import SimpleNamespace
                return SimpleNamespace(st_size=s.st_size, st_mtime_ns=s.st_mtime_ns+1, st_ctime_ns=s.st_ctime_ns)
            return s
        with review.directory(self.input) as fd, patch.object(review.os, "fstat", side_effect=changed):
            with self.assertRaises(PolicyError): review.read_at(fd, "snapshot.json", 65536)

    def test_postread_source_replacement_does_not_change_captured_bytes(self):
        a = self.m["assets"][0]
        p = self.input / "images" / a["local_content_addressed_file"]
        expected = p.read_bytes()
        decoder = review.decode_image
        def replace_after_read(raw):
            p.write_bytes(b"replaced source after immutable read")
            return decoder(raw)
        with patch.object(review, "decode_image", side_effect=replace_after_read): self.apply()
        self.assertEqual((self.store.root / "assets" / a["local_content_addressed_file"]).read_bytes(), expected)

    def test_output_symlink_and_hash_collision_rejected_without_db_changes(self):
        out = self.store.root / "assets"; out.mkdir()
        a = self.m["assets"][0]
        p = out / a["local_content_addressed_file"]; p.write_bytes(b"conflict")
        with self.assertRaises(PolicyError): self.apply()
        self.assertEqual(p.read_bytes(), b"conflict")
        self.assert_unapplied()
        p.unlink(); out.rmdir(); other = self.root / "outside"; other.mkdir(); out.symlink_to(other, target_is_directory=True)
        with self.assertRaises(PolicyError): self.apply()
        self.assertEqual(list(other.iterdir()), [])
        self.assert_unapplied()

    def test_removed_destination_before_commit_is_not_marked_downloaded(self):
        writer = review.write_at
        removed = [False]
        def remove_after_write(fd, name, raw):
            result = writer(fd, name, raw)
            if name.endswith(".webp") and not removed[0]:
                os.unlink(name, dir_fd=fd)
                removed[0] = True
            return result
        with patch.object(review, "write_at", side_effect=remove_after_write):
            with self.assertRaises(PolicyError): self.apply()
        self.assert_unapplied()

    def test_existing_nonempty_fact_or_missing_pending_url_rejected(self):
        p = self.product(); p["measurements"]["tar_mg"]["value"] = 11
        self.store.db.execute("UPDATE products SET document=? WHERE source_id='610'", (dump_json(p),)); self.store.db.commit()
        before = self.rows("products")
        with self.assertRaises(PolicyError): self.apply()
        self.assertEqual(self.rows("products"), before)
        p["measurements"]["tar_mg"]["value"] = None
        self.store.db.execute("UPDATE products SET document=? WHERE source_id='610'", (dump_json(p),))
        self.store.db.execute("DELETE FROM assets WHERE url=?", (self.m["assets"][0]["source_asset_url"],)); self.store.db.commit()
        with self.assertRaises(PolicyError): self.apply()
        self.assert_unapplied()

    def test_actual_decode_limits_and_mime(self):
        with self.assertRaises(PolicyError): review.decode_image(b"x" * (review.MAX_IMAGE_BYTES + 1))
        with self.assertRaises(PolicyError): review.decode_image(b"<html>not image</html>")
        b = BytesIO(); Image.new("RGB", (4001,4000)).save(b, format="PNG")
        with self.assertRaises(PolicyError): review.decode_image(b.getvalue())
        b = BytesIO(); Image.new("RGB", (2,2)).save(b, format="JPEG")
        with self.assertRaises(PolicyError): review.decode_image(b.getvalue())
        b = BytesIO(); Image.new("RGB", (2,2), "red").save(b, format="PNG", save_all=True, append_images=[Image.new("RGB", (2,2), "blue")])
        with self.assertRaises(PolicyError): review.decode_image(b.getvalue())
        data = (self.input / "images" / self.m["assets"][0]["local_content_addressed_file"]).read_bytes()
        with self.assertRaises(PolicyError): review.decode_image(data[:len(data)//2])

    def test_schema_cannot_bypass_gate_or_drop_barcode_variant(self):
        self.apply(); original = self.product()
        for mutate in (lambda p: p["measurements"]["tar_mg"].update(value=11),
                       lambda p: p["measurements"]["tar_mg"].pop("image_review"),
                       lambda p: p["barcodes"].pop(1),
                       lambda p: p["barcodes"].reverse(),
                       lambda p: p.update(source_id="611", id="yanyue:611", source_url="https://www.yanyue.cn/product/611")):
            p = deepcopy(original); mutate(p)
            with self.assertRaises(PolicyError): validate(p, "product.schema.json")

    def test_unreviewed_barcode_limit_is_not_relaxed(self):
        p = self.product()
        p["barcodes"].append(deepcopy(p["barcodes"][0]))
        with self.assertRaises(PolicyError): validate(p, "product.schema.json")

    def test_plain_checkpoint_rejects_duplicate_keys(self):
        raw = (self.input / "baseline-checkpoint.json").read_text()
        f = self.root / "duplicate-checkpoint.json"
        f.write_text('{"images_included":true,' + raw.lstrip()[1:])
        with self.assertRaises(PolicyError): read_checkpoint(f)

    def test_json_only_checkpoint_rejects_reviewed_values(self):
        self.apply()
        with self.assertRaises(PolicyError): export_checkpoint(self.store, self.root / "plain")
        cp = checkpoint_value(self.store)
        f = self.root / "forged.json"; f.write_text(dump_json(cp))
        with self.assertRaises(PolicyError): read_checkpoint(f)

    def test_bundle_roundtrip_preserves_all_public_state_and_files(self):
        self.apply(); target = self.root / "bundle"
        result = review.export_bundle(self.store, target)
        self.assertEqual(result["file_count"], 21)
        dest = Store(self.root / "restored")
        try:
            review.restore_bundle(dest, target)
            for t in ("products", "assets", "metadata", "queue", "brands", "brand_reports", "edges", "reviewed_image_imports"):
                self.assertEqual(self.rows(t), [tuple(r) for r in dest.db.execute("SELECT * FROM " + t + " ORDER BY 1")])
            self.assertEqual(review.state_evidence(self.store), review.state_evidence(dest))
        finally: dest.close()
        for f in target.iterdir():
            if f.suffix == ".json":
                self.assertNotIn(str(self.root), f.read_text())

    def test_missing_state_file_cannot_idempotently_claim_download_or_export(self):
        self.apply()
        (self.store.root / "assets" / self.m["assets"][0]["local_content_addressed_file"]).unlink()
        with self.assertRaises(PolicyError): self.apply()
        with self.assertRaises(PolicyError): review.export_bundle(self.store, self.root / "bundle")

    def test_missing_or_corrupt_bundle_rejected_before_restore(self):
        self.apply(); target = self.root / "bundle"; review.export_bundle(self.store, target)
        p = target / self.m["assets"][0]["local_content_addressed_file"]; raw = p.read_bytes(); p.unlink()
        dest = Store(self.root / "restored")
        try:
            with self.assertRaises(PolicyError): review.restore_bundle(dest, target)
            self.assertEqual(dest.db.execute("SELECT count(*) FROM products").fetchone()[0], 0)
            p.write_bytes(raw[:-1])
            with self.assertRaises(PolicyError): review.restore_bundle(dest, target)
            self.assertEqual(dest.db.execute("SELECT count(*) FROM assets").fetchone()[0], 0)
        finally: dest.close()

    def test_unknown_extra_symlink_bundle_entries_rejected(self):
        self.apply(); target = self.root / "bundle"; review.export_bundle(self.store, target)
        manifest = target / "bundle.json"; raw = manifest.read_bytes(); b = json.loads(raw); b["private_path"] = str(self.root); manifest.write_text(dump_json(b))
        with self.assertRaises(PolicyError): review.read_bundle(target)
        manifest.write_bytes(raw); extra = target / "extra"; extra.write_bytes(b"x")
        with self.assertRaises(PolicyError): review.read_bundle(target)
        extra.unlink(); p = target / self.m["assets"][0]["local_content_addressed_file"]; other = self.root / "other"; p.rename(other); p.symlink_to(other)
        with self.assertRaises(PolicyError): review.read_bundle(target)

    def test_restore_write_failure_rolls_back_empty_and_direct_retry_succeeds(self):
        self.apply(); target = self.root / "bundle"; review.export_bundle(self.store, target)
        dest = Store(self.root / "restored")
        writer = review.write_at
        calls = [0]
        def fail_second(fd, name, raw):
            calls[0] += 1
            if calls[0] == 2: raise PolicyError("Injected local write failure")
            return writer(fd, name, raw)
        try:
            with patch.object(review, "write_at", side_effect=fail_second):
                with self.assertRaises(PolicyError): review.restore_bundle(dest, target)
            for table in ("products", "assets", "metadata", "queue", "edges", "brands", "brand_reports", "runs"):
                self.assertEqual(dest.db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
            self.assertFalse(review.table_exists(dest))
            # Previously completed unreferenced bytes remain; no deletion/overwrite.
            saved = {p.name: p.read_bytes() for p in (dest.root / "assets").iterdir()}
            self.assertTrue(saved)
            review.restore_bundle(dest, target)
            for name, raw in saved.items(): self.assertEqual((dest.root / "assets" / name).read_bytes(), raw)
            self.assertEqual(review.state_evidence(self.store), review.state_evidence(dest))
        finally: dest.close()

    def test_all_manual_flags_require_approved_per_field_provenance(self):
        for group, name in (("fields", "brand_name"), ("measurements", "filter_length_mm"), ("measurements", "tar_mg")):
            for status, method in (("manually_read_image", "manual_image_review"), ("manually_read_image", "source_text"), ("source_reported", "manual_image_review")):
                p = self.product(); p[group][name].update(verification_status=status, extraction_method=method)
                with self.assertRaises(PolicyError): validate(p, "product.schema.json")
                cp = checkpoint_value(self.store); cp["products"] = [p if x["source_id"] == "610" else x for x in cp["products"]]
                f = self.root / "fake-manual.json"; f.write_text(dump_json(cp))
                with self.assertRaises(PolicyError): read_checkpoint(f)

    def test_worker_lock_symlink_or_hardlink_cannot_write_outside(self):
        p = self.store.root / "worker.lock"
        if p.exists(): p.unlink()
        outside = self.root / "must-not-be-created"
        p.symlink_to(outside)
        with self.assertRaises(PolicyError): self.apply()
        self.assertFalse(outside.exists()); self.assert_unapplied()
        p.unlink(); outside.write_bytes(b"unchanged"); os.link(outside, p)
        with self.assertRaises(PolicyError): self.apply()
        self.assertEqual(outside.read_bytes(), b"unchanged"); self.assert_unapplied()

    def test_export_and_restore_use_safe_worker_lock(self):
        self.apply(); target = self.root / "bundle"; review.export_bundle(self.store, target)
        p = self.store.root / "worker.lock"; p.unlink(); outside = self.root / "export-outside"; p.symlink_to(outside)
        with self.assertRaises(PolicyError): review.export_bundle(self.store, self.root / "blocked-bundle")
        self.assertFalse(outside.exists())
        dest = Store(self.root / "restored")
        try:
            targetlock = dest.root / "worker.lock"; outside2 = self.root / "restore-outside"; targetlock.symlink_to(outside2)
            with self.assertRaises(PolicyError): review.restore_bundle(dest, target)
            self.assertFalse(outside2.exists())
            self.assertEqual(dest.db.execute("SELECT count(*) FROM products").fetchone()[0], 0)
        finally: dest.close()

    def test_cli_corrupt_database_error_is_path_redacted(self):
        state = self.root / "corrupt-state"; state.mkdir()
        (state / "state.sqlite").write_bytes(b"not a sqlite database")
        output = StringIO()
        with redirect_stdout(output):
            code = review.main(["import", "--state-dir", str(state), "--manifest", str(self.input / "review-manifest.json"),
                                "--snapshot", str(self.input / "snapshot.json"), "--images", str(self.input / "images")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "offline_evidence_operation_rejected", "source_requests": 0})
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertEqual((state / "state.sqlite").read_bytes(), b"not a sqlite database")

    def test_cli_errors_never_expose_private_paths(self):
        output = StringIO()
        with redirect_stdout(output):
            code = review.main(["import", "--state-dir", str(self.store.root), "--manifest", str(self.root/"secret"), "--snapshot", str(self.input/"snapshot.json"), "--images", str(self.input/"images")])
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["source_requests"], 0)


if __name__ == "__main__": unittest.main()


class ReviewedCLIArgumentPrivacy(unittest.TestCase):
    def test_argument_errors_do_not_echo_paths_or_open_state(self):
        from contextlib import redirect_stderr
        canary = '/synthetic/private/ARGUMENT_CANARY'
        cases = [[], ['import', '--state-dir', canary, '--manifest', canary, '--snapshot', canary, '--images', canary, '--unexpected', canary],
                 ['export-bundle', '--state-dir', canary], ['unknown-command', canary]]
        for args in cases:
            with self.subTest(args=args):
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr), patch('yanyue.store.Store', side_effect=AssertionError('No state access on invalid arguments')):
                    code = review.main(args)
                self.assertEqual(code, 2)
                self.assertEqual(stderr.getvalue(), '')
                self.assertNotIn(canary, stdout.getvalue())
                self.assertEqual(json.loads(stdout.getvalue()), {'error':'offline_evidence_operation_rejected','source_requests':0})
