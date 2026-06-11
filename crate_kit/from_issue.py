"""from-issue: apply a submitted GitHub issue-form to the crate.

Two forms (Configure root / Edit data entity) both produce the same edit-intent —
`(path, @type, {property: value})` — applied through the SAME `edit_entity` the CLI uses, so the
issue, the CLI, and Crate-O can't drift. The parser uses the UNION field vocabulary
(`issue_form.parser_specs`), so it handles either form: the Configure form carries no path field
(→ target is the root), the Data form carries a path. `citation` (a reference to another entity)
is applied as a small post-pass.

The issue is create/edit-time capture only; the crate is authoritative thereafter.
"""
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


def _next_form(profile, type_):
    """R4 of the form grammar — the bot narrates the two-step. If the applied edit gave an entity a
    @type whose component type spawns a typed form (`form: true`), return that form's NAME (exactly
    what `refresh_forms` will put in the issue chooser after this build) so the workflow comment
    can point the user at the next step instead of leaving it silent."""
    if not type_:
        return None
    ct = profile.get("component_types", {}) or {}
    for t in (type_ if isinstance(type_, list) else [type_]):
        tcfg = ct.get(t) or {}
        if tcfg.get("form"):
            return f"Edit a {tcfg.get('label', t)} entity"
    return None


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
                "command": res.get("command"), "next_form": _next_form(profile, res.get("type")),
                "out": str(repo_dir / "ro-crate-metadata.json")}

    # Content form? (the "What is this file?" role field is filled) → tag a website role.
    role_spec = next((s for s in speclist if s.get("role") == "role"), None)
    if role_spec and parsed.get(role_spec["label"], "") not in _EMPTY:
        from .describe import set_role, command_for_role
        role_name = role_spec.get("roles", {}).get(parsed[role_spec["label"]], parsed[role_spec["label"]])
        path = next((parsed.get(s["label"], "") for s in speclist if s.get("role") == "path"), "")
        path = "." if path in (_ROOT_OPT, "(root)", "") else path.strip()
        cap = next((parsed.get(s["label"], "") for s in speclist if s.get("role") == "caption"), "")
        cap = None if cap in _EMPTY else cap
        res = set_role(repo_dir, path, role_name, caption=cap)
        if res.get("error"):
            return res
        return {"applied": ["additionalType"], "edited": res.get("roled"),
                "command": command_for_role(path, role_name, cap),
                "next_form": _next_form(profile, res.get("type")),
                "out": str(repo_dir / "ro-crate-metadata.json")}

    target, type_ = ".", None
    name = description = publication = None
    authors, sets, tags = [], [], []

    for label, spec in specs.items():
        val = parsed.get(label, "")
        if val in _EMPTY:
            continue
        role = spec.get("role")
        if role in ("kind", "ref", "cname"):
            continue   # contextual-only fields (handled above)
        if role == "tag":
            labels = [x.strip() for x in re.split(r"[,\n]", val) if x.strip()]
            ids = [spec.get("tagmap", {}).get(l, l) for l in labels]
            tags.append((spec.get("tag_set"), ids))
            continue
        if role == "path":
            target = "." if val in (_ROOT_OPT, "(root)") else val.strip()
        elif role == "type":
            v = val.strip()
            type_ = None if v == _TYPE_KEEP else spec.get("typemap", {}).get(v, v)   # label → @type key
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

    # 2) citation reference (a link to another work — valid on any entity), via the engine verb
    crate_path = Path(out_path) if out_path else repo_dir / "ro-crate-metadata.json"
    if publication:
        from .contextual import link_citation
        if not link_citation(repo_dir, tid, publication).get("error"):
            applied.append("citation")

    # tag post-pass: apply controlled tags (DefinedTerm) picked on the configure form's tag dropdowns
    if tags:
        from .tags import apply_tag
        for set_name, ids in tags:
            if set_name and ids:
                apply_tag(repo_dir, set_name, ids)
                applied.append(f"tag:{set_name}")

    command = command_for(target, type_, name, description, authors, sets)
    return {"applied": applied, "edited": tid, "command": command,
            "next_form": _next_form(profile, type_), "out": str(crate_path)}
