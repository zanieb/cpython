"""Regenerate selected CI dependency locks and record their inputs."""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check_ci_dependencies import LOCK_INPUTS, ROOT

ENVIRONMENTS = {
    "docs": ("Doc/pylock.toml", "3.12"),
    "epubcheck": ("Doc/pylock.epubcheck.toml", "3.12"),
    "typing": ("Tools/pylock.dev.toml", "3.15"),
    "hypothesis": ("Tools/pylock.hypothesis.toml", "3.12"),
    "build-tools": ("Tools/requirements-build.txt", "3.12"),
}


def regenerate(root, environment, *, uv="uv", upgrade=False,
               upgrade_packages=(), offline=False):
    filename, python_version = ENVIRONMENTS[environment]
    lock = root / filename
    command = [
        *shlex.split(uv), "pip", "compile", LOCK_INPUTS[filename][0],
        "--universal", "--python-version", python_version,
        "--exclude-newer", "P14D",
        "--custom-compile-command",
        f"python3 Tools/build/update_ci_dependencies.py {environment}",
    ]
    # Packages maintained by the core team are exempt from the 14-day cooldown.
    if environment == "docs":
        for package in ("blurb", "python-docs-theme", "sphinx_linklint"):
            command.extend(["--exclude-newer-package", f"{package}=PT0S"])
    if filename.endswith(".txt"):
        command.append("--generate-hashes")
    if upgrade:
        command.append("--upgrade")
    for package in upgrade_packages:
        command.extend(["--upgrade-package", package])
    if offline:
        command.append("--offline")

    # Seed resolution with the committed versions and publish only on success.
    with tempfile.TemporaryDirectory(dir=lock.parent) as directory:
        output = Path(directory) / lock.name
        shutil.copyfile(lock, output)
        subprocess.run([*command, "--output-file", str(output)],
                       cwd=root, check=True)
        os.replace(output, lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environments", nargs="+", choices=ENVIRONMENTS)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--upgrade-package", action="append", default=[])
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    for environment in args.environments:
        regenerate(ROOT, environment, uv=args.uv, upgrade=args.upgrade,
                   upgrade_packages=args.upgrade_package, offline=args.offline)
    subprocess.run([
        sys.executable, str(ROOT / "Tools/build/check_ci_dependencies.py"),
        "--record", *(ENVIRONMENTS[name][0] for name in args.environments),
    ], cwd=ROOT, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
