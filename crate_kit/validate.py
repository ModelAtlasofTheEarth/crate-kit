"""validate: red/green check that a repo's crate meets the M@TE contract.

Rules come from the PROFILE (profiles/mate-geoscience.yml or a repo's .mate/profile.yml),
not hard-coded: required root fields + `requires_for_website`. Render is permissive; the
validator is the gate (TARGET_ARCHITECTURE.md §6c).
"""
from pathlib import Path
from urllib.parse import unquote

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


def validate(repo_dir, reverse_engineer=False, profile=None, strict=False):
    """Return (errors, warnings). Empty errors == valid.

    Two tiers:
    - STRUCTURAL (always hard errors): the crate is broken — it references a local file that
      doesn't exist. (ro-crate-py's deep check is a warning; build repairs most of it.)
    - READINESS (soft by default): the crate is well-formed but not yet catalogue-ready —
      missing required root fields / website-eligibility. A fresh, unseeded repo is legitimately
      not-ready, so the build pipeline shouldn't go red over it. Pass strict=True (the explicit
      `mate validate --strict` gate, e.g. for registry submission) to escalate these to errors.
    """
    repo_dir = Path(repo_dir)
    profile = profile or load_profile(repo_dir)

    doc, _ = build_crate(repo_dir, out_path=None, reverse_engineer=reverse_engineer)
    graph = doc["@graph"]
    by_id = {e.get("@id"): e for e in graph}
    root = by_id.get("./", {})

    errors, warnings, readiness = [], [], []
    reported = set()

    # 1) required root fields (well-formedness) — from the profile [readiness]
    for fname, fdef in (profile.get("root", {}).get("fields", {}) or {}).items():
        if fdef.get("required"):
            prop = fdef.get("property", fname)
            if not root.get(prop):
                readiness.append(f"missing required field `{fname}` (root.{prop})")
                reported.add(prop)

    # 2) website-eligibility gate — from the profile [readiness]
    for req in profile.get("requires_for_website", []) or []:
        if isinstance(req, str) and req in reported:
            continue  # already reported as a missing required field
        if not _satisfied(req, root, by_id, graph):
            readiness.append(f"not website-eligible: requires {req!r}")

    # 3) referenced local files must exist [structural — always hard]. The @id is URL-encoded
    #    per RO-Crate (a space becomes %20, etc.), so DECODE before hitting the filesystem.
    for e in graph:
        if e.get("@type") != "File":
            continue
        i = e.get("@id", "")
        if not i or i.startswith(("http", "#", "./")):
            continue
        if not (repo_dir / unquote(i)).exists():
            errors.append(f"crate references missing local file `{i}`")

    # 4) deep structural check via ro-crate-py on the ON-DISK crate (strict: hasPart
    #    completeness, references, …). Catches editor mangling that our in-memory build-repair
    #    would otherwise mask. A warning, not an error — build repairs most of it on next run.
    crate_file = repo_dir / "ro-crate-metadata.json"
    if crate_file.exists():
        try:
            from rocrate.rocrate import ROCrate
            ROCrate(str(repo_dir))
        except Exception as exc:
            warnings.append(f"ro-crate-py structural check on the committed crate: {exc}")

    # soft checks
    if not root.get("description"):
        warnings.append("root entity has no description")

    # readiness: hard under --strict (a gate), otherwise informational warnings so a fresh /
    # unseeded repo still builds green.
    if strict:
        errors += readiness
    else:
        warnings += [f"not yet catalogue-ready: {r}" for r in readiness]

    return errors, warnings
