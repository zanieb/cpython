# Maintaining CI dependencies

Editable `requirements.in` files declare direct dependencies and compatibility
limits. CI installs their committed locks, which record exact versions and
artifact hashes. Keep exact pins in an input only when a compatibility restriction
requires that version. The build environment uses a hashed `requirements-build.txt`
lock; the bootstrap `pip` pin must match the wheel bundled with `ensurepip`.

Install `uv` locally, then regenerate selected environments from the repository
root:

```sh
python3 Tools/build/update_ci_dependencies.py docs typing hypothesis
python3 Tools/build/update_ci_dependencies.py typing --upgrade-package mypy
python3 Tools/build/update_ci_dependencies.py epubcheck --upgrade
```

The available environments are `docs`, `epubcheck`, `typing`, `hypothesis`, and
`build-tools`. The command prefers existing versions. Resolution can change when those versions
no longer satisfy the inputs or release cooldown, or are unavailable from the
index. Use `--upgrade` or `--upgrade-package` to request newer versions. The
14-day cooldown also applies to upgrades, except for the core-team-maintained
`blurb`, `python-docs-theme`, and `sphinx-linklint` packages. The command records
the inputs after successful generation. `make -C Doc lock` upgrades the docs environment.
Review the complete version and artifact-hash diff and run the consuming jobs.

The docs constraints remain resolution inputs. Keep documented compatibility
limits, including the Sphinx minimum in `Doc/conf.py`, distinct from the versions
selected by the lock. The regular documentation workflow uses a Python 3.12 host;
doctests and property-based tests use the CPython interpreter under test. Lock
generation targets are recorded in the update script.

The offline checker associates each lock with its requirements and constraints.
Its input record cannot prove that a lock was regenerated or that its transitive
dependencies are complete. It also checks bare direct requirements and plain
`name==version` pins, SHA-256 artifact metadata, and the bundled `pip` wheel.
For a deliberate metadata-only lock edit, refresh the record with
`python3 Tools/build/check_ci_dependencies.py --record PATH_TO_LOCK`.
