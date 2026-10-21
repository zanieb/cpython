#!/usr/bin/env python3
"""Fetch and build the native dependencies selected by CPython recipes."""

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit
from urllib.request import urlopen

MANIFEST = Path(__file__).with_name("dependencies.json")


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(value):
    return (
        isinstance(value, str) and bool(value)
        and not PurePosixPath(value).is_absolute()
        and ".." not in PurePosixPath(value).parts
        and "\\" not in value
        and not PureWindowsPath(value).drive
    )


def load_manifest(path=MANIFEST):
    manifest = json.loads(Path(path).read_text())
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("Unsupported native dependency manifest schema")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("The dependency manifest must define sources")
    for source_id, source in sources.items():
        if not isinstance(source, dict):
            raise ValueError(f"Invalid source metadata for {source_id}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", source_id):
            raise ValueError(f"Invalid source identifier: {source_id!r}")
        for field in (
            "name", "version", "url", "sha256", "archive-root", "license",
            "license-file", "purl",
        ):
            if not isinstance(source.get(field), str) or not source[field]:
                raise ValueError(f"Missing {field!r} for {source_id}")
        if not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
            raise ValueError(f"Invalid SHA-256 for {source_id}")
        url = urlsplit(source["url"])
        if url.scheme != "https" or not url.netloc:
            raise ValueError(f"Expected an HTTPS source URL for {source_id}")
        root = source["archive-root"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", root):
            raise ValueError(f"Invalid archive root for {source_id}")
        if not relative_path(source["license-file"]):
            raise ValueError(f"Invalid license path for {source_id}")
    recipes = manifest.get("native-builds", {})
    if not isinstance(recipes, dict):
        raise ValueError("Native build recipes must be an object")
    for name, recipe in recipes.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(f"Invalid native recipe name: {name!r}")
        if not isinstance(recipe, dict):
            raise ValueError(f"Invalid native recipe: {name}")
        if recipe.get("source") not in sources:
            raise ValueError(f"Unknown source for native recipe {name}")
        if recipe.get("role") != "target-library":
            raise ValueError(f"Unsupported native recipe role for {name}")
        for field in ("configure", "expected-files"):
            values = recipe.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values
            ):
                raise ValueError(f"Invalid {field} for native recipe {name}")
        for filename in recipe["expected-files"]:
            if not relative_path(filename):
                raise ValueError(f"Invalid installed path for {name}")
    return manifest


