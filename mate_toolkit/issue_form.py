"""Generate a GitHub issue form from the profile, and expose the SAME field spec the parser
uses — so the form, the issue→crate mapping, and validation can't drift. Field id == profile
field name, so `from_issue` maps each answer straight back to its schema.org `property`.
"""
import yaml

_PEOPLE_HELP = 'One per line: an ORCID iD (e.g. 0000-0002-1270-4377), or "Family, Given".'
_LIST_HELP = "Comma-separated."

_INTRO = ("Describe your model. On submit, an automated action writes these values into the "
          "model's RO-Crate (`ro-crate-metadata.json`) — the single source of truth. "
          "**Edit the crate afterwards (CLI / editor), not by reopening this issue.**")


def form_spec(profile):
    """Ordered list of field specs shared by the form generator and the issue parser."""
    specs = []
    for name, fdef in (profile.get("root", {}) or {}).get("fields", {}).items():
        specs.append({
            "id": name, "label": fdef.get("label", name), "input": fdef.get("input", "text"),
            "property": fdef.get("property", name), "options": fdef.get("options"),
            "required": bool(fdef.get("required")), "enrich": fdef.get("enrich"), "target": "root",
        })
    payload = profile.get("payload", {}) or {}
    if payload.get("backends"):
        specs.append({"id": "payload_backend", "target": "payload", "input": "dropdown",
                      "label": "External data payload — backend (optional)",
                      "options": ["(none)"] + list(payload["backends"]), "required": False})
        specs.append({"id": "payload_ref", "target": "payload", "input": "input", "required": False,
                      "label": "Payload reference (e.g. Zenodo record id, or a URL)"})
    return specs


def _element(spec):
    inp = spec["input"]
    attrs = {"label": spec["label"]}
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


def build_issue_form(profile):
    body = [{"type": "markdown", "attributes": {"value": _INTRO}}]
    body += [_element(s) for s in form_spec(profile)]
    return {
        "name": "New M@TE model",
        "description": "Describe a model; an action writes it into the crate.",
        "title": "[model] ",
        "labels": ["model-submission"],
        "body": body,
    }


def write_issue_form(profile, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(build_issue_form(profile), f, sort_keys=False,
                       default_flow_style=False, allow_unicode=True)
    return out_path
