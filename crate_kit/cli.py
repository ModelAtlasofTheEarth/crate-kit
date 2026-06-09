"""mate CLI — the single engine behind both the GitHub Action and local conda runs."""
import argparse
import json
import sys

from .build_crate import build_crate
from .render import render as render_repo
from .validate import validate as validate_repo


def main(argv=None):
    p = argparse.ArgumentParser(prog="crate", description="FAIR research-object toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build an RO-Crate from a repository's contents")
    b.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    b.add_argument("-o", "--out", help="write ro-crate-metadata.json here (default: <repo>/ro-crate-metadata.json)")
    b.add_argument("--stdout", action="store_true", help="print the crate to stdout instead of writing")
    b.add_argument("--reverse-engineer", action="store_true",
                   help="seed root metadata from an old-engine .metadata_trail/issue_dict.json")

    r = sub.add_parser("render", help="render a repo's crate to model.qmd -> README.md + index.html (Tier-1 view)")
    r.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    r.add_argument("-o", "--out", required=True, help="output directory for model.qmd + rendered files")
    r.add_argument("--reverse-engineer", action="store_true",
                   help="seed root metadata from an old-engine .metadata_trail/issue_dict.json")
    r.add_argument("--no-quarto", action="store_true", help="write model.qmd but do not run quarto")

    w = sub.add_parser("website", help="resolve the crate against the profile's website: schema -> a flat, portable website.json")
    w.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    w.add_argument("-o", "--out", help="write website.json here (default: stdout)")
    w.add_argument("--no-build", action="store_true", help="resolve the existing crate without rebuilding first")

    rl = sub.add_parser("role", help="tag an entity with a website role (additionalType), e.g. graphical-abstract")
    rl.add_argument("target", help="path to the file/folder to tag")
    rl.add_argument("--as", dest="role", required=True, metavar="ROLE", help="the role, e.g. graphical-abstract")
    rl.add_argument("--repo", default=".", help="repository directory (default: .)")
    rl.add_argument("--type", dest="type_", help="also set a more-specific @type, e.g. ImageObject")
    rl.add_argument("--caption", help="caption for the asset")

    v = sub.add_parser("validate", help="check a repo's crate meets the minimum M@TE model requirements")
    v.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    v.add_argument("--reverse-engineer", action="store_true",
                   help="seed root metadata from an old-engine .metadata_trail/issue_dict.json")
    v.add_argument("--strict", action="store_true",
                   help="escalate website-readiness problems (missing fields/eligibility) to "
                        "errors; without it they are informational warnings (build stays green)")

    g = sub.add_parser("issue-form", help="generate a GitHub issue form (.yml) from the profile")
    g.add_argument("-o", "--out", required=True, help="output path for the issue form yaml")
    g.add_argument("--repo", default=None, help="use this repo's .mate/profile.yml (else the builtin profile)")
    g.add_argument("--kind", choices=["configure", "data", "contextual"], default="data",
                   help="'configure' = edit root; 'data' = edit a non-root data entity (default); "
                        "'contextual' = add a remote reference")
    g.add_argument("--dir", action="append", dest="dirs", metavar="PATH",
                   help="a live folder to offer in the data form's path dropdown (repeatable; GitHub-surface knob)")
    g.add_argument("--title", help="issue title prefix the build workflow gates on")

    fi = sub.add_parser("from-issue", help="write a submitted issue-form's answers into the repo's crate")
    fi.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    fi.add_argument("--body", required=True, help="path to the issue body (or '-' for stdin)")
    fi.add_argument("--print-command", action="store_true",
                    help="also print the equivalent `mate` command for the applied edit")

    e = sub.add_parser("enrich", help="resolve PIDs in the crate (ORCID, publication DOI); best-effort")
    e.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")

    d = sub.add_parser("describe", help="attach typed, schema-aware metadata to any entity (dir/file) in the crate")
    d.add_argument("target", help="path to describe, e.g. model_results/ or recordings/x.csv")
    d.add_argument("--repo", default=".", help="repository directory (default: .)")
    d.add_argument("--type", dest="type_", help="component type (curated: Dataset, SoftwareSourceCode, … or any schema.org type)")
    d.add_argument("--name", help="human name")
    d.add_argument("--description", help="what it is / how it was made")
    d.add_argument("--author", action="append", dest="authors", metavar="ORCID|\"Family, Given\"",
                   help="author of THIS entity (repeatable); distinct from the root's creators")
    d.add_argument("--set", action="append", dest="sets", metavar="property=value",
                   help="set any schema.org/CodeMeta property (repeatable; the escape hatch)")
    d.add_argument("--list-fields", action="store_true", help="list the curated fields for --type (or all types) and exit")

    a = sub.add_parser("add", help="add a contextual entity (remote reference) by PID — person, publication, software, funder, remote data")
    a.add_argument("kind", help="contextual kind from the profile (creator, publication, software, funder, remote_data, …)")
    a.add_argument("reference", help="the identifier: DOI / ORCID / ROR / URL")
    a.add_argument("--repo", default=".", help="repository directory (default: .)")
    a.add_argument("--name", help="optional name (otherwise filled by enrich)")

    s = sub.add_parser("seed", help="author the ROOT entity in the crate (== `describe .`) — the one-shot bootstrap")
    s.add_argument("--repo", default=".", help="repository directory (default: .)")
    s.add_argument("--name", help="title")
    s.add_argument("--description", help="short description")
    s.add_argument("--license", help="SPDX id or URL (shortcut for --set license=…)")
    s.add_argument("--author", action="append", dest="authors", metavar="ORCID|\"Family, Given\"",
                   help="creator(s) of the dataset (repeatable)")
    s.add_argument("--set", action="append", dest="sets", metavar="property=value",
                   help="set any root property, e.g. --set keywords=a,b")

    mf = sub.add_parser("mode-file", help="generate a Crate-O mode file (web editor config) from the profile")
    mf.add_argument("-o", "--out", required=True, help="output path for the mode file json")
    mf.add_argument("--repo", default=None, help="use this repo's .mate/profile.yml (else builtin profile)")

    args = p.parse_args(argv)

    if args.cmd == "build":
        out = None if args.stdout else (args.out or f"{args.repo.rstrip('/')}/ro-crate-metadata.json")
        doc, summary = build_crate(args.repo, out_path=out, reverse_engineer=args.reverse_engineer)
        if args.stdout:
            print(json.dumps(doc, indent=2))
        print(json.dumps(summary, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "render":
        result = render_repo(args.repo, args.out, reverse_engineer=args.reverse_engineer,
                             run_quarto=not args.no_quarto)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "website":
        from .website import resolve_website
        site = resolve_website(args.repo, out_path=args.out, build=not args.no_build)
        print(json.dumps(site, indent=2))
        return 0

    if args.cmd == "role":
        from .describe import set_role
        result = set_role(args.repo, args.target, args.role, type_=args.type_, caption=args.caption)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "issue-form":
        from .profile import load_profile
        from .issue_form import write_form
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        out = write_form(load_profile(args.repo), args.out, kind=args.kind,
                         dirs=args.dirs, title=args.title)
        print(f"wrote {out}", file=sys.stderr)
        return 0

    if args.cmd == "from-issue":
        from .from_issue import apply_issue
        body = sys.stdin.read() if args.body == "-" else open(args.body, encoding="utf-8").read()
        result = apply_issue(args.repo, body)
        if args.print_command and result.get("command"):
            print(result["command"])      # stdout: the equivalent `mate` command (for the comment)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "enrich":
        from .enrich import enrich as enrich_repo
        print(json.dumps(enrich_repo(args.repo), indent=2), file=sys.stderr)
        return 0

    if args.cmd == "describe":
        from .describe import edit_entity
        result = edit_entity(args.repo, args.target, type_=args.type_, name=args.name,
                             description=args.description, authors=args.authors, sets=args.sets,
                             list_fields=args.list_fields)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "seed":
        from .describe import edit_entity
        sets = list(args.sets or [])
        if args.license:
            sets.append(f"license={args.license}")
        result = edit_entity(args.repo, ".", name=args.name, description=args.description,
                             authors=args.authors, sets=sets or None)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "add":
        from .contextual import add_contextual
        result = add_contextual(args.repo, args.kind, args.reference, name=args.name)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "mode-file":
        from .profile import load_profile
        from .mode_file import write_mode
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        out = write_mode(load_profile(args.repo), args.out)
        print(f"wrote {out}", file=sys.stderr)
        return 0

    if args.cmd == "validate":
        errors, warnings = validate_repo(args.repo, reverse_engineer=args.reverse_engineer,
                                         strict=args.strict)
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        if errors:
            print(f"INVALID: {len(errors)} error(s)", file=sys.stderr)
            return 1
        print("VALID", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
