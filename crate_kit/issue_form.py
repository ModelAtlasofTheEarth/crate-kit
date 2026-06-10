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
import json
import re
from pathlib import Path

import yaml

from .profile import load_profile
from .vocab import load_vocab

# Fallback ONLY — the authoritative whitelist of website-asset file types is the profile's
# `website_asset_types:` (policy lives in the profile, not the engine). A discipline tunes its own.
_DEFAULT_ASSET_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".tif", ".tiff",
                       ".mp4", ".webm", ".mov")


def _asset_exts(profile):
    raw = profile.get("website_asset_types") or _DEFAULT_ASSET_EXTS
    return tuple(("." + str(x).lstrip(".")).lower() for x in raw)

_PEOPLE_HELP = 'One per line: an ORCID iD (e.g. 0000-0002-1270-4377), or "Family, Given".'
_LIST_HELP = "Comma-separated."

_ROOT_OPT = "(the dataset itself / root)"
_TYPE_KEEP = "(keep current)"

_INTRO_CONFIGURE = (
    "Configure the dataset as a whole — title, description, license, creators. *(You can leave the "
    "issue title above as-is — it's just a marker; the dataset's title is the field below.)* On "
    "submit, an action writes these onto the crate's **root** entity (`ro-crate-metadata.json`, the "
    "single source of truth). **Edit the crate afterwards (CLI / Crate-O), not by reopening this issue.**")
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

# `title` is a COMPLETE default for the GitHub issue-title box (not just the gate prefix) — so the
# box looks done and isn't mistaken for a second "title" field. The workflow only needs the prefix.
_CONFIGURE_DEFAULTS = {"name": "Configure dataset (the whole crate)", "title": "[configure crate] dataset metadata", "labels": ["crate-edit"]}
_DATA_DEFAULTS = {"name": "Edit a data entity (a local file/folder)", "title": "[edit data] (the entity selected below)", "labels": ["crate-edit"]}
_CONTEXTUAL_DEFAULTS = {"name": "Add a contextual entity (a remote reference)", "title": "[add reference] (the reference below)", "labels": ["crate-edit"]}

# Universal fields for ANY non-root data entity. Type-specific depth (programmingLanguage,
# variableMeasured, …) is Crate-O's job — a static form can't reveal it per chosen type.
_DATA_FIELDS = [
    {"id": "ent_name", "label": "Name", "input": "text", "property": "name", "target": "entity"},
    {"id": "ent_description", "label": "Description", "input": "textarea", "property": "description", "target": "entity"},
    {"id": "ent_keywords", "label": "Keywords", "input": "list", "property": "keywords", "target": "entity"},
    {"id": "ent_author", "label": "Author(s) — ORCID iD or 'Family, Given'", "input": "people", "target": "entity"},
]


_INTRO_CONTENT = (
    "Tag a **local file** with the role it plays on the model's website — its *communicative "
    "function* (graphical abstract, model-setup diagram, figure…). Pick the file and its role; "
    "an action records the role on the crate (as `additionalType`, keeping the file's structural "
    "type) and stores your caption. The role decides where the asset appears on the page.")
_CONTENT_DEFAULTS = {"name": "Tag website content (a file's role)", "title": "[tag content] (the file below)", "labels": ["crate-edit"]}


def _role_specs(profile, dirs=None):
    """Specs for the 'Tag website content' form. The role dropdown is generated from the profile's
    imported VOCABULARY (vocab.py) — one option per term, label shown, definitions in the help.
    The spec carries a label->term map so the parser can resolve the picked role back to its name."""
    vocab = load_vocab(profile)
    if not vocab:
        return []
    label_to_term = {t.label: name for name, t in vocab.items()}
    gloss = "\n".join(f"- **{t.label}** — {t.definition}" for t in vocab.values())
    return [
        _path_spec(dirs),
        {"id": "role_term", "role": "role", "input": "dropdown", "required": True,
         "label": "What is this file?", "options": list(label_to_term),
         "help": gloss, "roles": label_to_term},
        {"id": "role_caption", "role": "caption", "input": "textarea", "required": False,
         "label": "Caption (short; shown with the asset)"},
    ]


def _tag_specs(profile, target="root"):
    """Multi-select dropdown specs for the profile's `tag_sets` that target `target`. Options are the
    set's term names; the spec carries a name->id map so the parser can resolve picks back to ids."""
    out = []
    for name, tset in (profile.get("tag_sets") or {}).items():
        if (tset.get("target") or "root") != target:
            continue
        label_to_id = {t.get("name", t["id"]): t["id"] for t in (tset.get("terms") or []) if "id" in t}
        out.append({"id": f"tag_{name}", "role": "tag", "tag_set": name, "input": "dropdown",
                    "multiple": True, "required": False, "label": tset.get("label", name),
                    "options": list(label_to_id), "tagmap": label_to_id})
    return out


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


