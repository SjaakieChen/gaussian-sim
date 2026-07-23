# Migrations

Machine-facing migration bundles live here by version:

```text
migration_v1\
migration_v2\
migration_v3\
migration_v4\
migration_v5\
migration_v6\
```

Each version keeps its YASE files, Python helpers, dev-side files, and
`Migration back` machine snapshots inside that version folder.

`migration_v6` is the current reviewed coarse-to-fine workflow. Its operator
entry point, independent subsequences, memory behavior, canonical axes, mirror
calibration, simulator, and commissioning boundary are documented in
`migration_v6\README.md`.

For current machine configuration and recurring YASE/TMPython mistakes, use
the repository-root `MACHINE_CONFIGURATION.md` and `COMMON_MISTAKES.md` rather
than migration-local copies.
