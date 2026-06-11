"""sync: THE derive pipeline, as one engine verb.

apply issue (if given) → build (merge) → validate → enrich (external + internal, best-effort)
→ refresh issue forms. This is the canonical sequence both the CI workflow's main path AND its
push-retry path run — defining it here (tested, versioned) instead of twice in workflow bash was
the point (the race-fix retry loop had become a second, drift-prone copy of the step list).

Failure semantics mirror the workflow's:
  • a failed ISSUE APPLY does not abort the pipeline — it's recorded in the result
    (`apply`/`apply_ok`) so the bot comment can report it, and the crate still re-derives;
  • build / validate / refresh-forms failures are real failures (`ok: False`, CLI exit 1);
  • enrich never fails the pipeline (network best-effort, same as the workflow steps).
"""
import json
import traceback
from pathlib import Path

from .build_crate import build_crate, write_preview
from .issue_form import refresh_forms
from .validate import validate as validate_repo


def sync(repo_dir, issue_body=None, forms_out=".github/ISSUE_TEMPLATE", strict=False):
    """Run the pipeline; return a result dict (`ok` = should the caller fail)."""
    repo_dir = Path(repo_dir).resolve()
    result = {"ok": True, "apply": None, "apply_ok": None}

    # 1) apply a submitted issue form, if any (recorded, never aborts — see module docstring;
    #    even a crash must not stop the pipeline, or the bot comment never posts = silent loss)
    if issue_body:
        from .from_issue import apply_issue
        try:
            result["apply"] = apply_issue(repo_dir, issue_body)
        except Exception as exc:
            result["apply"] = {"error": f"{type(exc).__name__}: {exc}"}
        result["apply_ok"] = not result["apply"].get("error")

    # 2) build-as-merge (refresh the derived layer, keep authored/enriched values)
    _, summary = build_crate(repo_dir, out_path=str(repo_dir / "ro-crate-metadata.json"))
    summary["preview"] = write_preview(repo_dir)
    result["build"] = summary

    # 3) validate (same strictness knob as `crate validate`)
    errors, warnings = validate_repo(repo_dir, strict=strict)
    result["validate"] = {"errors": errors, "warnings": warnings}
    if errors:
        result["ok"] = False

    # 4) enrich — best-effort by contract
    for label in ("external", "internal"):
        try:
            if label == "external":
                from .enrich import enrich as enrich_repo
                result["enrich_external"] = enrich_repo(repo_dir)
            else:
                from .internal_enrich import internal_enrich
                result["enrich_internal"] = internal_enrich(repo_dir)
        except Exception:
            result[f"enrich_{label}"] = {"skipped": traceback.format_exc(limit=1)}

    # 5) regenerate every dynamic issue form from the crate+profile
    if forms_out:
        out_dir = Path(forms_out)
        if not out_dir.is_absolute():
            out_dir = repo_dir / out_dir           # relative = relative to the REPO, not the CWD
        out_dir.mkdir(parents=True, exist_ok=True)
        result["forms"] = refresh_forms(repo_dir, out_dir)

    return result


def sync_cli(repo_dir, issue_body_path=None, forms_out=".github/ISSUE_TEMPLATE",
             strict=False, result_out=None):
    """CLI/CI adapter: read the issue body from a file (skipped when absent/empty — so the
    workflow can pass the path unconditionally), optionally write the JSON result to a file
    (more robust than stdout under login-shell/conda-run quirks)."""
    body = None
    if issue_body_path:
        p = Path(issue_body_path)
        if p.is_file() and p.stat().st_size:
            body = p.read_text(encoding="utf-8")
    result = sync(repo_dir, issue_body=body, forms_out=forms_out, strict=strict)
    if result_out:
        Path(result_out).write_text(json.dumps(result, indent=2))
    return result