def _typed_field_specs(profile, type_name):
    """Specs for ONE component type's fields (e.g. SoftwareSourceCode → buildInstructions, …),
    drawn straight from the profile's `component_types`. Generic — any discipline's types work."""
    fields = (profile.get("component_types", {}) or {}).get(type_name, {}).get("fields", {}) or {}
    out = []
    for prop, fdef in fields.items():
        out.append({
            "id": f"f_{prop}", "label": fdef.get("label", prop), "input": fdef.get("input", "text"),
            "property": prop, "help": fdef.get("help"), "options": fdef.get("options"),
            "required": False, "target": "entity",
        })
    return out


def _all_component_field_specs(profile):
    """Union of every component type's fields — so the parser can map a typed-form submission back
    (keyed by label; same-labelled fields across types share one property, which is fine)."""
    out, seen = [], set()
    for type_name in (profile.get("component_types", {}) or {}):
        for s in _typed_field_specs(profile, type_name):
            if s["label"] in seen:
                continue
            seen.add(s["label"])
            out.append(s)
    return out


def _root_specs(profile):
    out = []
    for name, fdef in (profile.get("root", {}) or {}).get("fields", {}).items():
        out.append({
            "id": name, "label": fdef.get("label", name), "input": fdef.get("input", "text"),
            "property": fdef.get("property", name), "options": fdef.get("options"),
            "required": False, "enrich": fdef.get("enrich"), "target": "root",
        })
    return out


def _path_spec(dirs, include_root=True):
    # Always a dropdown — options are DERIVED from the repo by the build's live refresh, never
    # hardcoded. Root is offered ONLY when include_root (Configure edits root; the non-root forms
    # don't). It's also the fallback when there are no other options, so the dropdown is never empty.
    opts = list(dirs or [])
    if include_root or not opts:
        opts = [_ROOT_OPT] + opts
    return {"id": "path", "role": "path", "input": "dropdown", "required": False,
            "label": "Which entity to edit", "options": opts}


_TYPE_HELP = ("Optional. *Source code* = your model's own code/scripts in this repo. "
              "*Application* = a packaged tool/solver you used (often better added via 'Add a "
              "reference'). Picking a type unlocks its type-specific fields on a dedicated form.")


def _type_spec(profile):
    types = profile.get("component_types", {}) or {}
    if not types:
        return None
    # options are the human LABELS (clearer than the schema keys, e.g. SoftwareSourceCode); a
    # label->key map lets the parser resolve the pick back to the @type. Help text disambiguates
    # the choice at the decision point (the App-vs-SourceCode confusion).
    label_to_key = {(tdef.get("label") or k): k for k, tdef in types.items()}
    return {"id": "entity_type", "role": "type", "input": "dropdown", "required": False,
            "label": "Type tag", "help": _TYPE_HELP,
            "options": [_TYPE_KEEP] + list(label_to_key), "typemap": label_to_key}


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
    specs += [s for s in _role_specs(profile) if s.get("role") in ("role", "caption")]
    specs += _all_component_field_specs(profile)
    specs += _tag_specs(profile, "root")
    return specs


def _element(spec):
    inp = spec["input"]
    attrs = {"label": spec["label"]}
    if spec.get("help"):
        attrs["description"] = spec["help"]
    if inp == "dropdown":
        etype = "dropdown"
        attrs["options"] = list(spec.get("options") or [])
        if spec.get("multiple"):
            attrs["multiple"] = True
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
    """Form 1: edit the ROOT entity (the whole dataset). Root fields + any root-targeted tag-set
    dropdowns (controlled categories). No path, no type, no payload."""
    form = profile.get("form", {}) or {}
    specs = _root_specs(profile) + _tag_specs(profile, "root")
    return _wrap(_CONFIGURE_DEFAULTS, form.get("intro_configure", _INTRO_CONFIGURE),
                 specs, title=title, labels=labels, name=form.get("name_configure"))


def build_data_entity_form(profile, dirs=None, title=None, labels=None):
    """Form 2: edit a NON-ROOT data entity. Path selector (live dir dropdown) + type tag +
    universal fields. `dirs` is a GitHub-surface knob passed by the build workflow."""
    specs = [_path_spec(dirs, include_root=False)]   # root is edited via Configure, not here
    t = _type_spec(profile)
    if t:
        specs.append(t)
    specs += _DATA_FIELDS
    return _wrap(_DATA_DEFAULTS, _INTRO_DATA, specs, title=title, labels=labels)


def build_contextual_form(profile, title=None, labels=None):
    """Form 3: add a contextual entity (a 'remote' reference) by PID. Kinds from the profile."""
    return _wrap(_CONTEXTUAL_DEFAULTS, _INTRO_CONTEXTUAL, _contextual_specs(profile),
                 title=title, labels=labels)


