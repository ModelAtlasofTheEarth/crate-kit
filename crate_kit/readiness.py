"""readiness: the catalogue traffic-light — what a repo still needs to clear its profile's floor.

The floor is DERIVED, not decreed (see memory/minimum-asset-floor.md): the catalogue card must be
able to render, so the required tier is "title + creators + license + ≥1 image". A graphical
abstract is *encouraged*, never required. The tiers live in the profile's `readiness:` block:

    readiness:
      required:    [ {property: name}, {type: ImageObject, label: …, action_role: figure}, … ]
      encouraged:  [ {role: graphical-abstract, label: …, action_role: graphical-abstract}, … ]

Each item is a PREDICATE (one of {property|type|role|any|all} or a bare string) plus a human
`label` and an optional `action_role` (the content-form role a fix-it link pre-selects). This module
both EVALUATES items (reused by `validate`) and RENDERS the report (used by `crate readiness`).
"""
from pathlib import Path
from urllib.parse import quote

from .build_crate import build_crate
from .profile import load_profile
from .vocab import load_vocab
from .website import _types, _atypes   # reuse the same @type/additionalType readers as the resolver

_CONTENT_TEMPLATE = "tag-website-content.yml"   # the repo-side filename of the content issue form
_CONFIGURE_TEMPLATE = "configure-crate.yml"


def _license_value(root):
    lic = root.get("license")
    return lic.get("@id") if isinstance(lic, dict) else lic


def _met(pred, root, by_id, graph, role_value, profile):
    """Is one predicate satisfied? Metadata keys (label/action_role) are ignored — only the first
    predicate key matters. Predicate forms:
      {any|all: [...]}   — combinators
      {type: X}          — ≥1 entity of schema.org @type X
      {role: term}       — ≥1 entity tagged with that vocab term's additionalType
      {property: p}      — root.p is present
      {open_payload: true} — ≥1 ExternalPayload whose @id is on the profile's `open_pid_hosts`
                             allowlist (a resolvable open PID, not just any URL)
      {open_license: true} — root.license is on the profile's `open_licenses` allowlist
      "external_payload" / "dir/" / bare prop — legacy string forms.
    """
    if isinstance(pred, dict):
        if "any" in pred:
            return any(_met(p, root, by_id, graph, role_value, profile) for p in pred["any"])
        if "all" in pred:
            return all(_met(p, root, by_id, graph, role_value, profile) for p in pred["all"])
        if "type" in pred:
            return any(pred["type"] in _types(e) for e in graph)
        if "role" in pred:                                   # role names a vocab term → its type_value
            want = role_value.get(pred["role"], pred["role"])
            return any(want in _atypes(e) for e in graph)
        if "open_payload" in pred:
            hosts = profile.get("open_pid_hosts") or ["doi.org"]
            return any(e.get("additionalType") == "ExternalPayload"
                       and any(h in (e.get("@id") or "") for h in hosts) for e in graph)
        if "open_license" in pred:
            val = _license_value(root)
            allow = profile.get("open_licenses") or []
            return bool(val) and any(a == val or a in str(val) for a in allow)
        if "property" in pred:
            return bool(root.get(pred["property"]))
        return False
    if pred == "external_payload":
        return any(e.get("additionalType") == "ExternalPayload" for e in graph)
    if isinstance(pred, str) and pred.endswith("/"):
        return pred in by_id
    return bool(root.get(pred))


def _label(item):
    if isinstance(item, dict):
        return item.get("label") or item.get("property") or item.get("type") or item.get("role") or str(item)
    return str(item)


def _tier_items(profile, tier):
    """Items for a tier. If a `readiness:` block exists use it; else fall back to the legacy
    `requires_for_website` list as the required tier (so old profiles keep working)."""
    block = profile.get("readiness")
    if block is not None:
        return list(block.get(tier, []) or [])
    if tier == "required":
        return list(profile.get("requires_for_website", []) or [])
    return []


