"""describe: attach human meaning to a sub-component of the crate (the value beyond a manifest).

Sets name / description / extra @type on a directory's Dataset entity. The crate is the single
source of truth, so this writes straight into ro-crate-metadata.json; build-as-merge preserves
the description on every rebuild (directory Datasets keep their authored fields).

  mate describe model_results/ --name "Postprocessed results" \\
      --description "Derived stress/strain fields from the raw VTK output." --type SoftwareSourceCode
"""
import json
from pathlib import Path

from .build_crate import build_crate


def describe(repo_dir, target, name=None, description=None, types=None):
    repo_dir = Path(repo_dir).resolve()
    crate_path = repo_dir / "ro-crate-metadata.json"

    # ensure the manifest is current so a freshly-added directory has an entity to describe
    build_crate(repo_dir, out_path=str(crate_path), merge=True)
    doc = json.loads(crate_path.read_text())

    tid = target if target.endswith("/") else target + "/"
    entity = next((e for e in doc["@graph"] if e.get("@id") == tid), None)
    if entity is None:
        return {"error": f"no dataset '{tid}' in the crate — is it a directory in the repo? "
                         f"(add files to it and it will appear)"}

    applied = []
    if name:
        entity["name"] = name; applied.append("name")
    if description:
        entity["description"] = description; applied.append("description")
    if types:
        current = entity.get("@type", "Dataset")
        current = [current] if isinstance(current, str) else list(current)
        for t in types:
            if t not in current:
                current.append(t)
        entity["@type"] = current; applied.append("@type")

    crate_path.write_text(json.dumps(doc, indent=2))
    return {"described": tid, "set": applied}
