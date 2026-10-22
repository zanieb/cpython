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
    "Doc/pylock.toml": ("Doc/requirements.in", "Doc/constraints.txt"),
    "Doc/pylock.epubcheck.toml": ("Doc/requirements-epubcheck.in",),
    "Tools/pylock.dev.toml": ("Tools/requirements-dev.in",),
    "Tools/pylock.hypothesis.toml": ("Tools/requirements-hypothesis.in",),
    "Tools/requirements-build.txt": ("Tools/requirements-build.in",),
}


def digest(path, *, normalize_newlines=False):
    content = path.read_bytes()
    if normalize_newlines:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def snapshot(root, inventory=LOCK_INPUTS):
    return {
        lock: {
            "sha256": digest(root / lock, normalize_newlines=True),
            "inputs": {
                source: digest(root / source, normalize_newlines=True)
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
        source = root / sources[0]
        for line in source.read_text(encoding="utf-8").splitlines():
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
    current = snapshot(ROOT)
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
