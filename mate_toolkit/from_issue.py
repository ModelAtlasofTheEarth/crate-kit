"""from-issue: turn a submitted GitHub issue-form into authored crate metadata.

The issue is *create-time capture* — its answers are written into the crate (the single source
of truth) and the issue is not authoritative thereafter. Mapping is drift-free: the issue form
and this parser share `issue_form.form_spec`, and each field's id → schema.org `property`.
"""
import json
import re
from pathlib import Path

from .build_crate import build_crate, _root_entity, _person
from .issue_form import form_spec
from .payload import _adapter
from .profile import load_profile

_HEAD = re.compile(r"^###\s+(?P<label>.+?)\s*$", re.M)
_EMPTY = ("", "_No response_", "_no response_")


def parse_issue_body(body):
    """GitHub renders an issue-form submission as '### <label>\\n\\n<value>'. Return {label: value}."""
    out = {}
    heads = list(_HEAD.finditer(body or ""))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        out[m.group("label").strip()] = body[m.end():end].strip()
    return out


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "person").lower()).strip("-")


def _ensure_person(doc, spec):
    pid = spec.get("@id") or "#person-" + _slug(spec.get("name"))
    if not any(e.get("@id") == pid for e in doc["@graph"]):
        ent = {"@id": pid, "@type": "Person"}
        if spec.get("name"):
            ent["name"] = spec["name"]
        doc["@graph"].append(ent)
    return {"@id": pid}


def apply_issue(repo_dir, body, out_path=None):
    repo_dir = Path(repo_dir).resolve()
    profile = load_profile(repo_dir)
    parsed = parse_issue_body(body)
    specs = {s["label"]: s for s in form_spec(profile)}

    # derived-only base (ignore front-matter; the issue is the authored source), merged with any
    # existing crate so a re-submission updates without losing the file manifest.
    doc, summary = build_crate(repo_dir, merge=True, seed_authored=False)
    root = _root_entity(doc)

    applied, backend, ref = [], None, None
    for label, spec in specs.items():
        val = parsed.get(label, "")
        if val in _EMPTY:
            continue
        if spec["target"] == "payload":
            if spec["id"] == "payload_backend":
                backend = val
            else:
                ref = val
            continue
        prop, inp = spec["property"], spec["input"]
        if inp == "people":
            people = [_person(line) for line in val.splitlines() if line.strip()]
            root["creator"] = [_ensure_person(doc, p) for p in people]
        elif inp == "list":
            root[prop] = [x.strip() for x in val.split(",") if x.strip()]
        elif inp == "dropdown" and prop == "license":
            if val != "(other URL)":
                root[prop] = {"@id": val}
        else:
            root[prop] = val
        applied.append(prop)

    if backend and backend not in ("(none)", "") and ref:
        spec = {"backend": backend, ("record" if backend == "zenodo" else "url"): ref}
        ent, backing, _ = _adapter(spec)
        if ent["@id"]:
            ent["about"] = {"@id": backing}
            doc["@graph"] = [e for e in doc["@graph"] if e.get("additionalType") != "ExternalPayload"]
            doc["@graph"].append(ent)
            for e in doc["@graph"]:
                if e.get("@id") == backing:
                    e.setdefault("distribution", []).append({"@id": ent["@id"]})
                if e.get("@id") == "./":
                    e.setdefault("hasPart", []).append({"@id": ent["@id"]})
            applied.append("payload")

    out_path = out_path or str(repo_dir / "ro-crate-metadata.json")
    Path(out_path).write_text(json.dumps(doc, indent=2))
    return {"applied": applied, "out": out_path, "base_mode": summary.get("mode")}
