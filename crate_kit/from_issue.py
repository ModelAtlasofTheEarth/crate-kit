"""from-issue: apply a submitted GitHub issue-form to the crate.

Two forms (Configure root / Edit data entity) both produce the same edit-intent —
`(path, @type, {property: value})` — applied through the SAME `edit_entity` the CLI uses, so the
issue, the CLI, and Crate-O can't drift. The parser uses the UNION field vocabulary
(`issue_form.parser_specs`), so it handles either form: the Configure form carries no path field
(→ target is the root), the Data form carries a path. `citation` (a reference to another entity)
is applied as a small post-pass.

The issue is create/edit-time capture only; the crate is authoritative thereafter.
"""
import json
import re
from pathlib import Path

from .describe import edit_entity, command_for
from .issue_form import parser_specs, _ROOT_OPT, _TYPE_KEEP
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


def apply_issue(repo_dir, body, out_path=None):
    repo_dir = Path(repo_dir).resolve()
    profile = load_profile(repo_dir)
    parsed = parse_issue_body(body)
    speclist = list(parser_specs(profile))                   # union of all three forms' fields
    specs = {s["label"]: s for s in speclist}

    # Contextual form? (the "What are you adding?" kind field is filled) → add by reference.
    kind_spec = next((s for s in speclist if s.get("role") == "kind"), None)
    if kind_spec and parsed.get(kind_spec["label"], "") not in _EMPTY:
        from .contextual import add_contextual
        key = kind_spec.get("kinds", {}).get(parsed[kind_spec["label"]], parsed[kind_spec["label"]])
        ref = next((parsed.get(s["label"], "") for s in speclist if s.get("role") == "ref"), "")
        cname = next((parsed.get(s["label"], "") for s in speclist if s.get("role") == "cname"), "")
        res = add_contextual(repo_dir, key, ref.strip(), name=(None if cname in _EMPTY else cname))
        if res.get("error"):
            return res
        return {"applied": [res.get("link")], "edited": res.get("added"),
                "command": res.get("command"), "out": str(repo_dir / "ro-crate-metadata.json")}

    target, type_ = ".", None
    name = description = publication = None
    authors, sets = [], []

    for label, spec in specs.items():
        val = parsed.get(label, "")
        if val in _EMPTY:
            continue
        role = spec.get("role")
        if role in ("kind", "ref", "cname"):
            continue   # contextual-only fields (handled above)
        if role == "path":
            target = "." if val in (_ROOT_OPT, "(root)") else val.strip()
        elif role == "type":
            type_ = None if val == _TYPE_KEEP else val.strip()
        elif spec.get("input") == "people":
            authors = [line for line in val.splitlines() if line.strip()]
        elif spec.get("enrich") == "publication":
            publication = val
        else:
            prop = spec.get("property")
            if prop == "name":
                name = val
            elif prop == "description":
                description = val
            elif spec.get("input") == "dropdown" and prop == "license" and val == "(other URL)":
                continue
            elif prop:
                sets.append(f"{prop}={val}")   # edit_entity shapes lists/license by the field def

    # 1) the universal edit, via the shared editor (same code path as the CLI)
    result = edit_entity(repo_dir, target, type_=type_, name=name, description=description,
                         authors=authors or None, sets=sets or None)
    if result.get("error"):
        return result
    tid = result.get("edited")
    applied = list(result.get("applied", []))

    # 2) citation reference (a link to another work — valid on any entity) as a small post-pass
    crate_path = Path(out_path) if out_path else repo_dir / "ro-crate-metadata.json"
    if publication:
        doc = json.loads(crate_path.read_text())
        entity = next((e for e in doc["@graph"] if e.get("@id") == tid), None)
        if entity is not None:
            entity["citation"] = {"@id": publication if publication.startswith("http")
                                  else f"https://doi.org/{publication}"}
            applied.append("citation")
            crate_path.write_text(json.dumps(doc, indent=2))

    command = command_for(target, type_, name, description, authors, sets)
    return {"applied": applied, "edited": tid, "command": command, "out": str(crate_path)}