def fetch_source(source, cache_dir, *, offline=False):
    """Return a verified archive, using its digest as the cache identity."""
    cache_dir = Path(cache_dir)
    archive = cache_dir / source["sha256"]
    if archive.is_file():
        if file_digest(archive) == source["sha256"]:
            return archive
        if offline:
            raise ValueError(f"Cached source has the wrong SHA-256: {archive}")
    if offline:
        raise FileNotFoundError(f"Source is not available offline: {archive}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache_dir, prefix=".download-") as tmp:
        downloaded = Path(tmp) / "archive"
        with urlopen(source["url"], timeout=30) as response:
            with downloaded.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        if file_digest(downloaded) != source["sha256"]:
            raise ValueError(f"Source has the wrong SHA-256: {source['url']}")
        downloaded.replace(archive)
    return archive


def extract_source(archive, destination, source):
    """Extract a source archive with one declared top-level directory."""
    destination = Path(destination)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != source["archive-root"]:
                raise ValueError(f"Unexpected source archive member: {member.name}")
        tar.extractall(destination, filter="data")
    root = destination / source["archive-root"]
    if not root.is_dir():
        raise ValueError("Source archive does not contain its declared root")
    return root


BUILD_ENVIRONMENT = (
    "CC", "CXX", "AR", "RANLIB", "CFLAGS", "CPPFLAGS", "LDFLAGS", "ARFLAGS",
    "CPATH", "C_INCLUDE_PATH", "LIBRARY_PATH", "MACOSX_DEPLOYMENT_TARGET",
    "SDKROOT", "SOURCE_DATE_EPOCH", "CONFIG_SITE", "MAKEFLAGS",
)


def tool_identity(command):
    args = shlex.split(command)
    executable = shutil.which(args[0]) if args else None
    if executable is None:
        raise ValueError(f"Build tool is not available: {command}")
    # BSD ar does not support --version. The executable digest still records
    # which tool was used when a version query is unavailable.
    result = subprocess.run(
        [*args, "--version"], capture_output=True, text=True, check=False
    )
    return {
        "command": args,
        "executable": str(Path(executable).resolve()),
        "sha256": file_digest(executable),
        "version": result.stdout.splitlines()[0] if result.stdout else None,
    }


def build_inputs(manifest, names, prefix):
    recipes = {name: manifest["native-builds"][name] for name in sorted(names)}
    sources = {
        recipe["source"]: manifest["sources"][recipe["source"]]
        for recipe in recipes.values()
    }
    environment = {
        key: os.environ[key] for key in BUILD_ENVIRONMENT if key in os.environ
    }
    environment["CFLAGS"] = os.environ.get("CFLAGS", "-O2") + " -fPIC"
    return {
        "platform": sys.platform,
        "machine": platform.machine(),
        "prefix": str(Path(prefix).absolute()),
        "sources": sources,
        "recipes": recipes,
        "environment": environment,
        "tools": {
            name: tool_identity(os.environ.get(variable, default))
            for name, variable, default in (
                ("cc", "CC", "cc"), ("ar", "AR", "ar"),
                ("ranlib", "RANLIB", "ranlib"), ("make", "MAKE", "make"),
            )
        },
        "builder-sha256": file_digest(__file__),
    }


def build_native(manifest, names, prefix, cache_dir, *, offline=False, jobs=1):
    """Build the selected libraries into one managed native prefix."""
    if sys.platform not in {"linux", "darwin"}:
        raise ValueError("Native dependency recipes support Linux and macOS")
    if jobs < 1:
        raise ValueError("The number of build jobs must be positive")
    prefix = Path(prefix).absolute()
    cache_dir = Path(cache_dir).absolute()
    inputs = build_inputs(manifest, names, prefix)
    environment = os.environ | inputs["environment"]
    make = inputs["tools"]["make"]["command"]

    def install(staging):
        with tempfile.TemporaryDirectory(
            dir=prefix.parent, prefix=".native-build-"
        ) as tmp:
            work = Path(tmp)
            for name, recipe in inputs["recipes"].items():
                source = inputs["sources"][recipe["source"]]
                archive = fetch_source(source, cache_dir, offline=offline)
                source_dir = extract_source(archive, work / name, source)
                build_dir = work / name / "build"
                build_dir.mkdir()
                subprocess.run(
                    [
                        str(source_dir / "configure"),
                        f"--prefix={prefix}", f"--libdir={prefix / 'lib'}",
                        *recipe["configure"],
                    ],
                    cwd=build_dir, env=environment, check=True,
                )
                subprocess.run(
                    [*make, f"-j{jobs}"], cwd=build_dir,
                    env=environment, check=True,
                )
                destdir = work / name / "install"
                subprocess.run(
                    [*make, "install", f"DESTDIR={destdir}"], cwd=build_dir,
                    env=environment, check=True,
                )
                installed = destdir / prefix.relative_to(prefix.anchor)
                shutil.copytree(installed, staging, dirs_exist_ok=True, symlinks=True)
                for filename in recipe["expected-files"]:
                    if not (staging / filename).is_file():
                        raise ValueError(f"{name} did not install {filename}")
                license_dir = staging / "share" / "licenses" / name
                license_dir.mkdir(parents=True)
                shutil.copy2(source_dir / source["license-file"], license_dir)

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from dependency_cache import install_prefix
    finally:
        sys.path.pop(0)
    return install_prefix(prefix, inputs, install)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Print the selected source metadata")
    fetch = commands.add_parser("fetch", help="Fetch verified source archives")
    fetch.add_argument("sources", nargs="+")
    fetch.add_argument("--cache-dir", type=Path, required=True)
    fetch.add_argument("--offline", action="store_true")
    build = commands.add_parser("build", help="Build native libraries locally")
    build.add_argument("libraries", nargs="+")
    build.add_argument("--prefix", type=Path, required=True)
    build.add_argument("--cache-dir", type=Path, required=True)
    build.add_argument("--offline", action="store_true")
    build.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "list":
            print(json.dumps(manifest, indent=2, sort_keys=True))
        elif args.command == "build":
            rebuilt = build_native(
                manifest, args.libraries, args.prefix, args.cache_dir,
                offline=args.offline, jobs=args.jobs,
            )
            print(f"{'Built' if rebuilt else 'Reused'} dependencies: {args.prefix}")
            print("Use this prefix when configuring CPython:")
            print(f"PKG_CONFIG_PATH={shlex.quote(str(args.prefix.absolute() / 'lib/pkgconfig'))}")
        else:
            for source_id in args.sources:
                source = manifest["sources"][source_id]
                print(fetch_source(source, args.cache_dir, offline=args.offline))
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
