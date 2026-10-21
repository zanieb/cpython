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

## Local native builds

On Linux or macOS, the native recipes can build `libffi` and `mpdecimal` into a
local directory with no administrator privileges. Install a C compiler, `make`,
and `pkg-config` through your usual system package manager first.

```sh
python3 Tools/build/native_deps.py build libffi mpdecimal \
    --prefix "$PWD/.native-deps" --cache-dir "$PWD/.native-sources"
PKG_CONFIG_PATH="$PWD/.native-deps/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
    ./configure --with-pydebug
make -j4
```

The prefix contains the selected set of libraries, built as position-independent
static archives. Changing the selection, source metadata, recipe, compiler,
build flags, or builder code rebuilds that prefix. Completed matching prefixes
are reused. Existing directories without a readable JSON receipt are left
unchanged; choose a different `--prefix` to create a managed installation.
Downloads can be prefetched with `fetch`; adding `--offline` to `build` prohibits
source downloads.

`CC`, `AR`, `RANLIB`, `MAKE`, `CFLAGS`, `CPPFLAGS`, and `LDFLAGS` can customize the
build. The receipt records the selected recipes, sources, tool identities, and
relevant environment settings. Compiler defaults, system headers, and operating
system libraries still come from the build machine. These recipes provide local
dependencies; they do not establish a portable release ABI.

The source-tree build continues to discover other dependencies through its
normal system-package configuration. `configure` remains authoritative about
which extension modules can be built. To require all available optional modules,
set `PYTHONSTRICTEXTENSIONBUILD=1` when running `make`. Explicitly disabled and
platform-inapplicable modules are excluded by the existing extension checker.
