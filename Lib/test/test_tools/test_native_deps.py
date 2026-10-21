"""Tests for the managed native dependency tools."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if not (ROOT / "Tools/build/native_deps.py").is_file():
    raise unittest.SkipTest("requires the CPython source tree")
SPEC = importlib.util.spec_from_file_location(
    "native_deps", ROOT / "Tools/build/native_deps.py")
deps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deps)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.payload = b"verified source archive"
        self.source = dict(
            name="example", version="1", url="https://example.invalid/source",
            sha256=hashlib.sha256(self.payload).hexdigest(),
        )
        self.source.update({
            "archive-root": "example-1", "license": "MIT",
            "license-file": "LICENSE", "purl": "pkg:generic/example@1",
        })

    def test_fetch_and_offline_reuse(self):
        with patch.object(deps, "urlopen", return_value=io.BytesIO(self.payload)):
            archive = deps.fetch_source(self.source, self.directory)
        with patch.object(deps, "urlopen") as fetch:
            self.assertEqual(deps.fetch_source(
                self.source, self.directory, offline=True), archive)
            fetch.assert_not_called()

    def test_wrong_digest_is_not_published(self):
        with patch.object(deps, "urlopen", return_value=io.BytesIO(b"wrong")):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                deps.fetch_source(self.source, self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_corrupt_cache_is_repaired(self):
        archive = self.directory / self.source["sha256"]
        archive.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            deps.fetch_source(self.source, self.directory, offline=True)
        with patch.object(deps, "urlopen", return_value=io.BytesIO(self.payload)):
            self.assertEqual(deps.fetch_source(self.source, self.directory), archive)
        self.assertEqual(archive.read_bytes(), self.payload)

    def test_offline_miss_does_not_download(self):
        with patch.object(deps, "urlopen") as fetch:
            with self.assertRaises(FileNotFoundError):
                deps.fetch_source(self.source, self.directory, offline=True)
            fetch.assert_not_called()

    def test_invalid_manifest_digest(self):
        self.source["sha256"] = "not a digest"
        manifest = self.directory / "manifest.json"
        manifest.write_text(json.dumps({
            "schema": 1, "sources": {"example-1": self.source}}))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            deps.load_manifest(manifest)

    def test_extract_declared_root(self):
        archive = self.directory / "source.tar"
        with tarfile.open(archive, "w") as tar:
            member = tarfile.TarInfo("example-1/source.c")
            member.size = 4
            tar.addfile(member, io.BytesIO(b"code"))
        root = deps.extract_source(archive, self.directory, self.source)
        self.assertEqual((root / "source.c").read_bytes(), b"code")

    def test_reject_archive_escape(self):
        for filename in ("../escaped", "example-1/../../escaped"):
            with self.subTest(filename=filename):
                archive = self.directory / "source.tar"
                with tarfile.open(archive, "w") as tar:
                    tar.addfile(tarfile.TarInfo(filename))
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    deps.extract_source(archive, self.directory / "out", self.source)
                self.assertFalse((self.directory / "escaped").exists())

    def test_invalid_manifest_paths(self):
        for filename in ("../LICENSE", "/LICENSE", "C:/LICENSE", "..\\LICENSE"):
            with self.subTest(filename=filename):
                self.source["license-file"] = filename
                manifest = self.directory / "manifest.json"
                manifest.write_text(json.dumps({
                    "schema": 1, "sources": {"example-1": self.source}}))
                with self.assertRaisesRegex(ValueError, "license path"):
                    deps.load_manifest(manifest)

    def test_build_inputs_track_flags_and_selection(self):
        manifest = deps.load_manifest()
        with patch.object(deps, "tool_identity", return_value={"version": "cc 1"}):
            with patch.dict(os.environ, {"CFLAGS": "-O0"}):
                first = deps.build_inputs(manifest, ["libffi"], self.directory)
            with patch.dict(os.environ, {"CFLAGS": "-O2"}):
                second = deps.build_inputs(manifest, ["libffi"], self.directory)
                both = deps.build_inputs(
                    manifest, ["libffi", "mpdecimal"], self.directory)
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, both)
        self.assertEqual(first["environment"]["CFLAGS"], "-O0 -fPIC")
        self.assertEqual(set(first["sources"]), {"libffi-3.4.6"})

    def test_checked_in_manifest(self):
        manifest = deps.load_manifest()
        self.assertIn("libffi-3.4.6", manifest["sources"])
        self.assertIn("mpdecimal-4.0.1", manifest["sources"])


if __name__ == "__main__":
    unittest.main()
