"""tags: the TAG binding of the one term-application mechanism (see terms.py / §23).

This module is a re-export shim for its existing import sites (CLI, from_issue, tests) —
the implementation lives in terms.py alongside the role binding, so the two read as two
bindings of one mechanism, not two codebases.
"""
from .terms import apply_tags as apply_tag, command_for_tag, set_eid, _term_eid  # noqa: F401
