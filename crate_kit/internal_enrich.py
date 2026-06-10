"""internal enrich: derive metadata from the repo's OWN CONTENT (the sibling of external enrich,
which resolves remote PIDs). Both are best-effort + GAP-FILL only — a value already authored is
never overwritten, and a failure is skipped, never fatal.

Where external enrich answers "what does this DOI/ORCID resolve to?", internal enrich answers
"what can I read off the files already here?":
  • license     — a LICENSE/COPYING file → an SPDX id on root.license (helps clear the open-license
                  floor without hand-typing).
  • docstrings  — (later) a module/notebook docstring → a data entity's description.
  • exif        — (later) image EXIF/caption → an ImageObject caption.

The profile opts in via `internal_enrich: [license, …]`; each name maps to a DETECTOR here. Adding a
detector is a small function + a profile entry — same "capacity in the engine, policy in the profile"
split as the rest of the toolkit.
"""
import json
from pathlib import Path

from .profile import load_profile

# Ordered SPDX signatures: the first license whose ALL markers appear (case-insensitive) wins.
# Order matters — more specific variants first (AGPL/LGPL before GPL; ShareAlike before plain BY).
_LICENSE_SIGNATURES = [
    ("CC0-1.0",        ["cc0 1.0 universal"]),
    ("CC-BY-SA-4.0",   ["creative commons attribution-sharealike 4.0"]),
    ("CC-BY-4.0",      ["creative commons attribution 4.0"]),
    ("AGPL-3.0",       ["affero general public license", "version 3"]),
    ("LGPL-3.0",       ["lesser general public license", "version 3"]),
    ("GPL-3.0",        ["gnu general public license", "version 3"]),
    ("MPL-2.0",        ["mozilla public license", "version 2.0"]),
    ("Apache-2.0",     ["apache license", "version 2.0"]),
    ("BSD-3-Clause",   ["redistribution and use in source and binary forms", "neither the name"]),
    ("BSD-2-Clause",   ["redistribution and use in source and binary forms"]),
    ("MIT",            ["permission is hereby granted, free of charge", 'the "software"']),
]

_LICENSE_FILENAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md",
                      "LICENCE.txt", "COPYING", "COPYING.md"]


def _spdx_from_text(text):
    low = text.lower()
    for spdx, markers in _LICENSE_SIGNATURES:
        if all(m in low for m in markers):
            return spdx
    return None


def _detect_license(repo_dir, doc, root):
    """LICENSE/COPYING file → SPDX id on root.license. Gap-fill: skip if a license is already set."""
    if root.get("license"):
        return None
    for fn in _LICENSE_FILENAMES:
        p = repo_dir / fn
        if not p.exists():
            continue
        try:
            spdx = _spdx_from_text(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            spdx = None
        if spdx:
            root["license"] = {"@id": spdx}
            return {"detector": "license", "file": fn, "license": spdx}
        return {"detector": "license", "file": fn, "license": None,
                "note": "found a license file but couldn't identify the SPDX id"}
    return None


DETECTORS = {"license": _detect_license}


def internal_enrich(repo_dir, out_path=None):
    """Run the profile's enabled internal detectors over the crate. Returns {applied: [...]}.

    Default (no `internal_enrich:` key) runs nothing — opt-in keeps it predictable. Each detector is
    best-effort + gap-fill; the crate is rewritten only with non-derived values, so build-as-merge
    preserves them."""
    repo_dir = Path(repo_dir).resolve()
    crate_path = repo_dir / "ro-crate-metadata.json"
    if not crate_path.exists():
        return {"error": "no crate to enrich (run build first)"}

    enabled = load_profile(repo_dir).get("internal_enrich", []) or []
    doc = json.loads(crate_path.read_text())
    root = next((e for e in doc["@graph"] if e.get("@id") == "./"), None)
    if root is None:
        return {"error": "crate has no root entity"}

    applied = []
    for name in enabled:
        fn = DETECTORS.get(name)
        if not fn:
            continue
        res = fn(repo_dir, doc, root)
        if res:
            applied.append(res)

    Path(out_path or crate_path).write_text(json.dumps(doc, indent=2))
    return {"applied": applied}
