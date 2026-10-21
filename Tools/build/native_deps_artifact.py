#!/usr/bin/env python3
"""Export a managed native dependency prefix with its source and build metadata."""

import argparse
import datetime
import json
import os
import tarfile
import tempfile
import uuid
from pathlib import Path

from dependency_cache import RECEIPT, _prefix_lock
from native_deps import file_digest


def source_sbom(sources):
    packages = []
    relationships = []
    for index, (_, source) in enumerate(sorted(sources.items())):
        package_id = f"SPDXRef-Source-{index}"
        packages.append({
            "SPDXID": package_id,
            "name": source["name"],
            "versionInfo": source["version"],
            "downloadLocation": source["url"],
            "checksums": [{"algorithm": "SHA256", "checksumValue": source["sha256"]}],
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": source["license"],
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "SOURCE",
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": source["purl"],
            }],
        })
        relationships.append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": package_id,
        })
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "CPython managed native dependency sources",
        "documentNamespace": f"https://python.org/spdx/native-dependencies/{uuid.uuid4()}",
        "creationInfo": {
            "creators": ["Tool: CPython-native-deps"],
            "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "packages": packages,
        "relationships": relationships,
    }


def export_prefix(prefix, output):
    prefix = Path(prefix).resolve()
    output = Path(output).resolve()
    if output == prefix or prefix in output.parents:
        raise ValueError("The artifact directory must be outside the dependency prefix")
    if output.exists():
        raise ValueError(f"The artifact directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with _prefix_lock(prefix):
        receipt = json.loads((prefix / RECEIPT).read_text())
        if receipt.get("schema") != 1:
            raise ValueError("Unsupported dependency receipt schema")
        inputs = receipt["inputs"]
        if not inputs.get("recipes"):
            raise ValueError("The dependency receipt does not select native recipes")
        for name, recipe in inputs["recipes"].items():
            source = inputs["sources"][recipe["source"]]
            expected = [
                *recipe["expected-files"],
                f"share/licenses/{name}/{Path(source['license-file']).name}",
            ]
            for filename in expected:
                if not (prefix / filename).is_file():
                    raise ValueError(f"Dependency prefix is missing {filename}")
        with tempfile.TemporaryDirectory(
            dir=output.parent, prefix=".native-artifact-"
        ) as tmp:
            staging = Path(tmp) / "artifact"
            staging.mkdir()
            archive = staging / "native-dependencies.tar.gz"
            files = {}
            with tarfile.open(archive, "w:gz", dereference=False) as tar:
                for path in sorted(prefix.rglob("*")):
                    relative = path.relative_to(prefix).as_posix()
                    if path.is_symlink():
                        target = path.resolve()
                        if prefix not in target.parents or not target.exists():
                            raise ValueError(f"Dependency symlink escapes its prefix: {relative}")
                        files[relative] = {"symlink": os.readlink(path)}
                    elif path.is_file():
                        files[relative] = {"sha256": file_digest(path), "size": path.stat().st_size}
                    elif not path.is_dir():
                        raise ValueError(f"Unsupported dependency file: {relative}")
                    tar.add(path, arcname=f"prefix/{relative}", recursive=False)
            metadata = {
                "schema": 1,
                "archive": {"filename": archive.name, "sha256": file_digest(archive)},
                "receipt": receipt,
                "files": files,
                "relocatable": False,
            }
            for filename, content in (
                ("native-dependencies.json", metadata),
                ("native-dependencies.spdx.json", source_sbom(inputs["sources"])),
            ):
                (staging / filename).write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
            # Publish the complete export together so an interrupted export is
            # never mistaken for an artifact with all required metadata.
            staging.rename(output)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = export_prefix(args.prefix, args.output)
    except (KeyError, OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(metadata["archive"], indent=2))


if __name__ == "__main__":
    main()
