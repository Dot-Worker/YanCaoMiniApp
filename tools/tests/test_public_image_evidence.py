"""Self-contained read-only checks against published single-product evidence."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
from hashlib import sha256
import io,json,os
from pathlib import Path
import shutil,tempfile,unittest
from unittest.mock import patch
from yanyue import public_image_evidence as verify
from yanyue import reviewed_images as review
from yanyue.policy import PolicyError

ROOT=Path(__file__).resolve().parents[2]
SNAP=ROOT/'tools/tests/fixtures/public-reviewed/product-610-fields.json'
IMAGES=ROOT/'WeChatAPP/miniprogram/assets/reviewed'


class PublicEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.snapshot=self.root/'snapshot.json';shutil.copyfile(SNAP,self.snapshot)
        self.images=self.root/'images';shutil.copytree(IMAGES,self.images)
        self.names=sorted(p.name for p in self.images.iterdir())
    def tearDown(self):self.tmp.cleanup()
    def call(self):return verify.verify_public_evidence(self.snapshot,self.images)
    def reject(self):
        with self.assertRaises((PolicyError,KeyError,ValueError,TypeError)):self.call()
    def hashes(self):
        return {str(p.relative_to(self.root)):sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}

    def test_actual_public_evidence_is_readonly_without_sql_network_private_inputs(self):
        before=self.hashes()
        with ExitStack() as stack:
            for target in ('socket.socket','socket.create_connection','socket.getaddrinfo','sqlite3.connect','yanyue.store.Store','yanyue.reviewed_images.apply_bytes','yanyue.reviewed_images.import_reviewed_images','yanyue.reviewed_images.restore_bundle'):
                stack.enter_context(patch(target,side_effect=AssertionError('not permitted')))
            original_read=review.read_file
            reads=[]
            def bounded_read(path,*args):
                reads.append(Path(path).name)
                self.assertIn(Path(path).name,{'snapshot.json','reviewed-image-profile-product610.json'})
                return original_read(path,*args)
            stack.enter_context(patch.object(review,'read_file',side_effect=bounded_read))
            result=self.call()
        self.assertEqual(set(reads),{'snapshot.json','reviewed-image-profile-product610.json'})
        self.assertEqual((len(result['assets_verified']),len(result['published_reviewed_facts'])),(9,9))
        self.assertEqual(result['source_snapshot_sha256'],'3448d16e48149b4d15b1b1ad6d34b26b92e73ecc24bd6f35db09c0f627a39693')
        for flag in ('new_visual_review_performed','private_review_provenance_validated','writes_database','state_changed','manufacturer_verified','publication_ready'):
            self.assertFalse(result[flag])
        self.assertEqual(result['source_requests'],0);self.assertEqual(result['images_downloaded'],0)
        self.assertNotIn('\"image_review\":',json.dumps(result))
        self.assertEqual(before,self.hashes())

    def test_units_are_visible_or_context_only_not_fabricated(self):
        facts=self.call()['published_reviewed_facts']
        numeric={f['selector']:f for f in facts if f['section']=='measurements'}
        self.assertEqual(len(numeric),6)
        for key in ('sticks_per_pack','packs_per_carton'):
            self.assertIsNone(numeric[key]['expected_fact']['raw_unit'])
            self.assertEqual(numeric[key]['unit_basis'],'field_label_context_only')
        self.assertEqual(numeric['tar_mg']['expected_fact']['value'],10)
        self.assertEqual(numeric['tar_mg']['expected_fact']['raw_unit'],'mg')
        barcode=[f for f in facts if f['section']=='barcodes']
        self.assertEqual([f['expected_fact']['kind'] for f in barcode],['pack','pack','carton'])
        self.assertEqual([f['evidence_variant_index'] for f in barcode],[0,1,0])
        self.assertEqual([f['evidence_variant_count'] for f in barcode],[2,2,1])

    def test_missing_extra_and_renamed_images_fail(self):
        target=self.images/self.names[0];body=target.read_bytes();target.unlink();self.reject()
        target.write_bytes(body);extra=self.images/'extra.png';extra.write_bytes(body);self.reject()
        extra.unlink();target.rename(self.images/'renamed.png');self.reject()

    def test_same_size_tampering_and_wrong_format_fail(self):
        target=self.images/self.names[0];body=target.read_bytes();target.write_bytes(bytes([body[0]^1])+body[1:]);self.reject()
        target.write_bytes(b'<html>not an image</html>');self.reject()

    def test_snapshot_wrong_identity_bytes_and_unknown_keys_fail(self):
        raw=SNAP.read_bytes()
        for change in (lambda v:v.update(source_url='https://outside.example/product/610'),lambda v:v.update(product_name='Other'),lambda v:v.update(cookie='secret')):
            v=json.loads(raw);change(v);self.snapshot.write_text(json.dumps(v));self.reject()
        self.snapshot.write_bytes(raw+b'\n');self.reject()

    def test_fixed_policy_cannot_be_overridden(self):
        original=review.read_file
        def tamper(path,*args):
            raw=original(path,*args)
            if Path(path).name=='reviewed-image-profile-product610.json':
                rules=review.loads(raw);rules['facts'][0]['expected_fact']['value']=999
                return json.dumps(rules).encode()
            return raw
        with patch.object(review,'read_file',side_effect=tamper):self.reject()

    def test_symlink_files_and_parent_directories_fail(self):
        target=self.images/self.names[0];target.unlink();target.symlink_to(IMAGES/self.names[0]);self.reject()
        target.unlink();shutil.copyfile(IMAGES/self.names[0],target)
        real=self.snapshot;self.snapshot=self.root/'snapshot-link.json';self.snapshot.symlink_to(real);self.reject()
        self.snapshot=real
        link=self.root/'dir-link';link.symlink_to(self.images,target_is_directory=True);self.images=link;self.reject()

    def test_hardlinks_fail_without_changing_original(self):
        target=self.images/self.names[0];held=self.root/'held.png';target.rename(held);os.link(held,target);self.reject()
        self.assertEqual(held.read_bytes(),(IMAGES/self.names[0]).read_bytes())

    def test_fifo_file_fails_without_wait(self):
        target=self.images/self.names[0];target.unlink();os.mkfifo(target);self.reject()

    def test_full_decode_result_is_checked(self):
        actual=review.decode_image
        def mismatch(raw):
            data=actual(raw);data['width']+=1;return data
        with patch.object(review,'decode_image',side_effect=mismatch):self.reject()

    def test_anchored_image_directory_does_not_follow_replacement(self):
        actual=review.read_at;replaced=False
        def replace_once(fd,name,cap):
            nonlocal replaced
            if name in self.names and not replaced:
                replaced=True;self.images.rename(self.root/'held');self.images.mkdir()
                for n in self.names:(self.images/n).write_bytes(b'wrong replacement')
            return actual(fd,name,cap)
        with patch.object(review,'read_at',side_effect=replace_once):result=self.call()
        self.assertTrue(replaced);self.assertEqual(len(result['assets_verified']),9)
        self.assertTrue(all(p.read_bytes()==b'wrong replacement' for p in self.images.iterdir()))

    def test_internal_variant_and_unit_binding_guards(self):
        for mutate in (lambda r:r['facts'][0].update(evidence_variant_index=9),lambda r:r['facts'][0]['expected_fact'].update(raw_unit='mm'),lambda r:r['facts'][0]['expected_fact'].update(source_label='另一标签')):
            rules=deepcopy(review.profile());mutate(rules)
            # Production policy hash rejects modified rules first; mock here
            # independently exercises the underlying source-binding checks.
            with patch.object(review,'profile',return_value=rules):self.reject()

    def test_cli_success_and_sanitized_failure(self):
        stream=io.StringIO()
        with redirect_stdout(stream):code=verify.main(['--snapshot',str(self.snapshot),'--images',str(self.images)])
        self.assertEqual(code,0);self.assertFalse(json.loads(stream.getvalue())['state_changed'])
        stream=io.StringIO()
        with patch.object(verify,'verify_public_evidence',side_effect=OSError('secret-token=/private/path')),redirect_stdout(stream):
            code=verify.main(['--snapshot',str(self.snapshot),'--images',str(self.images)])
        self.assertEqual(code,2);self.assertNotIn('secret',stream.getvalue());self.assertNotIn('/private',stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())['error'],'public_image_evidence_rejected')


    def test_cli_argument_failures_are_sanitized_and_help_works(self):
        private='/secret/private/source.json'
        cases=[['--snapshot',private,'--images','/secret/private/images','--state-dir','/secret/private/state'],
            ['--snapshot',private],['--snapshot',private,'--images']]
        for args in cases:
            stdout,stderr=io.StringIO(),io.StringIO()
            with self.subTest(args=args),redirect_stdout(stdout),redirect_stderr(stderr):
                code=verify.main(args)
            self.assertEqual(code,2);self.assertEqual(stderr.getvalue(),'')
            self.assertNotIn('/secret',stdout.getvalue());self.assertNotIn('Traceback',stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())['error'],'public_image_evidence_rejected')
        out=io.StringIO()
        with redirect_stdout(out),self.assertRaises(SystemExit) as caught:verify.main(['--help'])
        self.assertEqual(caught.exception.code,0);self.assertIn('--snapshot',out.getvalue())


if __name__=='__main__':unittest.main()
