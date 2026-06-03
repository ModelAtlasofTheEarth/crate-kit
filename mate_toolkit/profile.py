"""Load the M@TE profile — the single metadata contract (see profiles/README.md).

Resolution: a repo's own `.mate/profile.yml` overrides the builtin packaged profile, so a
model (or a different discipline) can swap the contract without changing the engine.
"""
import importlib.resources as resources
from pathlib import Path

import yaml

DEFAULT_PROFILE = "mate-geoscience"


def load_profile(repo_dir=None, name=DEFAULT_PROFILE):
    if repo_dir:
        override = Path(repo_dir) / ".mate" / "profile.yml"
        if override.exists():
            return yaml.safe_load(override.read_text()) or {}
    text = resources.files("mate_toolkit").joinpath("profiles", f"{name}.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}
