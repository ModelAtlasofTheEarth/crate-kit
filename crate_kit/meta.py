"""Shared reader for the authored `mate:` metadata block (front-matter or .mate/metadata.yml).

Kept in its own module so both build_crate and payload can use it without a circular import.
"""
from pathlib import Path

import yaml


def read_mate_block(repo_dir):
    """Return the authored `mate:` mapping, or {} if none found.

    Priority: `.mate/metadata.yml` then README.md YAML front-matter. In both cases a top-level
    `mate:` key is unwrapped if present, otherwise the whole mapping is returned.
    """
    repo_dir = Path(repo_dir)

    mate_yml = repo_dir / ".mate" / "metadata.yml"
    if mate_yml.exists():
        data = yaml.safe_load(mate_yml.read_text()) or {}
        return data.get("mate", data) if isinstance(data, dict) else {}

    readme = repo_dir / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8", errors="replace")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    data = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    return {}
                if isinstance(data, dict):
                    return data.get("mate", data)

    return {}
