"""validate: red/green check that a repo's crate meets the M@TE contract.

Rules come from the PROFILE (profiles/mate-geoscience.yml or a repo's .mate/profile.yml),
not hard-coded: required root fields + `requires_for_website`. Render is permissive; the
validator is the gate (TARGET_ARCHITECTURE.md §6c).
"""
from pathlib import Path
from urllib.parse import unquote

from .build_crate import build_crate
from .profile import load_profile
from .vocab import load_vocab


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

    # 2) catalogue-readiness tiers — from the profile's `readiness:` block (falls back to the
    #    legacy `requires_for_website` as the required tier). REQUIRED unmet → readiness (soft/strict
    #    as below); ENCOURAGED unmet → an informational warning (never gates the build).
    from .readiness import evaluate
    tiers = evaluate(profile, graph, by_id, root)
    for item in tiers["required"]:
        pred = item["predicate"]
        # a {property: X} or bare-string predicate that's already flagged as a missing required root
        # field shouldn't be double-reported.
        key = pred.get("property") if isinstance(pred, dict) else (pred if isinstance(pred, str) else None)
        if key and key in reported:
            continue
        if not item["met"]:
            readiness.append(f"not catalogue-eligible: needs {item['label']}")
    for item in tiers["encouraged"]:
        if not item["met"]:
            warnings.append(f"encouraged (a thin page without it): {item['label']}")

    # 2b) role cardinality — a `single` vocabulary role (graphical-abstract, model-setup-diagram)
    #     must not be carried by more than one entity. Structural error (the crate is inconsistent;
    #     the website can't pick a hero). Only known terms with single cardinality are checked.
    vocab = load_vocab(profile)
    for term in vocab.values():
        if not term.single:
            continue
        holders = [e.get("@id") for e in graph
                   if term.type_value in ([e["additionalType"]] if isinstance(e.get("additionalType"), str)
                                           else (e.get("additionalType") or []))]
        if len(holders) > 1:
            errors.append(f"role `{term.name}` is single-use but tags {len(holders)} entities: "
                          f"{', '.join(holders)}")

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
