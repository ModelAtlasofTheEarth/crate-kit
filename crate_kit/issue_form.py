"""Generate the GitHub issue forms from the profile, and expose the SAME field vocabulary the
parser uses — so the forms, the issue→crate mapping, and validation can't drift.

Two forms, split by entity ROLE (TARGET_ARCHITECTURE.md §18):
  - CONFIGURE  -> edits the ROOT entity (the whole dataset): title, description, license, …
  - DATA       -> edits a NON-ROOT data entity (a folder/file): name, description, author, …

There is deliberately no payload field: "payload" is not a field — local data is a data entity,
remote data is a contextual entity (the references form, a later milestone). Root-only fields
live only on CONFIGURE, so scoping is solved by construction (a static GitHub form can't show
fields conditionally).
"""
import yaml

_PEOPLE_HELP = 'One per line: an ORCID iD (e.g. 0000-0002-1270-4377), or "Family, Given".'
_LIST_HELP = "Comma-separated."

_ROOT_OPT = "(the dataset itself / root)"
_TYPE_KEEP = "(keep current)"

_INTRO_CONFIGURE = (
    "Configure the dataset as a whole — title, description, license, creators. On submit, an "
    "action writes these onto the crate's **root** entity (`ro-crate-metadata.json`, the single "
    "source of truth). **Edit the crate afterwards (CLI / Crate-O), not by reopening this issue.**")
_INTRO_DATA = (
    "Edit metadata for one **local file or folder** in this dataset (a *data entity*). Pick it "
    "above, then fill only what you want to set (blank = leave as-is). On submit, an action writes "
    "it into the crate. Type-specific fields and richer editing live in **Crate-O**.")
_INTRO_CONTEXTUAL = (
    "Add a **remote** thing this dataset points to (a *contextual entity*) — a person, a "
    "publication, the software you used, a funder, or large data hosted elsewhere — by its "
    "identifier (DOI / ORCID / ROR / URL). An action mints it in the crate, links it to the "
    "dataset, and `enrich` fills in the details. *(Local things — files/folders — are data "
    "entities; use the other forms for those.)*")

_CONFIGURE_DEFAULTS = {"name": "Configure dataset (the whole crate)", "title": "[configure crate] ", "labels": ["crate-edit"]}
_DATA_DEFAULTS = {"name": "Edit a data entity (a local file/folder)", "title": "[edit data] ", "labels": ["crate-edit"]}
_CONTEXTUAL_DEFAULTS = {"name": "Add a contextual entity (a remote reference)", "title": "[add reference] ", "labels": ["crate-edit"]}

# Universal fields for ANY non-root data entity. Type-specific depth (programmingLanguage,
# variableMeasured, …) is Crate-O's job — a static form can't reveal it per chosen type.
_DATA_FIELDS = [
    {"id": "ent_name", "label": "Name", "input": "text", "property": "name", "target": "entity"},
    {"id": "ent_description", "label": "Description", "input": "textarea", "property": "description", "target": "entity"},
    {"id": "ent_keywords", "label": "Keywords", "input": "list", "property": "keywords", "target": "entity"},
    {"id": "ent_author", "label": "Author(s) — ORCID iD or 'Family, Given'", "input": "people", "target": "entity"},
]


def _contextual_specs(profile):
    """Specs for the 'Add a contextual entity' form. The kind dropdown is generated from the
    profile's `contextual:` block; the spec carries a label->key map so the parser can resolve it."""
    kinds = profile.get("contextual", {}) or {}
    if not kinds:
        return []
    label_to_key = {cdef.get("label", k): k for k, cdef in kinds.items()}
    hints = "; ".join(f"{cdef.get('label', k)} → {cdef.get('id_hint', '')}" for k, cdef in kinds.items())
    return [
        {"id": "ctx_kind", "role": "kind", "input": "dropdown", "required": True,
         "label": "What are you adding?", "options": list(label_to_key), "kinds": label_to_key},
        {"id": "ctx_ref", "role": "ref", "input": "text", "required": True,
         "label": "Reference (DOI / ORCID / ROR / URL)", "help": hints},
        {"id": "ctx_name", "role": "cname", "input": "text", "required": False,
         "label": "Name (optional — otherwise filled by enrich)"},
    ]


