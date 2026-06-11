"""Load controlled vocabularies — the ONE place that reads a vocabulary file.

A vocabulary (e.g. `communication.yml`) defines TERMS used as `additionalType` refinements on
schema.org types (graphical-abstract on an ImageObject, …). A profile opts in via `imports:`; this
module loads those vocabularies (vendored, pinned snapshots under `crate_kit/vocabularies/`) and
returns a single flat lookup of normalised Terms.

That lookup is the contract every consumer reads — and nothing else opens the file:
  • the issue-form generator → builds the role dropdown (label + definition);
  • `crate role` / `set_role`  → stamps a term onto an entity (sets @type from `refines`,
    writes the right text field, enforces cardinality);
  • `validate`                → checks the crate against the terms (e.g. a `single` term used twice).
"""
import importlib.resources as resources

import yaml


class Term:
    """One vocabulary term, normalised. The fields a consumer needs, nothing else."""

    __slots__ = ("name", "label", "definition", "refines", "cardinality", "text",
                 "type_value", "alignment")

    def __init__(self, name, raw):
        self.name = name
        self.label = raw.get("label", name)
        self.definition = raw.get("definition", "")
        self.refines = raw.get("refines")             # → the structural @type to set (ImageObject…)
        self.cardinality = raw.get("cardinality", "multiple")  # single | multiple
        self.text = raw.get("text")                   # caption | description (the text field it carries)
        self.alignment = raw.get("alignment", {}) or {}
        # what actually goes in additionalType: a loadable URI if the term aligns to one
        # (e.g. doco:Figure), else the local term name (resolves to our namespace via @context).
        self.type_value = self._type_value()

    def _type_value(self):
        for k, v in self.alignment.items():
            if k in ("local", "jats"):
                continue                              # local = homeless; jats = crosswalk note, not a URI
            if isinstance(v, str) and v.startswith("http"):
                return v                              # a loadable RDF URI — use it directly
        return self.name

    @property
    def single(self):
        return self.cardinality == "single"

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def _load_file(name):
    text = resources.files("crate_kit").joinpath("vocabularies", f"{name}.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def load_vocab(profile):
    """Return {term_name: Term} merged across the profile's imported vocabularies.

    A profile declares `imports: [communication, …]`. Unknown/unreadable imports are skipped (so a
    profile is never broken by a missing optional vocab). No imports → empty dict (generic, no roles).
    """
    terms = {}
    for vname in (profile.get("imports") or []):
        try:
            data = _load_file(vname)
        except (FileNotFoundError, ModuleNotFoundError):
            continue
        for tname, raw in (data.get("terms") or {}).items():
            terms[tname] = Term(tname, raw or {})
    # Discipline re-skins: profile `vocab_overrides:` swaps a term's human-facing label/definition
    # (what forms and reports show) WITHOUT touching its identity (name/type_value — what crates
    # store). E.g. MATE shows the generic `setup-diagram` as "Model setup diagram".
    for tname, ov in (profile.get("vocab_overrides") or {}).items():
        t = terms.get(tname)
        if t and isinstance(ov, dict):
            t.label = ov.get("label", t.label)
            t.definition = ov.get("definition", t.definition)
    return terms


def load_tag_terms(profile, set_name):
    """Return {term_id: Term} for one of the profile's `tag_sets:` — TAG terms through the SAME
    normalised Term contract as role vocabularies (§23: two bindings, one mechanism).

    Sources, merged in order: a `vocab:` key naming a vendored vocabulary file (how an external /
    minted DefinedTermSet plugs in — same file format as role vocabs), then inline `terms:`
    ({id, name} entries, normalised to Term with label=name). `vocab_overrides:` re-skins these
    exactly as it does role terms. Empty dict for an unknown set."""
    tset = (profile.get("tag_sets") or {}).get(set_name) or {}
    terms = {}
    if tset.get("vocab"):
        try:
            data = _load_file(tset["vocab"])
        except (FileNotFoundError, ModuleNotFoundError):
            data = {}
        for tname, raw in (data.get("terms") or {}).items():
            terms[tname] = Term(tname, raw or {})
    for t in (tset.get("terms") or []):
        if isinstance(t, dict) and "id" in t:
            terms[t["id"]] = Term(t["id"], {"label": t.get("name", t["id"]),
                                            "definition": t.get("definition", "")})
    for tname, ov in (profile.get("vocab_overrides") or {}).items():
        t = terms.get(tname)
        if t and isinstance(ov, dict):
            t.label = ov.get("label", t.label)
            t.definition = ov.get("definition", t.definition)
    return terms
