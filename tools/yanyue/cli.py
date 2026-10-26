"""Explicit network gate; default invocation is a plan, never a crawl."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
from .checkpoint import export_checkpoint, restore_checkpoint
from .crawler import run_pages, run_images
from .fetch import BrowserFetcher, Transport
from .parser import GROUPS
from .policy import Budget, BudgetExhausted, ORIGIN, Pause, PolicyError, RateLimiter, canonical_url, utcnow, walltime_deadline
from .store import Store, atomic_json, dump_json


def parser():
    p = argparse.ArgumentParser(description="Source-attributed factual catalog collector; no commerce, no challenge bypass.")
    sub = p.add_subparsers(dest="command", required=True)
    crawl = sub.add_parser("crawl", help="Plan a bounded run; --execute is required for all network access")
    crawl.add_argument("--state-dir", default="data/runtime")
    crawl.add_argument("--execute", action="store_true")
    crawl.add_argument("--phase", choices=["discover", "products", "all"], default="all")
    crawl.add_argument("--only-brand", help="Filter already-observed jobs; never generates source IDs")
    crawl.add_argument("--max-pages", type=int, default=3)
    crawl.add_argument("--max-requests", type=int, default=5)
    crawl.add_argument("--max-images", type=int, default=0)
    crawl.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    crawl.add_argument("--max-seconds", type=int, default=180)
    crawl.add_argument("--interval", type=float, default=5)
    status = sub.add_parser("status", help="Read coverage, pause state and unresolved work offline")
    status.add_argument("--state-dir", default="data/runtime")
    resume = sub.add_parser("resume-source", help="Explicit operator review after a source pause; no network")
    resume.add_argument("--state-dir", default="data/runtime")
    resume.add_argument("--acknowledge-source-review", action="store_true", required=True)
    resume.add_argument("--retry-failed", action="store_true")
    imp = sub.add_parser("import-observed-index", help="Import the reviewed source research index offline")
    imp.add_argument("--state-dir", default="data/runtime")
    imp.add_argument("--input", required=True)
    cp = sub.add_parser("checkpoint", help="Strict JSON-only export/restore; no browser state or images")
    cps = cp.add_subparsers(dest="checkpoint_command", required=True)
    for command, flag in (("export", "--output"), ("restore", "--input")):
        c = cps.add_parser(command)
        c.add_argument("--state-dir", default="data/runtime")
        c.add_argument(flag, required=True)
    return p


def import_observed(store, filename):
    path = Path(filename)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise PolicyError("Observed index exceeds import limit")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("source_url") != ORIGIN + "/tobacco" or document.get("access_method") != "normal_browser_javascript_navigation_then_dom_read":
        raise PolicyError("Index does not match the reviewed observed-source contract")
    brands = {}
    for group in document["groups"]:
        group_name = GROUPS[group["source_group_id"]]
        for item in group["brands"]:
            url = canonical_url(item["source_url"])
            if url != ORIGIN + "/sort/" + item["source_id"]:
                raise PolicyError("Observed brand identity mismatch")
            brand = brands.setdefault(item["source_id"], {"source_id": item["source_id"], "name": item["name"], "source_url": url, "source_groups": []})
            if group_name not in brand["source_groups"]:
                brand["source_groups"].append(group_name)
    result = {"kind": "index", "source_url": ORIGIN + "/tobacco", "observed_at": document["checked_at"], "source_access_method": "imported_normal_browser_research", "source_observed_date": document["checked_at"], "source_time_precision": "day", "brands": list(brands.values()), "discovered": [{"url": b["source_url"], "kind": "brand", "brand": b} for b in brands.values()]}
    store.seed()
    store.save_page(result)
    return {"imported_unique_brands": len(brands), "date_precision": "day", "source_original_html_hash_available": False}


def main(argv=None):
    args = parser().parse_args(argv)
    store = Store(args.state_dir)
    try:
        with store.exclusive():
            if args.command == "status":
                result = store.coverage()
            elif args.command == "checkpoint":
                result = export_checkpoint(store, args.output) if args.checkpoint_command == "export" else restore_checkpoint(store, args.input)
            elif args.command == "resume-source":
                store.clear_pause()
                if args.retry_failed:
                    store.db.execute("UPDATE queue SET status='pending' WHERE status='failed'")
                    store.db.commit()
                result = store.coverage()
            elif args.command == "import-observed-index":
                result = import_observed(store, args.input)
            else:
                budget = Budget(args.max_requests, args.max_bytes, args.max_seconds)
                limiter = RateLimiter(args.interval)
                if not 1 <= args.max_pages <= 500 or not 0 <= args.max_images <= 200:
                    raise PolicyError("Page/image budgets exceed hard caps")
                if not args.execute:
                    result = {"network_executed": False, "execute_flag_required": True, "phase": args.phase, "max_pages": args.max_pages, "max_requests": args.max_requests, "max_images": args.max_images, "minimum_interval_seconds": args.interval, "coverage": store.coverage()}
                else:
                    if store.meta("pause"):
                        raise Pause("source_is_paused_explicit_review_required")
                    store.seed()
                    transport = Transport(budget, limiter)
                    started = utcnow()
                    completed_before = store.db.execute("SELECT count(*) FROM queue WHERE status='complete'").fetchone()[0]
                    def interrupted(signum, frame):
                        raise KeyboardInterrupt
                    old_term = signal.signal(signal.SIGTERM, interrupted)
                    try:
                        with walltime_deadline(args.max_seconds):
                            transport.ensure_robots(ORIGIN)
                            with BrowserFetcher(transport) as browser:
                                result = run_pages(store, browser, max_pages=args.max_pages, phase=args.phase, only_brand=args.only_brand)
                            result["images_downloaded_this_run"] = run_images(store, transport, max_images=args.max_images)
                    except BudgetExhausted:
                        result = {"pages_completed_this_run": 0, "stop_reason": "hard_run_budget_reached", "finished_at": utcnow()}
                        store.put_meta("last_result", result)
                    except Pause as exc:
                        store.pause(exc.reason, exc.retry_after)
                        result = {"pages_completed_this_run": 0, "stop_reason": exc.reason, "finished_at": utcnow()}
                        store.put_meta("last_result", result)
                    except KeyboardInterrupt:
                        result = {"pages_completed_this_run": 0, "stop_reason": "operator_interrupted", "finished_at": utcnow()}
                        store.put_meta("last_result", result)
                    except Exception:
                        store.pause("unexpected_source_operation_error")
                        result = {"pages_completed_this_run": 0, "stop_reason": "unexpected_source_operation_error", "finished_at": utcnow()}
                        store.put_meta("last_result", result)
                    finally:
                        completed_after = store.db.execute("SELECT count(*) FROM queue WHERE status='complete'").fetchone()[0]
                        result["pages_completed_this_run"] = max(0, completed_after - completed_before)
                        store.put_meta("last_result", {k: result[k] for k in ("pages_completed_this_run", "stop_reason", "finished_at")})
                        signal.signal(signal.SIGTERM, old_term)
                        summary = {k: getattr(budget, k) for k in ("max_requests", "requests", "max_bytes", "bytes", "max_seconds")}
                        store.put_meta("budget_summary", summary)
                        store.put_meta("robots_audit", transport.robots_audit)
                        atomic_json(store.root / "coverage.json", store.coverage())
                    result.update(network_executed=True, budget=summary, coverage=store.coverage())
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            if args.command == "crawl" and args.execute and (store.meta("pause") or result.get("stop_reason") == "operator_interrupted"):
                return 2
            return 0
    except Exception:
        # Deliberately omit raw tool/network exception strings and arbitrary URLs.
        print(json.dumps({"error": "operation_blocked_by_policy_or_source_pause", "coverage": store.coverage()}, ensure_ascii=False, indent=2))
        return 2
    finally:
        store.close()

if __name__ == "__main__":
    raise SystemExit(main())
