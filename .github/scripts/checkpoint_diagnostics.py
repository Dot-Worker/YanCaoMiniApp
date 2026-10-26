"""Fixed-target, offline artifact diagnosis; never restore or contact the source."""
import argparse
import json
from pathlib import Path

REPOSITORY = "Dot-Worker/YanCaoMiniApp"
SOURCE_SHA = "89af1edefdb855b17ba5ae3d438052fbdcf2402f"
RUN_ID = 40381732787
ARTIFACT_ID = 12679460903
ARTIFACT_NAME = "yancao-public-checkpoint-40381732787-1"
ARTIFACT_BYTES = 663
ARTIFACT_DIGEST = "sha256:90fc28e102ccad6181b509e48982f7bd7b9bef3b113a16b4c0a1877509324611"
MAX_EXPANDED_BYTES = 20 * 1024 * 1024

# Only known reviewed code constants may appear in logs, never arbitrary strings.
SAFE_REASONS = frozenset("""
robots_not_readable robots_disallow robots_malformed_group robots_invalid_delay
robots_unsupported_pattern_review robots_unsupported_path_review
browser_start_failed_no_fallback_or_escalation blocked_browser_channel_close_failed
browser_authentication_required browser_authentication_cancel_failed
document_challenge document_login document_loader canonical_page_not_reached
normal_loader_did_not_finish normal_browser_navigation_failed
browser_interception_failed browser_navigation_outside_source
browser_navigation_outside_target browser_source_response_error
http_redirect_requires_review unexpected_document_mime
compressed_browser_document_requires_review source_is_paused_explicit_review_required
unexpected_source_operation_error page_requires_parser_review
unexpected_fetch_or_parse_error asset_requires_review unexpected_asset_error
queue_exhausted_in_selected_scope hard_run_budget_reached page_budget_reached
operator_interrupted
""".split()) | {"source_http_401", "source_http_403", "source_http_429"} | {
    f"source_http_{status}" for status in range(500, 600)
}


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def read_json(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError("invalid_bounded_file")
    return json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=reject_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite")))


def verify_provenance(run, artifact):
    if not (
        run["id"] == RUN_ID and run["run_attempt"] == 1
        and run["event"] == "workflow_dispatch" and run["status"] == "completed"
        and run["conclusion"] == "failure" and run["head_branch"] == "main"
        and run["head_sha"] == SOURCE_SHA
        and run["path"] == ".github/workflows/data-pipeline.yml"
        and run["repository"]["full_name"] == REPOSITORY
        and run["head_repository"]["full_name"] == REPOSITORY
        and artifact["id"] == ARTIFACT_ID and artifact["name"] == ARTIFACT_NAME
        and artifact["expired"] is False and artifact["size_in_bytes"] == ARTIFACT_BYTES
        and artifact["digest"] == ARTIFACT_DIGEST
        and artifact["workflow_run"]["id"] == RUN_ID
        and artifact["workflow_run"]["head_sha"] == SOURCE_SHA
        and artifact["workflow_run"]["head_branch"] == "main"
    ):
        raise ValueError("provenance_rejected")
    return {"provenance_validated": True}


def safe_reason(value):
    if value is None:
        return None
    return value if value in SAFE_REASONS else "unrecognized_code_redacted"


def summarize(root):
    # Import only the frozen schema/policy modules, not transport or crawler code.
    from yanyue.schema import validate
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()} != {"checkpoint.json"}:
        raise ValueError("invalid_checkpoint_directory")
    value = read_json(root / "checkpoint.json", MAX_EXPANDED_BYTES)
    validate(value, "checkpoint.schema.json")
    if value["source_commit"] != SOURCE_SHA:
        raise ValueError("source_identity_rejected")
    urls = {job["url"] for job in value["queue"]}
    if len(urls) != len(value["queue"]) or any(
        edge["parent_url"] not in urls or edge["child_url"] not in urls for edge in value["edges"]
    ):
        raise ValueError("checkpoint_graph_rejected")
    budget = value["budget_summary"]
    if budget is not None and (budget["max_requests"], budget["max_bytes"], budget["max_seconds"]) != (5, 10485760, 180):
        raise ValueError("pilot_budget_identity_rejected")
    pause, result = value["pause"], value["last_result"]
    counts = {name: len(value[name]) for name in ("queue", "edges", "products", "brands", "brand_reports")}
    return {
        "checkpoint_validated": True,
        "source_commit_verified": True,
        "paused": pause is not None,
        "pause_reason": safe_reason(pause["reason"] if pause else None),
        "stop_reason": safe_reason(result["stop_reason"] if result else None),
        "pages_completed_this_run": result["pages_completed_this_run"] if result else None,
        "requests": budget["requests"] if budget else None,
        "bytes": budget["bytes"] if budget else None,
        **counts,
        "images_included": False,
        "whole_website_complete": False,
        "publication_ready": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    provenance = sub.add_parser("provenance")
    provenance.add_argument("--run", required=True)
    provenance.add_argument("--artifact", required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        if args.command == "provenance":
            output = verify_provenance(read_json(args.run, 2 * 1024 * 1024), read_json(args.artifact, 2 * 1024 * 1024))
        else:
            output = summarize(args.input)
    except Exception:
        print('{"diagnostic_error":"validation_failed_no_payload_logged"}')
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
