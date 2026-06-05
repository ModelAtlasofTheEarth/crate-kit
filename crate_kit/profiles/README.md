# Profiles (the M@TE pack)

A **profile** is the single contract for a model's metadata. One file drives four consumers:

| Consumer | Reads |
|---|---|
| issue form (authoring) | each field's `label`, `input`, `options`, `many` |
| issue→crate mapping (`build`) | each field's schema.org `property`; `datasets` typings; `payload` |
| `mate validate` (the gate) | `required` per field + `requires_for_website` |
| Crate-O mode file (rich editor, later) | `root.type` + properties + `datasets` types |

`mate-geoscience.yml` is the geoscience pack — a folder here for now, its own repo later. A
different discipline swaps this file, not the engine. Principle: **automate structure
(types, where things go), ask the human for meaning (descriptions).** The crate
(`ro-crate-metadata.json`) remains the single source of truth; this profile just says what a
valid one looks like and how to author it.
