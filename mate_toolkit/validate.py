"""validate: red/green check that a repo's crate meets the M@TE contract.

Rules come from the PROFILE (profiles/mate-geoscience.yml or a repo's .mate/profile.yml),
not hard-coded: required root fields + `requires_for_website`. Render is permissive; the
validator is the gate (TARGET_ARCHITECTURE.md §6c).
"""
from pathlib import Path

from .build_crate import build_crate
from .profile import load_profile


def _satisfied(req, root, by_id, graph):
    """Is a single requirement met? A requirement is one of:
    - {"any": [...]} / {"all": [...]}  — nested combinators
    - "external_payload"               — a declared external payload entity exists
    - "model_output_data/" (ends "/")  — that dataset/directory entity exists
    - "name" / "license" / ...         — a root property is present
    """
    if isinstance(req, dict):
        if "any" in req:
            return any(_satisfied(r, root, by_id, graph) for r in req["any"])
        if "all" in req:
            return all(_satisfied(r, root, by_id, graph) for r in req["all"])
        return False
    if req == "external_payload":
        return any(e.get("additionalType") == "ExternalPayload" for e in graph)
    if isinstance(req, str) and req.endswith("/"):
        return req in by_id
    return bool(root.get(req))


def validate(repo_dir, reverse_engineer=False, profile=None):
    """Return (errors, warnings). Empty errors == valid."""
    repo_dir = Path(repo_dir)
    profile = profile or load_profile(repo_dir)

    doc, _ = build_crate(repo_dir, out_path=None, reverse_engineer=reverse_engineer)
    graph = doc["@graph"]
    by_id = {e.get("@id"): e for e in graph}
    root = by_id.get("./", {})

    errors, warnings = [], []
    reported = set()

    # 1) required root fields (well-formedness) — from the profile
    for fname, fdef in (profile.get("root", {}).get("fields", {}) or {}).items():
        if fdef.get("required"):
            prop = fdef.get("property", fname)
            if not root.get(prop):
                errors.append(f"missing required field `{fname}` (root.{prop})")
                reported.add(prop)

    # 2) website-eligibility gate — from the profile
    for req in profile.get("requires_for_website", []) or []:
        if isinstance(req, str) and req in reported:
            continue  # already reported as a missing required field
        if not _satisfied(req, root, by_id, graph):
            errors.append(f"not website-eligible: requires {req!r}")

    # 3) referenced local files must exist
    for e in graph:
        if e.get("@type") != "File":
            continue
        i = e.get("@id", "")
        if not i or i.startswith(("http", "#", "./")):
            continue
        if not (repo_dir / i).exists():
            errors.append(f"crate references missing local file `{i}`")

    # soft checks
    if not root.get("description"):
        warnings.append("root entity has no description")

    return errors, warnings