def _root_specs(profile):
    out = []
    for name, fdef in (profile.get("root", {}) or {}).get("fields", {}).items():
        out.append({
            "id": name, "label": fdef.get("label", name), "input": fdef.get("input", "text"),
            "property": fdef.get("property", name), "options": fdef.get("options"),
            "required": False, "enrich": fdef.get("enrich"), "target": "root",
        })
    return out


def _path_spec(dirs):
    # LABEL must be identical whether dropdown (form has live dirs) or text (parser, no dirs) —
    # the parser keys on labels, so they can't drift.
    if dirs:
        return {"id": "path", "role": "path", "input": "dropdown", "required": False,
                "label": "Which entity to edit", "options": [_ROOT_OPT] + list(dirs)}
    return {"id": "path", "role": "path", "input": "text", "required": False,
            "label": "Which entity to edit", "help": "folder/file path; blank = the dataset root"}


def _type_spec(profile):
    opts = [_TYPE_KEEP] + list((profile.get("component_types", {}) or {}).keys())
    if len(opts) <= 1:
        return None
    return {"id": "entity_type", "role": "type", "input": "dropdown", "required": False,
            "label": "Type tag (optional — type-specific fields are edited in Crate-O)", "options": opts}


def parser_specs(profile):
    """The UNION field vocabulary used by `from_issue` to map a submitted issue (from EITHER form)
    back to an edit-intent. Keyed downstream by label, so overlapping labels just share intent."""
    specs = [_path_spec(None)]
    t = _type_spec(profile)
    if t:
        specs.append(t)
    specs += _root_specs(profile)
    specs += _DATA_FIELDS
    specs += _contextual_specs(profile)
    return specs


def _element(spec):
    inp = spec["input"]
    attrs = {"label": spec["label"]}
    if spec.get("help"):
        attrs["description"] = spec["help"]
    if inp == "dropdown":
        etype = "dropdown"
        attrs["options"] = list(spec.get("options") or [])
    elif inp in ("textarea", "people"):
        etype = "textarea"
        if inp == "people":
            attrs["description"] = _PEOPLE_HELP
    elif inp == "list":
        etype = "input"
        attrs["description"] = _LIST_HELP
    else:
        etype = "input"
    element = {"type": etype, "id": spec["id"], "attributes": attrs}
    if etype in ("input", "textarea", "dropdown"):
        element["validations"] = {"required": bool(spec.get("required"))}
    return element


def _wrap(meta, intro, specs, title=None, labels=None, name=None):
    body = [{"type": "markdown", "attributes": {"value": intro}}]
    body += [_element(s) for s in specs]
    return {
        "name": name or meta["name"],
        "description": meta["name"],
        "title": title or meta["title"],          # GitHub gate prefix — surface, not profile
        "labels": labels or meta["labels"],
        "body": body,
    }


def build_configure_form(profile, title=None, labels=None):
    """Form 1: edit the ROOT entity (the whole dataset). No path, no type, no payload."""
    form = profile.get("form", {}) or {}
    return _wrap(_CONFIGURE_DEFAULTS, form.get("intro_configure", _INTRO_CONFIGURE),
                 _root_specs(profile), title=title, labels=labels, name=form.get("name_configure"))


def build_data_entity_form(profile, dirs=None, title=None, labels=None):
    """Form 2: edit a NON-ROOT data entity. Path selector (live dir dropdown) + type tag +
    universal fields. `dirs` is a GitHub-surface knob passed by the build workflow."""
    specs = [_path_spec(dirs)]
    t = _type_spec(profile)
    if t:
        specs.append(t)
    specs += _DATA_FIELDS
    return _wrap(_DATA_DEFAULTS, _INTRO_DATA, specs, title=title, labels=labels)


def build_contextual_form(profile, title=None, labels=None):
    """Form 3: add a contextual entity (a 'remote' reference) by PID. Kinds from the profile."""
    return _wrap(_CONTEXTUAL_DEFAULTS, _INTRO_CONTEXTUAL, _contextual_specs(profile),
                 title=title, labels=labels)


def write_form(profile, out_path, kind="data", dirs=None, title=None, labels=None):
    if kind == "configure":
        form = build_configure_form(profile, title=title, labels=labels)
    elif kind == "contextual":
        form = build_contextual_form(profile, title=title, labels=labels)
    else:
        form = build_data_entity_form(profile, dirs=dirs, title=title, labels=labels)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(form, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return out_path
