#!/usr/bin/env python3
"""Report configured native library flags and available runtime versions."""

import argparse
import importlib
import json
import platform
import shlex
import sys
import sysconfig
from pathlib import Path

MODULES = {
    "libffi": ("_ctypes", "libffi_version"),
    "mpdecimal": ("_decimal", "__libmpdec_version__"),
}


def has_directory_option(flags, option, directory):
    arguments = shlex.split(flags)
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            candidate = arguments[index + 1]
        elif argument.startswith(option) and argument != option:
            candidate = argument[len(option):]
        else:
            continue
        if Path(candidate).resolve() == Path(directory).resolve():
            return True
    return False


def collect_report(prefix=None):
    report = {
        "schema": 1,
        "python": sys.version,
        "platform": sysconfig.get_platform(),
        "machine": platform.machine(),
        "configure": sysconfig.get_config_var("CONFIG_ARGS"),
        "libraries": {},
        "errors": [],
    }
    recipes = {}
    if prefix is not None:
        prefix = Path(prefix).resolve()
        receipt = json.loads((prefix / ".cpython-dependencies.json").read_text())
        if receipt.get("schema") != 1:
            raise ValueError("Unsupported dependency receipt schema")
        report["managed-prefix"] = str(prefix)
        report["receipt"] = receipt
        recipes = receipt["inputs"]["recipes"]
        if set(recipes) - MODULES.keys():
            raise ValueError("The receipt contains libraries unsupported by this report")
        if not recipes:
            raise ValueError("The dependency receipt does not select native recipes")
    for name, (module_name, version_attribute) in MODULES.items():
        config_prefix = f"MODULE_{module_name.upper()}"
        entry = {
            "module": module_name,
            "state": sysconfig.get_config_var(config_prefix + "_STATE"),
            "cflags": sysconfig.get_config_var(config_prefix + "_CFLAGS") or "",
            "ldflags": sysconfig.get_config_var(config_prefix + "_LDFLAGS") or "",
        }
        report["libraries"][name] = entry
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            report["errors"].append(f"Cannot import {module_name}: {exc}")
            continue
        entry["module-path"] = module.__file__
        entry["runtime-version"] = getattr(module, version_attribute, None)
        if name not in recipes:
            continue
        recipe = recipes[name]
        source = receipt["inputs"]["sources"][recipe["source"]]
        entry["selected-source"] = source
        if not has_directory_option(entry["cflags"], "-I", prefix / "include"):
            report["errors"].append(f"{module_name} did not select the managed headers")
        archives = {
            str(prefix / filename) for filename in recipe["expected-files"]
            if filename.endswith(".a")
        }
        if not (
            has_directory_option(entry["ldflags"], "-L", prefix / "lib")
            or archives.intersection(shlex.split(entry["ldflags"]))
        ):
            report["errors"].append(f"{module_name} did not select the managed libraries")
        version = entry["runtime-version"]
        if version is not None and version != source["version"]:
            report["errors"].append(
                f"{module_name} reports {version}, expected {source['version']}"
            )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix", type=Path,
        help="Require flags for this prefix and matching available runtime versions",
    )
    args = parser.parse_args()
    try:
        report = collect_report(args.prefix)
    except (KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return bool(report["errors"])


if __name__ == "__main__":
    sys.exit(main())
