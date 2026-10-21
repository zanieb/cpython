# Managed native dependencies

`dependencies.json` records exact source inputs used by managed build recipes.
It does not change the libraries accepted by `configure` or require contributors
to replace system packages. Entries have stable identifiers, so platforms can
select different versions of a library. Platform configuration selects an entry;
the common manifest owns its URL, SHA-256 digest, source root, and license data.

The first consumers are the Emscripten `libffi` and `mpdecimal` recipes. Their
versions are selected in `Platforms/emscripten/config.toml`. Source metadata is
included in their build receipts and CI cache identity alongside the SDK version
and recipe contents.

Use Python 3.12 or newer to inspect or fetch inputs:

```sh
python3 Tools/build/native_deps.py list
python3 Tools/build/native_deps.py fetch libffi-3.4.6 --cache-dir .native-sources
python3 Tools/build/native_deps.py fetch libffi-3.4.6 --cache-dir .native-sources --offline
```

Sources are cached by SHA-256. Every reuse verifies the cached bytes; a corrupt
entry is replaced only after a successful verified download. In offline mode,
missing or corrupt sources are errors. An offline cache can be populated by
copying archives to filenames equal to their SHA-256 digests.

To update a dependency, add a new source entry with an independently verified
upstream URL and digest, then update the selecting platform configuration.
Retain entries still used by other recipes. The license filename is relative to
the archive's declared top-level directory. Record changes to patches and build
options in the consuming recipe so they invalidate its installation receipt.

Build tools that execute on the build machine, libraries linked into the target
interpreter, and test dependencies have different roles. Adding them to the same
catalog does not make their artifacts interchangeable. A source digest establishes
an input identity; it does not establish a reproducible compiler or runtime
configuration.
