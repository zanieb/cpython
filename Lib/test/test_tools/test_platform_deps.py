"""Tests for dependency handling in the platform build scripts."""

import importlib.util
import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if not (ROOT / "Platforms/Android/__main__.py").is_file():
    raise unittest.SkipTest("requires the CPython source tree")


def load_platform(name):
    path = ROOT / "Platforms" / name / "__main__.py"
    spec = importlib.util.spec_from_file_location(f"build_{name}", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"ANDROID_HOME": str(ROOT)}):
        spec.loader.exec_module(module)
    return module


class DownloadTests(unittest.TestCase):
    def test_interrupted_download_is_not_cached(self):
        for name in ("Android", "Apple"):
            with self.subTest(platform=name):
                module = load_platform(name)
                with tempfile.TemporaryDirectory() as tmp:
                    cache = Path(tmp)
                    url = "https://example.invalid/dependency.tar.gz"
                    final = cache / "dependency.tar.gz"

                    def interrupted(command):
                        destination = Path(command[command.index("-o") + 1])
                        destination.write_bytes(b"partial archive")
                        raise subprocess.CalledProcessError(18, command)

                    with patch.object(module, "run", interrupted):
                        with self.assertRaises(subprocess.CalledProcessError):
                            module.download(url, cache)
                    self.assertFalse(final.exists())
                    self.assertEqual(list(cache.iterdir()), [])

                    def complete(command):
                        destination = Path(command[command.index("-o") + 1])
                        self.assertFalse(final.exists())
                        destination.write_bytes(b"complete archive")

                    with patch.object(module, "run", complete):
                        self.assertTrue(module.download(url, cache).samefile(final))
                    self.assertEqual(final.read_bytes(), b"complete archive")
                    self.assertEqual(list(cache.iterdir()), [final])

    def test_completed_download_is_reused(self):
        for name in ("Android", "Apple"):
            with self.subTest(platform=name):
                module = load_platform(name)
                with tempfile.TemporaryDirectory() as tmp:
                    cache = Path(tmp)
                    final = cache / "dependency.tar.gz"
                    final.write_bytes(b"complete archive")
                    with patch.object(module, "run") as run:
                        result = module.download(
                            "https://example.invalid/dependency.tar.gz", cache)
                    self.assertTrue(result.samefile(final))
                    self.assertEqual(final.read_bytes(), b"complete archive")
                    run.assert_not_called()


class PrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "Tools/build/dependency_cache.py"
        spec = importlib.util.spec_from_file_location("dependency_cache", path)
        cls.cache = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cache)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = Path(self.tmp.name) / "prefix"

    def install(self, staging):
        (staging / "library").write_text("complete")

    def test_reuse_and_update(self):
        self.assertTrue(self.cache.install_prefix(
            self.prefix, {"version": "1"}, self.install))
        with patch.object(self, "install") as install:
            self.assertFalse(self.cache.install_prefix(
                self.prefix, {"version": "1"}, install))
            install.assert_not_called()
        self.assertTrue(self.cache.install_prefix(
            self.prefix, {"version": "2"}, self.install))
        receipt = json.loads((self.prefix / self.cache.RECEIPT).read_text())
        self.assertEqual(receipt["inputs"], {"version": "2"})

    def test_failed_install_can_retry(self):
        def fail(staging):
            (staging / "partial").touch()
            raise RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.cache.install_prefix(self.prefix, {}, fail)
        self.assertFalse(self.prefix.exists())
        self.assertTrue(self.cache.install_prefix(
            self.prefix, {}, self.install))
        self.assertFalse((self.prefix / "partial").exists())

    def test_failed_update_keeps_previous_install(self):
        self.cache.install_prefix(self.prefix, {"version": "1"}, self.install)
        def fail(staging):
            raise RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.cache.install_prefix(self.prefix, {"version": "2"}, fail)
        receipt = json.loads((self.prefix / self.cache.RECEIPT).read_text())
        self.assertEqual(receipt["inputs"], {"version": "1"})
        self.assertEqual((self.prefix / "library").read_text(), "complete")

    def test_unmanaged_prefix_requires_override(self):
        self.prefix.mkdir()
        (self.prefix / "local").touch()
        with self.assertRaisesRegex(ValueError, "the existing prefix"):
            self.cache.install_prefix(self.prefix, {}, self.install)
        self.assertFalse(self.cache.install_prefix(
            self.prefix, {}, self.install, use_existing=True))
        self.assertTrue((self.prefix / "local").exists())
        self.assertFalse((self.prefix / self.cache.RECEIPT).exists())

    def test_publish_failure_restores_previous_install(self):
        self.cache.install_prefix(self.prefix, {"version": "1"}, self.install)
        rename = Path.rename
        def fail_publish(path, target):
            if target == self.prefix and "previous" not in str(path):
                raise OSError("publish failed")
            return rename(path, target)
        with patch.object(Path, "rename", fail_publish):
            with self.assertRaisesRegex(OSError, "publish failed"):
                self.cache.install_prefix(
                    self.prefix, {"version": "2"}, self.install)
        receipt = json.loads((self.prefix / self.cache.RECEIPT).read_text())
        self.assertEqual(receipt["inputs"], {"version": "1"})

    def test_overlapping_install_is_rejected(self):
        def overlap(staging):
            with self.assertRaisesRegex(RuntimeError, "in use"):
                self.cache.install_prefix(self.prefix, {}, self.install)
            self.install(staging)
        self.assertTrue(self.cache.install_prefix(self.prefix, {}, overlap))


if __name__ == "__main__":
    unittest.main()
