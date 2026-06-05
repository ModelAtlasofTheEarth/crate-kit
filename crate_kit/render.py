"""render: project a crate into a Quarto `model.qmd`, then to README.md + index.html.

Tier-1 (zero-config) view: an Overview from the root entity, plus one panel-tab per
top-level directory, listing the files that directory actually contains. No view-spec
needed; the page adapts to whatever directories exist. Tier-2 (a curated view-spec from
a pack) will later replace the auto tabs with named/ordered/selected ones — same engine.

In gfm output, Quarto degrades `.panel-tabset` to sequential sections (great for a README);
in html it renders real tabs (great for the website). One source, two outputs.
"""
import shutil
import subprocess
from pathlib import Path

import yaml

from .build_crate import build_crate

# top-level dirs hidden from the *view* (still described in the crate). A pack/profile
# could change this; Tier-1 default keeps infrastructure dirs off the page.
VIEW_HIDE_PREFIX = "."

IMG_EXT = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _is_image(file_id):
    return Path(file_id).suffix.lower() in IMG_EXT


def _collect_images(doc, repo_dir, out_dir):
    """Copy image File entities into out_dir/assets/ and return [(label, relative-src)]."""
    assets = Path(out_dir) / "assets"
    images = []
    for e in doc["@graph"]:
        if e.get("@type") != "File":
            continue
        i = e.get("@id", "")
        if not i or i.startswith(("http", "#", "./")) or not _is_image(i):
            continue
        src = Path(repo_dir) / i
        if not src.exists():
            continue
        assets.mkdir(parents=True, exist_ok=True)
        flat = i.replace("/", "__")
        shutil.copy(src, assets / flat)
        images.append((i, f"assets/{flat}"))
    return images


def _human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _authors(root, by_id):
    out = []
    for c in root.get("creator", []) or []:
        if not isinstance(c, dict):
            continue
        if c.get("name"):
            out.append(scalar(c["name"]))
        elif c.get("@id"):
            ent = by_id.get(c["@id"]) or {}
            out.append(scalar(ent.get("name")) or c["@id"].rstrip("/").rsplit("/", 1)[-1])
    return out


def scalar(v):
    """Crate-O wraps single values in lists; unwrap a 1-element list where we expect a scalar."""
    if isinstance(v, list):
        return v[0] if len(v) == 1 else v
    return v


def _sections(graph):
    """Group non-image File entities by top-level path segment (the directory tab).
    Images are shown embedded in the Figures tab instead of listed here."""
    sections = {}
    for e in graph:
        if e.get("@type") != "File":
            continue
        i = e.get("@id", "")
        if not i or i.startswith(("http", "#", "./")) or _is_image(i):
            continue
        parts = i.strip("/").split("/")
        key = "(repository root)" if len(parts) == 1 else parts[0]
        if key.startswith(VIEW_HIDE_PREFIX):
            continue
        sections.setdefault(key, []).append(e)
    return sections


def _order(name):
    # model_* content first, repository-root files last, others in between
    if name.startswith("model_"):
        return (0, name)
    if name.startswith("("):
        return (2, name)
    return (1, name)


def build_qmd(doc, repo_name, images=None):
    images = images or []
    graph = doc["@graph"]
    by_id = {e.get("@id"): e for e in graph}
    root = by_id.get("./", {})

    fm = {
        "title": scalar(root.get("name")) or repo_name,
        "format": {
            "gfm": {"output-file": "README.md"},
            "html": {"output-file": "index.html", "toc": True, "page-layout": "full",
                     "embed-resources": True},
        },
    }
    authors = _authors(root, by_id)
    if authors:
        fm["author"] = authors
    kw = root.get("keywords")
    if kw:
        fm["keywords"] = kw if isinstance(kw, list) else [kw]

    lines = ["---", yaml.safe_dump(fm, sort_keys=False).strip(), "---", ""]

    # Overview from the root entity
    lines.append("## Overview\n")
    desc = scalar(root.get("description"))
    if desc:
        lines.append(str(desc) + "\n")

    meta = []
    lic = root.get("license")
    if isinstance(lic, dict) and lic.get("@id"):
        meta.append(f"- **License:** {scalar(lic['@id'])}")
    if root.get("version"):
        meta.append(f"- **Version (git):** `{scalar(root['version'])}`")
    if root.get("codeRepository"):
        meta.append(f"- **Repository:** {scalar(root['codeRepository'])}")
    if root.get("dateModified"):
        meta.append(f"- **Last modified:** {scalar(root['dateModified'])}")
    if meta:
        lines.append("\n".join(meta) + "\n")

    ab = scalar(root.get("abstract"))
    if ab and ab != desc:
        lines.append("### Abstract\n")
        lines.append(str(ab) + "\n")

    # Tier-1 dynamic tabs: Figures, one per top-level directory, plus external payloads
    sections = _sections(graph)
    external = [e for e in graph if e.get("additionalType") == "ExternalPayload"]
    if sections or external or images:
        lines.append("## Contents\n")
        lines.append("::: {.panel-tabset}\n")
        if images:
            lines.append("### Figures\n")
            for label, src in images:
                lines.append(f"![{label}]({src})\n")
            lines.append("")
        for name in sorted(sections, key=_order):
            files = sorted(sections[name], key=lambda e: e["@id"])
            lines.append(f"### {name}\n")
            lines.append(f"_{len(files)} file(s)_\n")
            for e in files:
                size = _human_size(scalar(e.get("contentSize")))
                lines.append(f"- `{e['@id']}` — {size}")
            lines.append("")
        if external:
            lines.append("### Model output data (external)\n")
            for e in external:
                lines.append(f"- **{scalar(e.get('name')) or 'external dataset'}** — hosted externally, not in this repo")
                if e.get("@id"):
                    lines.append(f"  - <{e['@id']}>")
                if e.get("contentSize"):
                    lines.append(f"  - size: {scalar(e['contentSize'])}")
            lines.append("")
        lines.append(":::\n")

    lines.append("---\n")
    lines.append("_Page generated from `ro-crate-metadata.json` by `mate render` "
                 "(Tier-1 view). Do not edit by hand._")
    return "\n".join(lines)


def render(repo, out_dir, reverse_engineer=False, run_quarto=True):
    doc, _ = build_crate(repo, out_path=None, reverse_engineer=reverse_engineer)
    repo_name = Path(repo).resolve().name

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    images = _collect_images(doc, Path(repo).resolve(), out)
    qmd_path = out / "model.qmd"
    qmd_path.write_text(build_qmd(doc, repo_name, images))

    result = {"qmd": str(qmd_path), "quarto": None, "outputs": []}

    if run_quarto:
        quarto = shutil.which("quarto")
        if not quarto:
            result["quarto"] = "not found on PATH — wrote model.qmd only"
            return result
        proc = subprocess.run(
            [quarto, "render", "model.qmd"], cwd=str(out),
            capture_output=True, text=True,
        )
        result["quarto"] = f"exit {proc.returncode}"
        if proc.returncode != 0:
            result["quarto_stderr"] = proc.stderr[-1500:]
        result["outputs"] = sorted(
            p.name for p in out.iterdir() if p.name != "model.qmd"
        )
    return result
