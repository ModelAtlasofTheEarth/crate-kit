"""Generate a Crate-O 'mode file' from the profile, so the no-code web editor knows our entity
types and fields. Same profile that drives the issue form and validation — here it drives the
editor. (One profile, many consumers.)

Mode-file shape (from Language-Research-Technology/ro-crate-modes):
  { metadata, rootDataEntity, lookup, classes:{ <Class>: {inputs:[{id,name,help,multiple,type}]}}}
"""
import json

from .profile import load_profile

SCHEMA = "http://schema.org/"

# property -> Crate-O target type(s); default is plain Text
_TYPES = {
    "creator": ["Person"], "author": ["Person"],
    "citation": ["ScholarlyArticle"],
    "license": ["CreativeWork", "URL"],
    "affiliation": ["Organization"],
    "datePublished": ["Date"],
    "url": ["URL"],
    "hasPart": ["File", "Dataset"],
}


def _input(prop, help_text=None, multiple=False, types=None):
    return {"id": SCHEMA + prop, "name": prop, "help": help_text or prop,
            "multiple": bool(multiple), "type": types or _TYPES.get(prop, ["Text"])}


def build_mode(profile):
    fields = (profile.get("root", {}) or {}).get("fields", {})

    # base inputs so ANY Dataset (incl. subdirectories) can be named/described in the editor
    dataset_inputs = [_input("name", "Name of this dataset / model"),
                      _input("description", "What it is / how it was made")]
    seen = {"name", "description"}
    for fname, fdef in fields.items():
        prop = fdef.get("property", fname)
        if prop in seen:
            continue
        dataset_inputs.append(_input(prop, fdef.get("label"), fdef.get("many")))
        seen.add(prop)
    dataset_inputs.append(_input("hasPart", "Files / datasets contained here", multiple=True))

    classes = {
        "Dataset": {"hasSubclass": ["SoftwareSourceCode"], "inputs": dataset_inputs},
        "Person": {"inputs": [
            _input("givenName", "Given name"), _input("familyName", "Family name"),
            _input("name", "Full name"), _input("identifier", "ORCID iD"),
            _input("affiliation", "Affiliation", multiple=True)]},
        "ScholarlyArticle": {"inputs": [
            _input("name", "Title"), _input("author", "Authors", multiple=True),
            _input("datePublished", "Date published"), _input("url", "URL")]},
        "Organization": {"inputs": [_input("name", "Name"), _input("identifier", "ROR id")]},
        "File": {"inputs": [_input("name"), _input("description"),
                            _input("encodingFormat", "Format")]},
    }

    return {
        "metadata": {"name": f"M@TE ({profile.get('profile', 'mate')})",
                     "description": "M@TE model profile for Crate-O",
                     "version": profile.get("version", 0), "license": "GPLv3.0", "author": "M@TE"},
        "rootDataEntity": [{"type": ["Dataset"], "conformsToUri": [], "description": "A M@TE model"}],
        "lookup": {},
        "classes": classes,
    }


def write_mode(profile, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build_mode(profile), f, indent=2)
    return out_path
