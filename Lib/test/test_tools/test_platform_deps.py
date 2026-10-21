"""Tests for dependency handling in the platform build scripts."""

import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