def build_content_form(profile, dirs=None, title=None, labels=None):
    """Form 4: tag a local file with a website ROLE drawn from the imported vocabulary."""
    return _wrap(_CONTENT_DEFAULTS, _INTRO_CONTENT, _role_specs(profile, dirs),
                 title=title, labels=labels)


def build_typed_entity_form(profile, type_name, dirs=None, title=None, labels=None):
    """Form 5 (generated, one per component type that has entities): edit the TYPE-SPECIFIC fields of
    an entity of `type_name`. Path dropdown = the entities of that type (passed in); fields = that
    type's `component_types`. Shares the `[edit data]` gate, so it applies through the same path."""
    label = ((profile.get("component_types", {}) or {}).get(type_name, {}) or {}).get("label", type_name)
    intro = (f"Edit the **{label}**-specific metadata for one entity below (blank = leave as-is). "
             f"These fields come from its `{type_name}` type. Universal fields (name, description, "
             f"authors) live on *Edit a data entity*; richer editing is in Crate-O.")
    specs = [_path_spec(dirs, include_root=False)] + _typed_field_specs(profile, type_name)
    meta = {"name": f"Edit a {label} entity", "title": "[edit data] ", "labels": ["crate-edit"]}
    return _wrap(meta, intro, specs, title=title, labels=labels)


def _slug(s):
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s or "")   # split camelCase: SoftwareSourceCode → …
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _types_of(e):
    t = e.get("@type")
    return t if isinstance(t, list) else ([t] if t else [])


def refresh_forms(repo_dir, out_dir):
    """Regenerate the DYNAMIC issue forms from the crate + profile (run by the build workflow). One
    engine call so the workflow stays thin and the logic is testable. Returns written paths:
      • edit-data-entity.yml   ← first-level folders            (only if the form is shipped)
      • tag-website-content.yml ← image/video files             (only if the form is shipped)
      • edit-<type>-entity.yml  ← one per component type that declares `form: true` AND has entities
    Generic: every "which" (folders, files, types, fields) comes from the crate + profile.
    """
    repo_dir, out_dir = Path(repo_dir), Path(out_dir)
    profile = load_profile(repo_dir)
    crate_path = repo_dir / "ro-crate-metadata.json"
    graph = json.loads(crate_path.read_text())["@graph"] if crate_path.exists() else []
    written = []

    # Static forms (no dynamic content) — regenerated too so they never drift from the engine/profile
    # (e.g. a relabeled field, a new license option). Git-diff in the workflow only commits real changes.
    for fn, kind in (("configure-crate.yml", "configure"), ("add-contextual-entity.yml", "contextual")):
        if (out_dir / fn).exists():
            write_form(profile, str(out_dir / fn), kind=kind)
            written.append(fn)

    folders = sorted(e["@id"] for e in graph
                     if isinstance(e.get("@id"), str) and e["@id"].endswith("/")
                     and e["@id"] != "./" and e["@id"].count("/") == 1)
    if (out_dir / "edit-data-entity.yml").exists():
        write_form(profile, str(out_dir / "edit-data-entity.yml"), kind="data",
                   dirs=folders, title="[edit data] ")
        written.append("edit-data-entity.yml")

    asset_exts = _asset_exts(profile)
    imgs = sorted(e["@id"] for e in graph
                  if "File" in _types_of(e) and isinstance(e.get("@id"), str)
                  and e["@id"].lower().endswith(asset_exts))
    if (out_dir / "tag-website-content.yml").exists():
        write_form(profile, str(out_dir / "tag-website-content.yml"), kind="content",
                   dirs=imgs, title="[tag content] ")
        written.append("tag-website-content.yml")

    for type_name, tcfg in (profile.get("component_types", {}) or {}).items():
        if not (tcfg or {}).get("form"):
            continue                                   # only types that opt in get a typed form
        ents = sorted(e["@id"] for e in graph if type_name in _types_of(e) and e.get("@id") != "./")
        fn = f"edit-{_slug(type_name)}-entity.yml"
        if not ents:
            (out_dir / fn).unlink(missing_ok=True)     # no entities of this type → no form
            continue
        write_form(profile, str(out_dir / fn), kind="typed", component_type=type_name,
                   dirs=ents, title="[edit data] ")
        written.append(fn)

    return written


def write_form(profile, out_path, kind="data", dirs=None, title=None, labels=None, component_type=None):
    if kind == "configure":
        form = build_configure_form(profile, title=title, labels=labels)
    elif kind == "contextual":
        form = build_contextual_form(profile, title=title, labels=labels)
    elif kind == "content":
        form = build_content_form(profile, dirs=dirs, title=title, labels=labels)
    elif kind == "typed":
        form = build_typed_entity_form(profile, component_type, dirs=dirs, title=title, labels=labels)
    else:
        form = build_data_entity_form(profile, dirs=dirs, title=title, labels=labels)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(form, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return out_path