def evaluate(profile, graph, by_id, root):
    """Return {"required": [...], "encouraged": [...]}, each a list of
    {label, met, action_role, predicate}. Reused by validate (required → readiness errors) and by
    the report below."""
    role_value = {name: t.type_value for name, t in load_vocab(profile).items()}
    out = {}
    for tier in ("required", "encouraged"):
        items = []
        for it in _tier_items(profile, tier):
            items.append({
                "label": _label(it),
                "met": _met(it, root, by_id, graph, role_value, profile),
                "action_role": it.get("action_role") if isinstance(it, dict) else None,
                "predicate": it,
            })
        out[tier] = items
    return out


def _repo_url(root):
    code = (root.get("codeRepository") or "").rstrip("/")
    if code.endswith(".git"):
        code = code[:-4]
    return code if code.startswith("http") else None


def _gap_url(item, repo_url, role_label):
    """A fix-it link for an unmet item. Asset gaps (action_role) → the content form with the role
    PRE-SELECTED (the user only picks the file; role/cardinality stay hidden). Property gaps → the
    configure form. None if we can't build a URL (no repo URL known)."""
    if not repo_url:
        return None
    base = f"{repo_url}/issues/new"
    role = item.get("action_role")
    if role:
        return f"{base}?template={_CONTENT_TEMPLATE}&role_term={quote(role_label(role))}"
    pred = item.get("predicate")
    if isinstance(pred, dict) and ("property" in pred or "open_license" in pred):
        return f"{base}?template={_CONFIGURE_TEMPLATE}"
    return None


def report(repo_dir, repo_url=None, build=True):
    """Build the traffic-light report: tiered items with met-flags and per-gap fix-it URLs, plus an
    `eligible` flag (all required met)."""
    repo_dir = Path(repo_dir).resolve()
    if build:
        build_crate(repo_dir, out_path=str(repo_dir / "ro-crate-metadata.json"), merge=True)
    doc, _ = build_crate(repo_dir, out_path=None)
    graph = doc["@graph"]
    by_id = {e.get("@id"): e for e in graph}
    root = by_id.get("./", {})
    profile = load_profile(repo_dir)

    vocab = load_vocab(profile)
    def role_label(name):                                    # term name → its human dropdown label
        t = vocab.get(name)
        return t.label if t else name

    repo_url = repo_url or _repo_url(root)
    ev = evaluate(profile, graph, by_id, root)
    for tier in ("required", "encouraged"):
        for item in ev[tier]:
            item["tier"] = tier
            item["gap_url"] = None if item["met"] else _gap_url(item, repo_url, role_label)

    items = ev["required"] + ev["encouraged"]
    eligible = all(i["met"] for i in ev["required"])
    # report heading is a profile knob (e.g. geoscience says "Model readiness"); generic default
    title = (profile.get("readiness") or {}).get("title") or "Readiness"
    return {"eligible": eligible, "items": items, "title": title,
            "required_met": sum(i["met"] for i in ev["required"]),
            "required_total": len(ev["required"])}


def report_markdown(rep):
    """Render a report (from report()) as a GitHub-flavoured checklist — used for the Actions job
    summary and a model-status issue. Required gaps are 🔴, encouraged 🟡, met ✅; each gap shows its
    pre-filled fix-it link."""
    lines = [f"## 📋 {rep.get('title') or 'Readiness'}", ""]
    if rep["eligible"]:
        lines += ["**✅ Catalogue-eligible** — the required floor is met.", ""]
    else:
        lines += [f"**🔴 Not yet eligible** — {rep['required_met']}/{rep['required_total']} required items met.", ""]
    for tier, heading in (("required", "### Required (the floor)"), ("encouraged", "### Encouraged (a richer page)")):
        rows = [i for i in rep["items"] if i["tier"] == tier]
        if not rows:
            continue
        lines.append(heading)
        for i in rows:
            mark = "✅" if i["met"] else ("🔴" if tier == "required" else "🟡")
            row = f"- {mark} {i['label']}"
            if not i["met"] and i.get("gap_url"):
                row += f" — [add it]({i['gap_url']})"
            lines.append(row)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
