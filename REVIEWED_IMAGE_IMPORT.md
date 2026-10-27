# Offline reviewed-image evidence — product610

This is a **narrow local evidence implementation**, not a crawler, general OCR
importer, manufacturer certification, or an authorization to resume source access.
No browser, download, network, proxy, publication or source-resume option exists.

## Accepted evidence

Only the existing independently pixel-reviewed product610 manifest is admitted:
`423946df3bc973d6d93349ded43ac95e2ef4ee6d9497a2f37cbd56e6f3c4c15e`.
The full private manifest is deliberately NOT distributed. `data/` contains only a separately byte-pinned minimal public machine policy: source/asset hashes, expected facts and opaque canonical hashes of approved provenance. A future product/review requires a
separately reviewed code/profile change. Unknown fields, changed values, another
product, unreviewed fields, changed URLs or even different manifest serialization
are rejected; the pin is a local approved-evidence boundary, not a digital signature.

The frozen field-only snapshot hash is
`3448d16e48149b4d15b1b1ad6d34b26b92e73ecc24bd6f35db09c0f627a39693`.
It is not an original HTML hash. The existing snapshot parser is reused to
reconstruct and compare the entire original product before applying values.

9 exact saved files (1 WebP + 8 PNGs, 22,656 bytes) are checked against the manifest,
original download-metadata hashes, existing pending URLs, stored product linkage,
and frozen per-label source URLs. Actual MIME, <=5 MiB, <=16 MP, single-frame full
decode are independently checked. Hashes are over original bytes; no image encoding,
cropping, watermark removal or metadata stripping occurs. Decoded MIME is **not** a
claim to have observed HTTP Content-Type. Prior browser HTTP/cache counts are unknown.

## Values and provenance

Only product610 receives 6 measurements and 3 barcode variants. The pack barcodes
are **two separate strings**, not one selected primary variant. Each fact stores its
exact reviewed candidate, source label, original text, source/product URL, snapshot
hash, image hash, manifest hash, review time/method and unit basis. `20`/`10` have
`raw_unit: null`; expected units come only from the field label context. Missing
filter length/circumference stay null. Reused URLs are cached, but other products'
field values/review statuses are not changed. Review status means visually read
source-reported data; it is not measured or manufacturer-verified.

The source-reported barcode checksum flag is arithmetic quality only, not product
or barcode allocation/authenticity verification. All images remain quarantined;
rights remain user-asserted, not the code license. `publication_ready` stays false.

## Filesystem and transaction boundary

Inputs are traversed with directory descriptors and `O_NOFOLLOW`; only bounded,
regular, single-link files are read. Hashing and decoding consume the same captured
bytes. In-place changes are checked with descriptor metadata before/after reads;
subsequent source-path replacement cannot alter captured bytes. Output files are
content-addressed and created with `O_EXCL`, never overwritten. Existing files must
be byte-identical. Source queue, attempts, pause, budget, run records and other
products are not modified. A single SQLite transaction applies the review. Files
are fsynced first: a failed/crashed import may leave **unreferenced files**, never a
committed reference to bytes not successfully written. Retry refuses conflicting
partial files rather than overwriting them. State is a private, trusted local
working directory; callers coordinate through the the compatible worker-lock name using a descriptor-anchored NOFOLLOW, regular, single-link lock.

## Legacy controlled import (offline; private evidence required)

This legacy import/bundle path is for the evidence custodian, not a public reproduction recipe. It still requires the original private manifest and download records; these are not packaged in this repository. Inputs must already exist locally and the
state directory must be an isolated copy (the CLI does not choose/copy a live state).

```sh
PYTHONPATH=tools python -m yanyue.reviewed_images import \
  --state-dir isolated-state --manifest review-manifest.json \
  --snapshot snapshot.json --images retained-images
PYTHONPATH=tools python -m yanyue.reviewed_images export-bundle \
  --state-dir isolated-state --output new-evidence-bundle
mkdir empty-restored-state
PYTHONPATH=tools python -m yanyue.reviewed_images restore-bundle \
  --state-dir empty-restored-state --input new-evidence-bundle
PYTHONPATH=tools python -m unittest discover -s tools/tests -v
```

A repeat of the identical import verifies retained files and returns no changes.
Conflicting/nonempty facts, changed metadata, missing files or existing different
hashes fail closed. Errors/results contain no local input/output paths.

## Explicit checkpoint bundle (not an ordinary checkpoint)

A bundle contains `bundle.json` plus 21 content-addressed files: 9 original images,
9 original bounded download metadata JSONs, the reviewed manifest, frozen snapshot,
and a core checkpoint. No SQLite, cookie/browser state, download temporary path,
private absolute path, original HTML or unlisted file is exported.

The core checkpoint deliberately retains unresolved product610 values; the bundle
contains the additional evidence necessary to replay the exact reviewed gate. All
file hashes, byte sizes, MIME/frame/decode limits, schema, product bindings and the
complete asset URL set are checked before restoring into a completely empty state.
The downloaded state is reconstructed **only after** the actual files pass. Missing
or modified files abort; a JSON-only checkpoint cannot claim restored image review.
Restore uses one shared SQLite transaction across core and image application: any failure rolls back to the original empty database and allows direct retry. Successfully written but unreferenced content-addressed files are retained, never deleted or overwritten; identical bytes can be reused by retry.

Original `products`, `assets`, queue/control metadata, brands, reports, edges and
review provenance round-trip. Like the existing core checkpoint, transient caches,
worker locks and run history are not a portable checkpoint contract.

Ordinary `checkpoint export` rejects a state containing this reviewed evidence;
ordinary JSON-only restore rejects reviewed values. Bundle export rejects other
unbundled downloaded/failed asset states, rather than silently losing them. The
bundle is explicitly only this reviewed-image profile; future images need a new
approved profile/bundle extension. It does not automatically publish image bytes or
make any recommendation about tobacco use.

## Public packaging and checks

Public structural schemas do not embed internal review instances. The complete
`yanyue.schema.validate` contract also checks exact field locations, every public
fact and each complete private-provenance canonical hash against the pinned public
policy. JSON Schema alone is not sufficient for approval. Mutated/unknown private
provenance is rejected rather than ignored.

The public image regression tests generate small synthetic images and temporary
records. Test-only pins are scoped to `unittest.mock`; the production gate explicitly
rejects those generated manifests. No 82-record checkpoint, original download
metadata or internal review log is committed. The single retained product610 field
fixture is explicitly bounded and separately identified. Actual 82-product +
522-brand + 9-original-image state/bundle round-trip validation is performed outside
the public repository; passing public CI is not a claim that it re-ran that private
integration or collected any new source data.

Public self-contained read-only verification uses the published policy, single-product field fixture and 9 client images. See [PUBLIC_IMAGE_VERIFICATION.md](PUBLIC_IMAGE_VERIFICATION.md). It does not require private audit records, generate legacy provenance, write a database or reproduce private import/bundle operations.
