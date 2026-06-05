"""Optional git-provenance enrichment.

Principle: **git is the provenance store; the crate references and summarises it, it
does not duplicate it.** We never embed the commit graph or treat every commit author
as a creator. We pin the exact commit (so the crate is reproducible — "this describes
the repo at SHA x") and derive a handful of high-value fields cheaply by shelling out
to `git` (no parsing of `.git` internals). Degrades to {} if there is no git repo, so
build-crate stays runnable on a plain directory.

What/how much to include is a profile setting (see TARGET_ARCHITECTURE.md): e.g.
  git: { pin_commit: true, derive_dates: true, remote: true, contributors: false }
"""
import subprocess


def _git(repo_dir, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except Exception:
        return None


def git_provenance(repo_dir, opts=None):
    """Return a dict of crate properties derived from git, or {} if not a repo.

    Maps:
      HEAD commit SHA        -> version  (pins the description to an exact state)
      first commit date      -> dateCreated
      last commit date       -> dateModified
      latest tag             -> (release) version / hint
      remote origin url       -> codeRepository
    """
    opts = opts or {}
    if _git(repo_dir, "rev-parse", "--is-inside-work-tree") != "true":
        return {}

    props = {}

    if opts.get("pin_commit", True):
        sha = _git(repo_dir, "rev-parse", "HEAD")
        described = _git(repo_dir, "describe", "--tags", "--always", "--dirty")
        if sha:
            # version pins the crate to an exact repo state; described is human-friendly
            props["version"] = described or sha[:12]
            props["_git_commit"] = sha  # underscore = prototype-only provenance note

    if opts.get("derive_dates", True):
        first = _git(repo_dir, "log", "--reverse", "--format=%cI")
        if first:
            props["dateCreated"] = first.splitlines()[0]
        last = _git(repo_dir, "log", "-1", "--format=%cI")
        if last:
            props["dateModified"] = last

    if opts.get("remote", True):
        remote = _git(repo_dir, "config", "--get", "remote.origin.url")
        if remote:
            props["codeRepository"] = remote

    return props
