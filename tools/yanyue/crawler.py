"""Single-worker orchestration with durable partial progress and no implicit retry."""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
from .parser import parse_page
from .policy import BudgetExhausted, Pause, PolicyError, utcnow
from .store import atomic_json, dump_json


def run_pages(store, fetcher, *, max_pages=3, phase="all", only_brand=None):
    if not 1 <= max_pages <= 500:
        raise PolicyError("Page budget must be 1..500")
    if store.meta("pause"):
        raise Pause("source_is_paused_explicit_review_required")
    complete = 0
    stop_reason = "queue_exhausted_in_selected_scope"
    while complete < max_pages:
        job = store.next(phase, only_brand)
        if not job:
            break
        url = job["url"]
        store.attempted(url)
        try:
            page = fetcher.fetch(url)
            parsed = parse_page(page.html, url=url, access_method=page.access_method)
            expected = job["metadata"].get("name")
            if parsed["kind"] == "product" and expected and parsed["product"]["name"] != expected:
                raise PolicyError("Product name differs from the observed catalog entry")
            expected_brand = job["metadata"].get("brand", {}).get("name")
            if parsed["kind"] == "brand" and expected_brand and parsed["brand_name"] != expected_brand:
                raise PolicyError("Brand name differs from the observed catalog entry")
            store.save_page(parsed)
            complete += 1
        except BudgetExhausted:
            stop_reason = "hard_run_budget_reached"
            break  # Job stays pending; last-good data is unchanged.
        except Pause as exc:
            store.fail(url, exc.reason, pause=True)
            store.pause(exc.reason, exc.retry_after)
            stop_reason = exc.reason
            break
        except PolicyError:
            store.fail(url, "page_requires_parser_review")
            store.pause("page_requires_parser_review")
            stop_reason = "page_requires_parser_review"
            break
        except (KeyboardInterrupt, SystemExit):
            stop_reason = "operator_interrupted"
            break
        except Exception:
            store.fail(url, "unexpected_fetch_or_parse_error")
            store.pause("unexpected_fetch_or_parse_error")
            stop_reason = "unexpected_fetch_or_parse_error"
            break
    else:
        stop_reason = "page_budget_reached"
    result = {"pages_completed_this_run": complete, "stop_reason": stop_reason, "finished_at": utcnow()}
    store.put_meta("last_result", result)
    atomic_json(store.root / "coverage.json", store.coverage())
    return result


def run_images(store, transport, *, max_images=0):
    if not 0 <= max_images <= 200:
        raise PolicyError("Image budget must be 0..200")
    count = 0
    for row in store.db.execute("SELECT * FROM assets WHERE status='pending' ORDER BY rowid").fetchall():
        if count >= max_images or store.meta("pause"):
            break
        url = row["url"]
        try:
            body, details = transport.image(url)
            content_hash = sha256(body).hexdigest()
            ext = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}[details["format"]]
            relative = f"assets/{content_hash}.{ext}"
            path = store.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            with temporary.open("wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
            manifest = json.loads(row["document"])
            manifest.update(details, sha256=content_hash, storage_path=relative, fetched_at=utcnow(), publication_status="quarantined_pending_review")
            with store.db:
                store.db.execute("UPDATE assets SET status='downloaded',document=? WHERE url=?", (dump_json(manifest), url))
            count += 1
        except BudgetExhausted:
            break
        except Pause as exc:
            store.pause(exc.reason, exc.retry_after)
            break
        except PolicyError:
            with store.db:
                store.db.execute("UPDATE assets SET status='needs_review' WHERE url=?", (url,))
            store.pause("asset_requires_review")
            break
        except Exception:
            store.pause("unexpected_asset_error")
            break
    return count
