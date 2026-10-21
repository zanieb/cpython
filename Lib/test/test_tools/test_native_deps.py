"""Tests for the managed native dependency tools."""

import hashlib
import importlib.util
import io
import json
import os
import shlex
import sys
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
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


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "native_deps_report", ROOT / "Tools/build/native_deps_report.py")
        cls.reporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.reporter)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = Path(self.tmp.name)
        manifest = deps.load_manifest()
        receipt = {"schema": 1, "inputs": {
            "recipes": manifest["native-builds"], "sources": manifest["sources"],
        }}
        (self.prefix / ".cpython-dependencies.json").write_text(json.dumps(receipt))
        self.config = {}
        for name in ("_CTYPES", "_DECIMAL"):
            self.config[f"MODULE_{name}_CFLAGS"] = shlex.quote(f"-I{self.prefix}/include")
            self.config[f"MODULE_{name}_LDFLAGS"] = shlex.quote(f"-L{self.prefix}/lib")
        self.module = SimpleNamespace(__file__="extension.so", __libmpdec_version__="4.0.1")
        patcher = patch.object(self.reporter.sysconfig, "get_config_var", self.config.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(self.reporter.importlib, "import_module", return_value=self.module)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_selected_prefix(self):
        report = self.reporter.collect_report(self.prefix)
        self.assertEqual(report["errors"], [])
        self.assertIsNone(report["libraries"]["libffi"]["runtime-version"])

    def test_reject_other_prefix(self):
        self.config["MODULE__CTYPES_CFLAGS"] = "-I/another/include"
        report = self.reporter.collect_report(self.prefix)
        self.assertEqual(report["errors"], ["_ctypes did not select the managed headers"])

    def test_reject_runtime_version_mismatch(self):
        self.module.__libmpdec_version__ = "2.5.1"
        report = self.reporter.collect_report(self.prefix)
        self.assertEqual(report["errors"], ["_decimal reports 2.5.1, expected 4.0.1"])


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "native_deps_artifact", ROOT / "Tools/build/native_deps_artifact.py")
        cls.artifact = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(ROOT / "Tools/build"), *sys.path]):
            spec.loader.exec_module(cls.artifact)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.prefix = self.directory / "prefix"
        self.prefix.mkdir()
        self.output = self.directory / "artifact"
        manifest = deps.load_manifest()
        recipe = manifest["native-builds"]["libffi"]
        for filename in [*recipe["expected-files"], "share/licenses/libffi/LICENSE"]:
            path = self.prefix / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"installed file")
        self.receipt = {"schema": 1, "inputs": {
            "recipes": {"libffi": recipe},
            "sources": {"libffi-3.4.6": manifest["sources"]["libffi-3.4.6"]},
        }}
        (self.prefix / ".cpython-dependencies.json").write_text(json.dumps(self.receipt))

    def test_export_records_actual_files_and_selected_sources(self):
        metadata = self.artifact.export_prefix(self.prefix, self.output)
        archive = self.output / metadata["archive"]["filename"]
        self.assertEqual(metadata["archive"]["sha256"], deps.file_digest(archive))
        with tarfile.open(archive) as tar:
            for name, info in metadata["files"].items():
                payload = tar.extractfile(f"prefix/{name}").read()
                self.assertEqual(info["sha256"], hashlib.sha256(payload).hexdigest())
        sbom = json.loads((self.output / "native-dependencies.spdx.json").read_text())
        self.assertEqual([p["name"] for p in sbom["packages"]], ["libffi"])
        self.assertEqual(metadata["receipt"], self.receipt)

    def test_missing_library_does_not_publish(self):
        (self.prefix / "lib/libffi.a").unlink()
        with self.assertRaisesRegex(ValueError, "missing lib/libffi.a"):
            self.artifact.export_prefix(self.prefix, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require privileges")
    def test_external_symlink_does_not_publish(self):
        outside = self.directory / "outside"
        outside.write_bytes(b"unrelated file")
        (self.prefix / "escape").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "escapes its prefix"):
            self.artifact.export_prefix(self.prefix, self.output)
        self.assertFalse(self.output.exists())

    def test_output_inside_prefix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self.artifact.export_prefix(self.prefix, self.prefix / "artifact")


if __name__ == "__main__":
    unittest.main()
