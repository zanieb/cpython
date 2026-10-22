"""Check recorded CI lock inputs and selected metadata without network access."""

import argparse
import hashlib
import json
import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path("Tools/build/ci-dependency-inputs.json")
LOCK_INPUTS = {
    "Doc/pylock.toml": ("pyproject.toml:docs", "Doc/constraints.txt"),
    "Doc/pylock.epubcheck.toml": ("pyproject.toml:epubcheck",),
    "Tools/pylock.dev.toml": ("pyproject.toml:typing",),
    "Tools/pylock.hypothesis.toml": ("pyproject.toml:hypothesis",),
    "Tools/requirements-build.txt": ("pyproject.toml:build-tools",),
}


def digest(path, *, normalize_newlines=False):
    content = path.read_bytes()
    if normalize_newlines:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def group_requirements(path, group):
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    definitions = data.get("dependency-groups", {})
    if not isinstance(definitions, dict):
        raise ValueError(f"{path}: dependency-groups must be a table")
    groups = {}
    for name, requirements in definitions.items():
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name):
            raise ValueError(f"{path}: invalid dependency group name {name!r}")
        key = normalize(name)
        if key in groups:
            raise ValueError(f"{path}: duplicate dependency group {key!r}")
        groups[key] = requirements

    def expand(name, parents=()):
        name = normalize(name)
        if name in parents:
            cycle = " -> ".join((*parents, name))
            raise ValueError(f"{path}: dependency group cycle: {cycle}")
        if name not in groups:
            raise ValueError(f"{path}: missing dependency group {name!r}")
        if not isinstance(groups[name], list):
            raise ValueError(f"{path}: dependency group {name!r} must be an array")
        requirements = []
        for item in groups[name]:
            if isinstance(item, str):
                requirements.append(item)
            elif (isinstance(item, dict) and set(item) == {"include-group"}
                  and isinstance(item["include-group"], str)):
                requirements.extend(
                    expand(item["include-group"], (*parents, name)),
                )
            else:
                raise ValueError(
                    f"{path}: invalid item in dependency group {name!r}: {item!r}",
                )
        return requirements

    return expand(group)


def source_requirements(root, source):
    filename, separator, group = source.partition(":")
    if separator:
        return group_requirements(root / filename, group)
    return (root / filename).read_text(encoding="utf-8").splitlines()


def source_digest(root, source):
    if ":" not in source:
        return digest(root / source, normalize_newlines=True)
    # Hash only the selected group and its includes, not unrelated TOML data.
    requirements = source_requirements(root, source)
    return hashlib.sha256(json.dumps(requirements).encode("utf-8")).hexdigest()


def snapshot(root, inventory=LOCK_INPUTS):
    return {
        lock: {
            "sha256": digest(root / lock, normalize_newlines=True),
            "inputs": {
                source: source_digest(root, source)
                for source in sources
            },
        }
        for lock, sources in inventory.items()
    }


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def check_python_locks(root, inventory=LOCK_INPUTS):
    errors = []
    for filename, sources in inventory.items():
        if not filename.endswith(".toml"):
            continue
        lock = tomllib.loads((root / filename).read_text(encoding="utf-8"))
        packages = lock.get("packages", [])
        if not packages:
            errors.append(f"{filename}: no locked packages")
        versions = {}
        for package in packages:
            name = normalize(package["name"])
            versions.setdefault(name, set()).add(package.get("version"))
            artifacts = [*package.get("wheels", [])]
            if "sdist" in package:
                artifacts.append(package["sdist"])
            if not package.get("version") or not artifacts:
                errors.append(f"{filename}: {name} needs a version and artifacts")
            for artifact in artifacts:
                sha256 = artifact.get("hashes", {}).get("sha256", "")
                if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                    errors.append(f"{filename}: {name} has no valid SHA-256 hash")

        # Check bare requirements and exact pins after an input-record update.
        for line in source_requirements(root, sources[0]):
            match = re.fullmatch(
                r"([A-Za-z0-9_.-]+)(?:==([^\s;*]+))?", line.strip(),
            )
            if not match:
                continue
            locked = versions.get(normalize(match[1]), set())
            if not locked:
                errors.append(f"{filename}: missing direct requirement {match[1]}")
            elif match[2] and match[2] not in locked:
                errors.append(f"{filename}: missing direct pin {line.strip()}")
    return errors


def check_bootstrap(root):
    wheels = list((root / "Lib/ensurepip/_bundled").glob("pip-*.whl"))
    if len(wheels) != 1:
        return ["Expected one bundled pip wheel"]
    wheel = wheels[0]
    version = wheel.name.split("-")[1]
    bootstrap = (root / "Tools/requirements-bootstrap.txt").read_text()
    if (not re.search(rf"(?m)^pip=={re.escape(version)}(?:\s|$)", bootstrap)
            or f"--hash=sha256:{digest(wheel)}" not in bootstrap):
        return ["Tools/requirements-bootstrap.txt must match the bundled pip wheel"]
    return []


def check_record(current, recorded):
    return [
        f"{lock}: regenerate the lock, then record its inputs with "
        f"Tools/build/check_ci_dependencies.py --record {lock}"
        for lock in sorted(current.keys() | recorded.keys())
        if current.get(lock) != recorded.get(lock)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record", nargs="+", choices=LOCK_INPUTS,
        help="record selected locks after regenerating them",
    )
    args = parser.parse_args()
    try:
        current = snapshot(ROOT)
    except (OSError, ValueError) as error:
        print(f"Invalid dependency inputs: {error}")
        return True
    record = ROOT / RECORD
    recorded = json.loads(record.read_text()) if record.exists() else {}
    errors = check_python_locks(ROOT) + check_bootstrap(ROOT)
    if args.record and not errors:
        for lock in args.record:
            recorded[lock] = current[lock]
        record.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    errors.extend(check_record(current, recorded))
    for error in errors:
        print(error)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
