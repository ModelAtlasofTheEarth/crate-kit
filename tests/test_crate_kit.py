"""Engine safety-net — codifies the behaviours verified by hand across the vocab/role/readiness/
internal-enrich/website/from-issue work. Dependency-free: pytest discovers the `test_*` functions,
and `python tests/test_crate_kit.py` runs them with a built-in runner (no pytest needed).

Each test builds a throwaway crate in a tmp dir via the Python API (no git, no network).
"""
import contextlib
import importlib.resources as resources
import json
import shutil
import tempfile
from pathlib import Path

from crate_kit.build_crate import build_crate
from crate_kit.contextual import add_contextual
from crate_kit.describe import edit_entity, set_role
from crate_kit.contextual import _detect_type
from crate_kit.from_issue import apply_issue
from crate_kit.internal_enrich import internal_enrich
from crate_kit.issue_form import refresh_forms
from crate_kit.profile import load_profile
from crate_kit.readiness import report
from crate_kit.vocab import load_vocab
from crate_kit.website import resolve_website


@contextlib.contextmanager
def repo(files=None, profile=None):
    """A throwaway repo dir. `files` = {relpath: content}; `profile` = a packaged profile name
    written to .mate/profile.yml (default = the builtin `base`)."""
    d = Path(tempfile.mkdtemp())
    try:
        for rel, content in (files or {}).items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        if profile:
            (d / ".mate").mkdir(exist_ok=True)
            text = resources.files("crate_kit").joinpath("profiles", f"{profile}.yml").read_text(encoding="utf-8")
            (d / ".mate" / "profile.yml").write_text(text)
        build_crate(d, out_path=str(d / "ro-crate-metadata.json"), merge=True)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _graph(d):
    return json.loads((d / "ro-crate-metadata.json").read_text())["@graph"]


def _entity(d, eid):
    return next(e for e in _graph(d) if e.get("@id") == eid)


def _item(rep, needle):
    return next(i for i in rep["items"] if needle.lower() in i["label"].lower())


# ── vocab ────────────────────────────────────────────────────────────────────

def test_vocab_loads_terms_with_fields():
    v = load_vocab(load_profile())                       # base imports communication
    ga = v["graphical-abstract"]
    assert ga.refines == "ImageObject"
    assert ga.single is True
    assert ga.text == "caption"


def test_vocab_loadable_uri_vs_local():
    v = load_vocab(load_profile())
    assert v["figure"].type_value == "http://purl.org/spar/doco/Figure"   # aligned → URI
    assert v["graphical-abstract"].type_value == "graphical-abstract"      # homeless → local name


# ── role verb ────────────────────────────────────────────────────────────────

def test_role_sets_type_text_and_additionaltype():
    with repo({"figures/a.png": "x"}) as d:
        set_role(d, "figures/a.png", "graphical-abstract", caption="Hero")
        e = _entity(d, "figures/a.png")
        assert "ImageObject" in e["@type"]               # @type from `refines`
        assert e["additionalType"] == "graphical-abstract"
        assert e["caption"] == "Hero"                    # text routed to the term's field


def test_role_cardinality_moves_single_tag():
    with repo({"figures/a.png": "x", "figures/b.png": "y"}) as d:
        set_role(d, "figures/a.png", "graphical-abstract")
        set_role(d, "figures/b.png", "graphical-abstract")   # single → must move off a
        assert _entity(d, "figures/a.png").get("additionalType") is None
        assert _entity(d, "figures/b.png")["additionalType"] == "graphical-abstract"


def test_role_loadable_uri_lands_in_additionaltype():
    with repo({"figures/a.png": "x"}) as d:
        set_role(d, "figures/a.png", "figure")
        assert _entity(d, "figures/a.png")["additionalType"] == "http://purl.org/spar/doco/Figure"


# ── readiness: base floor ────────────────────────────────────────────────────

def test_base_floor_eligible_after_seed():
    with repo({"a.txt": "x"}) as d:
        assert report(d, build=False)["eligible"] is False
        edit_entity(d, ".", name="T", authors=["Doe, Jane"], sets=["license=CC-BY-4.0"])
        assert report(d, build=False)["eligible"] is True   # title+creator+license only


def test_base_image_is_encouraged_not_required():
    with repo({"a.txt": "x"}) as d:
        edit_entity(d, ".", name="T", authors=["Doe, Jane"], sets=["license=CC-BY-4.0"])
        assert report(d, build=False)["eligible"] is True   # no image, still eligible
        assert _item(report(d, build=False), "image")["tier"] == "encouraged"


# ── readiness: geoscience gates ──────────────────────────────────────────────

