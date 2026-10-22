"""Tests for the offline CI dependency consistency checks."""

import hashlib
import importlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test.test_tools import imports_under_tool, skip_if_missing

skip_if_missing("build")
with imports_under_tool("build"):
    dependencies = importlib.import_module("check_ci_dependencies")
    updater = importlib.import_module("update_ci_dependencies")


class DependencyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "requirements.in"
        self.source.write_text("example==1.0\n", newline="\n")
        self.lock = self.root / "pylock.toml"
        self.lock.write_text(
            '[[packages]]\nname = "example"\nversion = "1.0"\n'
            'wheels = [{url = "https://example.invalid/example.whl", '
            'hashes = {sha256 = "' + "a" * 64 + '"}}]\n',
            newline="\n",
        )
        self.inventory = {"pylock.toml": ("requirements.in",)}

    def snapshot(self):
        return dependencies.snapshot(self.root, self.inventory)

    def test_unchanged_inputs(self):
        recorded = self.snapshot()
        self.assertEqual(dependencies.check_record(self.snapshot(), recorded), [])

    def test_checkout_line_endings(self):
        recorded = self.snapshot()
        for path in (self.source, self.lock):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(dependencies.check_record(self.snapshot(), recorded), [])

    def test_source_change_requires_refresh(self):
        recorded = self.snapshot()
        self.source.write_text("example>=2\n")
        self.assertTrue(dependencies.check_record(self.snapshot(), recorded))

    def test_lock_change_requires_refresh(self):
        recorded = self.snapshot()
        self.lock.write_text(self.lock.read_text().replace('"1.0"', '"2.0"'))
        self.assertTrue(dependencies.check_record(self.snapshot(), recorded))

    def test_constraints_are_recorded(self):
        constraint = self.root / "constraints.txt"
        constraint.write_text("dependency<2\n")
        self.inventory["pylock.toml"] += ("constraints.txt",)
        recorded = self.snapshot()
        constraint.write_text("dependency<3\n")
        self.assertTrue(dependencies.check_record(self.snapshot(), recorded))

    def use_groups(self, content):
        self.source = self.root / "pyproject.toml"
        self.source.write_text(content, encoding="utf-8", newline="\n")
        self.inventory = {"pylock.toml": ("pyproject.toml:docs",)}

    def test_selected_group_change_requires_refresh(self):
        self.use_groups('[dependency-groups]\ndocs = ["example"]\n')
        recorded = self.snapshot()
        self.source.write_text('[dependency-groups]\ndocs = ["example>=2"]\n')
        self.assertTrue(dependencies.check_record(self.snapshot(), recorded))

    def test_unrelated_group_and_metadata_do_not_require_refresh(self):
        self.use_groups('[dependency-groups]\ndocs = ["example"]\n')
        recorded = self.snapshot()
        self.source.write_text(
            '# Only the selected group contributes to its input record.\n'
            '[dependency-groups]\ndocs = ["example"]\n'
            'typing = ["other"]\n[tool.example]\noption = true\n',
        )
        self.assertEqual(dependencies.check_record(self.snapshot(), recorded), [])

    def test_included_group_change_requires_refresh(self):
        self.use_groups(
            '[dependency-groups]\ndocs = [{include-group = "shared"}]\n'
            'shared = ["example"]\n',
        )
        recorded = self.snapshot()
        self.source.write_text(
            self.source.read_text().replace('"example"', '"example>=2"'),
        )
        self.assertTrue(dependencies.check_record(self.snapshot(), recorded))

    def test_nested_group_includes_and_normalized_names(self):
        self.use_groups(
            '[dependency-groups]\n'
            'docs = ["first", {include-group = "shared-docs"}, "last"]\n'
            'Shared_Docs = ["example", {include-group = "base"}]\n'
            'base = ["first"]\n',
        )
        self.assertEqual(
            dependencies.group_requirements(self.source, "docs"),
            ["first", "example", "first", "last"],
        )

    def test_group_requirements_are_checked_against_lock(self):
        self.use_groups('[dependency-groups]\ndocs = ["example"]\n')
        self.assertEqual(
            dependencies.check_python_locks(self.root, self.inventory), [],
        )
        self.source.write_text('[dependency-groups]\ndocs = ["missing"]\n')
        errors = dependencies.check_python_locks(self.root, self.inventory)
        self.assertTrue(any("missing direct requirement missing" in e for e in errors))

    def test_invalid_dependency_groups(self):
        cases = [
            ('dependency-groups = []\n', "must be a table"),
            ('[dependency-groups]\nother = []\n', "missing dependency group"),
            ('[dependency-groups]\ndocs = "example"\n', "must be an array"),
            ('[dependency-groups]\ndocs = [1]\n', "invalid item"),
            ('[dependency-groups]\ndocs = [{include-group = 1}]\n', "invalid item"),
            ('[dependency-groups]\n'
             'docs = [{include-group = "base", extra = true}]\n', "invalid item"),
            ('[dependency-groups]\ndocs = [{include-group = "docs"}]\n', "group cycle"),
            ('[dependency-groups]\ndocs = [{include-group = "base"}]\n'
             'base = [{include-group = "docs"}]\n', "group cycle"),
            ('[dependency-groups]\ndocs = []\n"invalid name" = []\n',
             "invalid dependency group name"),
            ('[dependency-groups]\ndocs = []\n'
             'Shared_Docs = []\nshared-docs = []\n', "duplicate dependency group"),
        ]
        for content, message in cases:
            with self.subTest(content=content):
                self.use_groups(content)
                with self.assertRaisesRegex(ValueError, message):
                    self.snapshot()

    def test_group_checkout_line_endings(self):
        self.use_groups('[dependency-groups]\ndocs = ["example"]\n')
        self.test_checkout_line_endings()

    def test_group_constraints_are_recorded(self):
        self.use_groups('[dependency-groups]\ndocs = ["example"]\n')
        self.test_constraints_are_recorded()

    def test_removed_lock_requires_record_update(self):
        self.assertTrue(dependencies.check_record({}, self.snapshot()))

    def test_matching_direct_pin(self):
        self.assertEqual(
            dependencies.check_python_locks(self.root, self.inventory), [],
        )

    def test_stale_direct_pin(self):
        self.source.write_text("example==2.0\n")
        errors = dependencies.check_python_locks(self.root, self.inventory)
        self.assertTrue(any("missing direct pin example==2.0" in e for e in errors))

    def test_bare_direct_requirement(self):
        self.source.write_text("example\n")
        self.assertEqual(
            dependencies.check_python_locks(self.root, self.inventory), [],
        )
        self.source.write_text("missing\n")
        errors = dependencies.check_python_locks(self.root, self.inventory)
        self.assertTrue(any("missing direct requirement missing" in e for e in errors))

    def test_missing_artifact_hash(self):
        self.lock.write_text(self.lock.read_text().replace("a" * 64, "invalid"))
        errors = dependencies.check_python_locks(self.root, self.inventory)
        self.assertTrue(any("SHA-256" in e for e in errors))

    def test_bootstrap_matches_wheel(self):
        wheel = self.root / "Lib/ensurepip/_bundled/pip-1.0-py3-none-any.whl"
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b"bundled\r\npip wheel")
        bootstrap = self.root / "Tools/requirements-bootstrap.txt"
        bootstrap.parent.mkdir()
        sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
        bootstrap.write_text(f"pip==1.0 --hash=sha256:{sha256}\n")
        self.assertEqual(dependencies.check_bootstrap(self.root), [])
        wheel.write_bytes(b"changed wheel")
        self.assertTrue(dependencies.check_bootstrap(self.root))

    def test_bootstrap_version_must_match(self):
        wheel = self.root / "Lib/ensurepip/_bundled/pip-2.0-py3-none-any.whl"
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b"bundled pip wheel")
        bootstrap = self.root / "Tools/requirements-bootstrap.txt"
        bootstrap.parent.mkdir()
        sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
        bootstrap.write_text(f"pip==1.0 --hash=sha256:{sha256}\n")
        self.assertTrue(dependencies.check_bootstrap(self.root))


class RegenerationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.lock = self.root / "Tools/pylock.dev.toml"
        self.lock.parent.mkdir()
        self.lock.write_bytes(b"committed lock\n")

    def test_failed_compile_keeps_committed_lock(self):
        def fail(command, **kwargs):
            output = Path(command[command.index("--output-file") + 1])
            self.assertEqual(output.read_bytes(), b"committed lock\n")
            output.write_bytes(b"incomplete lock\n")
            raise subprocess.CalledProcessError(1, command)

        with patch.object(updater.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                updater.regenerate(self.root, "typing")
        self.assertEqual(self.lock.read_bytes(), b"committed lock\n")
        self.assertEqual(list(self.lock.parent.iterdir()), [self.lock])

    def test_successful_compile_replaces_lock(self):
        def compile_lock(command, **kwargs):
            output = Path(command[command.index("--output-file") + 1])
            self.assertEqual(output.read_bytes(), b"committed lock\n")
            output.write_bytes(b"regenerated lock\n")

        with patch.object(updater.subprocess, "run", side_effect=compile_lock):
            updater.regenerate(self.root, "typing")
        self.assertEqual(self.lock.read_bytes(), b"regenerated lock\n")
        self.assertEqual(list(self.lock.parent.iterdir()), [self.lock])


if __name__ == "__main__":
    unittest.main()
