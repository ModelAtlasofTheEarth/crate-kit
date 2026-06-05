"""Load the M@TE profile — the single metadata contract (see profiles/README.md).

Resolution: a repo's own `.mate/profile.yml` overrides the builtin packaged profile, so a
model (or a different discipline) can swap the contract without changing the engine.
"""
import importlib.resources as resources
from pathlib import Path

import yaml

# The engine is domain-FREE by default: a bare schema.org dataset crate. A discipline
# (e.g. MATE geoscience) is opted INTO by the repo carrying `.mate/profile.yml`, not by the
# engine. So copying the toolkit + a template into a new field "just works" generically.
DEFAULT_PROFILE = "base"


def load_profile(repo_dir=None, name=DEFAULT_PROFILE):
    if repo_dir:
        override = Path(repo_dir) / ".mate" / "profile.yml"
        if override.exists():
            return yaml.safe_load(override.read_text()) or {}
    text = resources.files("crate_kit").joinpath("profiles", f"{name}.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}