def test_geoscience_open_license_gate():
    with repo({"a.txt": "x"}, profile="mate-geoscience") as d:
        edit_entity(d, ".", name="T", authors=["Doe, Jane"], sets=["license=CC-BY-4.0"])
        assert _item(report(d, build=False), "open license")["met"] is True
        edit_entity(d, ".", sets=["license=https://example.com/proprietary"])
        assert _item(report(d, build=False), "open license")["met"] is False


def test_geoscience_open_payload_gate():
    with repo({"figures/a.png": "x"}, profile="mate-geoscience") as d:
        add_contextual(d, "remote_data", "https://example.org/data.zip")     # bare URL
        assert _item(report(d, build=False), "code OR")["met"] is False
        add_contextual(d, "remote_data", "https://zenodo.org/records/7455999")  # open PID
        assert _item(report(d, build=False), "code OR")["met"] is True


# ── internal enrich: LICENSE → SPDX ──────────────────────────────────────────

_MIT = 'MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"),'


def test_internal_enrich_detects_license():
    with repo({"LICENSE": _MIT}) as d:
        internal_enrich(d)
        assert _entity(d, "./")["license"] == {"@id": "MIT"}


def test_internal_enrich_gap_fills_only():
    with repo({"LICENSE": "Apache License\nVersion 2.0"}) as d:
        edit_entity(d, ".", sets=["license=CC-BY-4.0"])   # already authored
        internal_enrich(d)
        assert _entity(d, "./")["license"] == {"@id": "CC-BY-4.0"}   # not overwritten


# ── website resolver: role match + hero fallback ─────────────────────────────

def test_website_resolves_role_and_falls_back():
    with repo({"figures/a.png": "x"}) as d:
        set_role(d, "figures/a.png", "graphical-abstract", caption="C")
        site = resolve_website(d, build=False)
        assert site["graphical_abstract"]["caption"] == "C"

    with repo({"figures/a.png": "x"}) as d:                # image present, NOT tagged
        set_role(d, "figures/a.png", "figure")
        site = resolve_website(d, build=False)
        assert site["graphical_abstract"]["url"] == "figures/a.png"   # hero fell back to any image


# ── from-issue: content form routes to a role ────────────────────────────────

def test_from_issue_content_tags_a_file():
    body = ("### Which entity to edit\n\nfigures/a.png\n\n"
            "### What is this file?\n\nGraphical abstract\n\n"
            "### Caption (short; shown with the asset)\n\nHello.\n")
    with repo({"figures/a.png": "x"}) as d:
        res = apply_issue(d, body)
        assert res["edited"] == "figures/a.png"
        e = _entity(d, "figures/a.png")
        assert e["additionalType"] == "graphical-abstract" and e["caption"] == "Hello."


# ── CodeMeta / software: detection, fields, typed forms (all profile-driven) ──

def test_detect_type_refines_from_rules():
    cdef = {"type": "SoftwareApplication",
            "detect_type": [{"match": "github.com", "type": "SoftwareSourceCode"}]}
    assert _detect_type(cdef, "https://github.com/a/b", "https://github.com/a/b") == "SoftwareSourceCode"
    assert _detect_type(cdef, "https://doi.org/10.5281/zenodo.1", "https://doi.org/...") == "SoftwareApplication"


def test_add_software_detects_source_code():
    with repo({"a.txt": "x"}) as d:
        add_contextual(d, "software", "https://github.com/underworldcode/underworld2")
        e = next(x for x in _graph(d) if "github.com" in x.get("@id", ""))
        assert e["@type"] == "SoftwareSourceCode"


def test_codemeta_context_and_fields_present():
    with repo({"a.txt": "x"}) as d:
        assert any("codemeta" in str(c) for c in json.loads((d / "ro-crate-metadata.json").read_text())["@context"])
    prof = load_profile()
    fields = prof["component_types"]["SoftwareSourceCode"]["fields"]
    assert "buildInstructions" in fields and fields["buildInstructions"].get("vocab") == "codemeta"


def test_typed_form_generated_and_shapes_list_field():
    with repo({"model_code_inputs/run.py": "x=1"}) as d:
        (d / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        edit_entity(d, "model_code_inputs/", type_="SoftwareSourceCode")
        written = refresh_forms(d, d / ".github" / "ISSUE_TEMPLATE")
        assert "edit-software-source-code-entity.yml" in written
        # a typed-form submission (no explicit type) must still list-shape programmingLanguage
        edit_entity(d, "model_code_inputs/", sets=["programmingLanguage=Python, C++"])
        assert _entity(d, "model_code_inputs/")["programmingLanguage"] == ["Python", "C++"]


# ── built-in runner (no pytest) ──────────────────────────────────────────────

def _run():
    import sys
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _run()
