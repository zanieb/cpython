"""Tests for the offline CI dependency consistency checks."""

import hashlib
import importlib
from pathlib import Path
import tempfile
import unittest

from test.test_tools import imports_under_tool, skip_if_missing

skip_if_missing("build")
with imports_under_tool("build"):
    dependencies = importlib.import_module("check_ci_dependencies")


class DependencyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "requirements.txt"
        self.source.write_text("example==1.0\n")
        self.lock = self.root / "pylock.toml"
        self.lock.write_text(
            '[[packages]]\nname = "example"\nversion = "1.0"\n'
            'wheels = [{url = "https://example.invalid/example.whl", '
            'hashes = {sha256 = "' + "a" * 64 + '"}}]\n'
        )
        self.inventory = {"pylock.toml": ("requirements.txt",)}

    def snapshot(self):
        return dependencies.snapshot(self.root, self.inventory)

    def test_unchanged_inputs(self):
        recorded = self.snapshot()
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

    def test_missing_artifact_hash(self):
        self.lock.write_text(self.lock.read_text().replace("a" * 64, "invalid"))
        errors = dependencies.check_python_locks(self.root, self.inventory)
        self.assertTrue(any("SHA-256" in e for e in errors))

    def test_bootstrap_matches_wheel(self):
        wheel = self.root / "Lib/ensurepip/_bundled/pip-1.0-py3-none-any.whl"
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b"bundled pip wheel")
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


if __name__ == "__main__":
    unittest.main()
