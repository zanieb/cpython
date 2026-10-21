#!/usr/bin/env python3
"""Fetch the source dependencies selected by CPython build recipes."""

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import urlopen

MANIFEST = Path(__file__).with_name("dependencies.json")


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path=MANIFEST):
    manifest = json.loads(Path(path).read_text())
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported native dependency manifest schema")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("The dependency manifest must define sources")
    for source_id, source in sources.items():
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
        license_path = PurePosixPath(source["license-file"])
        if license_path.is_absolute() or ".." in license_path.parts:
            raise ValueError(f"Invalid license path for {source_id}")
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
    return destination / source["archive-root"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Print the selected source metadata")
    fetch = commands.add_parser("fetch", help="Fetch verified source archives")
    fetch.add_argument("sources", nargs="+")
    fetch.add_argument("--cache-dir", type=Path, required=True)
    fetch.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "list":
            print(json.dumps(manifest, indent=2, sort_keys=True))
        else:
            for source_id in args.sources:
                source = manifest["sources"][source_id]
                print(fetch_source(source, args.cache_dir, offline=args.offline))
    except (KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
