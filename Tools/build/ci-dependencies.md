# Maintaining CI dependencies

The root `pyproject.toml` declares CI and development dependencies in
`[dependency-groups]`. Groups contain direct dependencies and compatibility
limits; they do not define CPython as an installable Python package. CI
installs their committed locks, which record exact versions and artifact
hashes. Keep exact pins in an input only when a compatibility restriction
requires that version. The build environment uses a hashed
`requirements-build.txt` lock; the bootstrap `pip` pin must match the wheel
bundled with `ensurepip`.

Install `uv` locally, then regenerate selected environments from the repository
root:

```sh
python3 Tools/build/update_ci_dependencies.py docs typing hypothesis
python3 Tools/build/update_ci_dependencies.py typing --upgrade-package mypy
python3 Tools/build/update_ci_dependencies.py epubcheck --upgrade
```

Each group produces a separate lock, so an update can be scoped to one
environment:

| Group | Lock |
| --- | --- |
| `docs` | `Doc/pylock.toml` |
| `epubcheck` | `Doc/pylock.epubcheck.toml` |
| `typing` | `Tools/pylock.dev.toml` |
| `hypothesis` | `Tools/pylock.hypothesis.toml` |
| `build-tools` | `Tools/requirements-build.txt` |

The command prefers existing versions. Resolution can change when those
versions no longer satisfy the inputs or release cooldown, or are unavailable
from the index. Use `--upgrade` or `--upgrade-package` to request newer
versions. The 14-day cooldown also applies to upgrades, except for the
core-team-maintained `blurb`, `python-docs-theme`, and `sphinx-linklint`
packages. The command records the inputs after successful generation.
`make -C Doc lock` upgrades the docs environment. Review the complete version and
artifact-hash diff and run the consuming jobs.

The update command passes `Doc/constraints.txt` as a constraint when resolving
the `docs` group. Constraints can limit direct and transitive dependencies
without adding requirements to the group. Keep documented compatibility limits,
including the Sphinx minimum in `Doc/conf.py`, distinct from the versions
selected by the lock. The regular documentation workflow uses a Python 3.12
host; doctests and property-based tests use the CPython interpreter under test.
Lock generation targets are recorded in the update script.

The offline checker fingerprints the selected group after expanding
`include-group` entries, together with its constraint files. Changes to groups
that are not included or unrelated `pyproject.toml` metadata do not invalidate
that record. CI installs the committed locks; installing a group directly would
resolve its requirements again.

The input record cannot prove that a lock was regenerated or that its
transitive dependencies are complete. The checker also checks bare direct
requirements and plain `name==version` pins, SHA-256 artifact metadata, and the
bundled `pip` wheel. For a deliberate metadata-only lock edit, refresh the
record with:

```sh
python3 Tools/build/check_ci_dependencies.py --record PATH_TO_LOCK
```
