# Maintaining CI dependencies

Ordinary pull requests install the committed dependency locks. Dependency updates
should change the source requirements and their generated lock together, without
refreshing unrelated environments.

Dependabot proposes monthly Python and npm updates with a 14-day cooldown.
GitHub Actions retain their quarterly schedule. The default branch controls
Dependabot's release-branch entries; keep Python and npm entries for bugfix
branches and remove them when those branches become security-only. Add the
notification directory to a release branch only after that branch has its npm
manifest. The bundled `ensurepip` wheel owns the `pip` bootstrap version.

| Environment | Source requirements | Installed lock |
| --- | --- | --- |
| Documentation | `Doc/requirements.txt`, `Doc/constraints.txt` | `Doc/pylock.toml` |
| Type checking | `Tools/requirements-dev.txt` | `Tools/pylock.dev.toml` |
| Property tests | `Tools/requirements-hypothesis.txt` | `Tools/pylock.hypothesis.toml` |
| EPUB validation | `Doc/requirements-epubcheck.txt` | `Doc/pylock.epubcheck.toml` |
| Source-build backends | `Tools/requirements-build.in` | `Tools/requirements-build.txt` |
| Issue notifications | `.github/dependencies/notification/package.json` | `.github/dependencies/notification/package-lock.json` |

Regenerate Python locks locally using `make -C Doc lock` or the command recorded
in the generated file. Dependabot updates to source requirements still need this
lock refresh before merging. Review the complete version and artifact-hash diff.
For native archives, follow `Tools/build/dependency-hashes.md`. Regenerate each
release branch from its own requirements; do not copy a newer branch's lock.
An urgent security update can be prepared manually without waiting for the
routine cooldown, with the reason stated in its pull request.

The weekly `CI dependency compatibility` workflow resolves newer versions on
the default branch and the configured `3.15` and `3.14` branches when present.
Update that branch list as releases enter or leave bugfix maintenance. Manual
runs also cover the selected ref. It uses `pip`, removes exact direct pins while retaining version
bounds, and records the source commit and installed versions as artifacts.
Documentation additionally runs with its declared minimum Sphinx version on the
Python `3.12`. Hypothesis runs under a freshly built interpreter.
These scheduled failures are visible independently of pull-request checks.

When a compatibility run fails, reproduce with the recorded commit and available
installed versions; an installation failure may leave only a partial package list.
The owners of the consuming workflow can then update the dependency lock, adjust
a compatibility bound, or fix the consumer. A successful canary is evidence for
a proposed update, not permission to change the committed lock automatically.
