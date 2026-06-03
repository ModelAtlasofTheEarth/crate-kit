"""What the crate describes — a layered ignore policy.

`.gitignore` and "what the crate should describe" overlap but are NOT the same set, and
conflating them fails in the case we most care about: large payloads (`model_output_data/`)
are often gitignored *because they are too big for git* — yet they are the most important
thing to describe (as external entities). So gitignore is a default *hint*, never law.

Layers, all applied together (union of matches):
  1. HARD_SKIP        always (`.git/`, the crate file, OS/editor noise)
  2. .gitignore       transient/large files git already excludes (also a payload signal)
  3. .rocrateignore   crate-specific extra exclusions (e.g. a bundled toolkit)
Inclusion of `payload:` external entities is handled separately (link-payload) and is
independent of these rules.

Promotion signal: a path gitignored under a *data*-named directory is a candidate external
entity ("this likely lives on NCI/Zenodo"). We surface such candidates, not silently drop them.
"""
from pathlib import Path

import pathspec

HARD_SKIP_DIRS = {".git"}
HARD_SKIP_FILES = {
    ".DS_Store", "Thumbs.db", "ro-crate-metadata.json",
    "ro-crate-preview.html",                                       # written by Crate-O on save
    ".gitkeep", ".gitignore", ".rocrateignore", ".gitattributes",  # placeholders / VCS infra
}

# heuristics for "looks like a data payload that was gitignored because it's big"
_DATA_HINTS = ("output", "data", "results")


def _load_specs(repo_dir):
    repo_dir = Path(repo_dir)
    specs = {}
    for name in (".gitignore", ".rocrateignore"):
        p = repo_dir / name
        if p.exists():
            specs[name] = pathspec.PathSpec.from_lines(
                "gitwildmatch", p.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    return specs


class IgnorePolicy:
    def __init__(self, repo_dir):
        self.specs = _load_specs(repo_dir)
        self.source = "+".join(self.specs) or None
        self.ignored = []
        self._payload = set()

    @property
    def payload_candidates(self):
        return sorted(self._payload)

    def _matched_by(self, path):
        """Return the name of the first ignore file that matches `path`, or None."""
        for name, spec in self.specs.items():
            if spec.match_file(path):
                return name
        return None

    def _note_payload(self, relpath, matched_by):
        """A gitignored path under a data-named directory -> external-entity candidate."""
        if matched_by != ".gitignore":
            return
        parts = relpath.strip("/").split("/")
        for i, seg in enumerate(parts):
            if any(h in seg.lower() for h in _DATA_HINTS):
                self._payload.add("/".join(parts[: i + 1]) + "/")
                return

    def skip_dir(self, name, relpath):
        if name in HARD_SKIP_DIRS:
            return True
        m = self._matched_by(relpath.rstrip("/") + "/")
        if m:
            self.ignored.append(relpath)
            self._note_payload(relpath, m)
            return True
        return False

    def skip_file(self, name, relpath):
        if name in HARD_SKIP_FILES:
            return True
        m = self._matched_by(relpath)
        if m:
            self.ignored.append(relpath)
            self._note_payload(relpath, m)
            return True
        return False
