"""mate CLI — the single engine behind both the GitHub Action and local conda runs."""
import argparse
import json
import sys

from .build_crate import build_crate
from .render import render as render_repo
from .validate import validate as validate_repo


def main(argv=None):
    p = argparse.ArgumentParser(prog="mate", description="FAIR research-object toolkit")
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

    v = sub.add_parser("validate", help="check a repo's crate meets the minimum M@TE model requirements")
    v.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    v.add_argument("--reverse-engineer", action="store_true",
                   help="seed root metadata from an old-engine .metadata_trail/issue_dict.json")

    g = sub.add_parser("issue-form", help="generate a GitHub issue form (.yml) from the profile")
    g.add_argument("-o", "--out", required=True, help="output path for the issue form yaml")
    g.add_argument("--repo", default=None, help="use this repo's .mate/profile.yml (else the builtin profile)")
    g.add_argument("--type", dest="type_", help="generate a describe-<type> form for a component type (else the root form)")

    di = sub.add_parser("describe-from-issue", help="apply a submitted describe-<type> issue to the crate")
    di.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    di.add_argument("--body", required=True, help="path to the issue body (or '-' for stdin)")
    di.add_argument("--type", dest="type_", help="component type (else parsed from --title)")
    di.add_argument("--title", help="issue title (used to parse '[describe:TYPE]')")

    fi = sub.add_parser("from-issue", help="write a submitted issue-form's answers into the repo's crate")
    fi.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    fi.add_argument("--body", required=True, help="path to the issue body (or '-' for stdin)")

    e = sub.add_parser("enrich", help="resolve PIDs in the crate (ORCID, publication DOI); best-effort")
    e.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")

    d = sub.add_parser("describe", help="attach typed, schema-aware metadata to a directory's crate entity")
    d.add_argument("target", help="directory to describe, e.g. model_results/")
    d.add_argument("--repo", default=".", help="repository directory (default: .)")
    d.add_argument("--type", dest="type_", help="component type (curated: Dataset, SoftwareSourceCode, … or any schema.org type)")
    d.add_argument("--name", help="human name")
    d.add_argument("--description", help="what it is / how it was made")
    d.add_argument("--set", action="append", dest="sets", metavar="property=value",
                   help="set any schema.org/CodeMeta property (repeatable; the escape hatch)")
    d.add_argument("--list-fields", action="store_true", help="list the curated fields for --type (or all types) and exit")

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

    if args.cmd == "issue-form":
        from .profile import load_profile
        from .issue_form import write_issue_form, write_component_form
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        if args.type_:
            out = write_component_form(load_profile(args.repo), args.type_, args.out)
        else:
            out = write_issue_form(load_profile(args.repo), args.out)
        print(f"wrote {out}", file=sys.stderr)
        return 0

    if args.cmd == "describe-from-issue":
        from .from_issue import apply_describe_issue
        body = sys.stdin.read() if args.body == "-" else open(args.body, encoding="utf-8").read()
        result = apply_describe_issue(args.repo, body, type_=args.type_, title=args.title)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "from-issue":
        from .from_issue import apply_issue
        body = sys.stdin.read() if args.body == "-" else open(args.body, encoding="utf-8").read()
        result = apply_issue(args.repo, body)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 0

    if args.cmd == "enrich":
        from .enrich import enrich as enrich_repo
        print(json.dumps(enrich_repo(args.repo), indent=2), file=sys.stderr)
        return 0

    if args.cmd == "describe":
        from .describe import describe as describe_target
        result = describe_target(args.repo, args.target, type_=args.type_, name=args.name,
                                 description=args.description, sets=args.sets,
                                 list_fields=args.list_fields)
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
        errors, warnings = validate_repo(args.repo, reverse_engineer=args.reverse_engineer)
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
