"""Tests for verified native dependency downloads."""

import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from test.test_tools import imports_under_tool, skip_if_missing

skip_if_missing('build')
with imports_under_tool('build'):
    dependencies = importlib.import_module('download_dependencies')


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.archive = self.directory / 'archive.tar.gz'
        self.manifest = self.directory / 'manifest.json'
        self.url = 'https://example.invalid/archive.tar.gz'
        self.content = b'archive content'
        self.manifest.write_text(json.dumps({
            self.url: {
                'sha256': hashlib.sha256(self.content).hexdigest(),
                'url': 'https://example.invalid/immutable-commit.tar.gz',
            },
        }))
        self.retrieve = Mock(side_effect=self.write_archive)

    def write_archive(self, url, filename):
        Path(filename).write_bytes(self.content)

    def download(self, **kwargs):
        return dependencies.download(
            self.url, self.archive, retrieve=self.retrieve,
            manifest=self.manifest, **kwargs)

    def test_download_uses_pinned_url(self):
        self.assertEqual(self.download(), str(self.archive))
        self.assertEqual(self.archive.read_bytes(), self.content)
        self.assertEqual(self.retrieve.call_args.args[0],
                         'https://example.invalid/immutable-commit.tar.gz')
        self.assertEqual(list(self.directory.glob('*.part')), [])

    def test_verified_cache_needs_no_network(self):
        self.archive.write_bytes(self.content)
        self.download()
        self.retrieve.assert_not_called()

    def test_corrupt_cache_is_rejected(self):
        self.archive.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.download()
        self.retrieve.assert_not_called()

    def test_corrupt_download_is_not_published(self):
        self.retrieve.side_effect = lambda url, path: Path(path).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.download(mirrors=['https://example.invalid/mirror'])
        self.assertEqual(self.retrieve.call_count, 1)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.directory.glob('*.part')), [])

    def test_transport_failure_cleans_partial_download(self):
        def fail(url, path):
            Path(path).write_bytes(b'partial')
            raise OSError('connection interrupted')
        self.retrieve.side_effect = fail
        with self.assertRaisesRegex(OSError, 'connection interrupted'):
            self.download()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.directory.glob('*.part')), [])

    def test_transport_failure_tries_mirror(self):
        def retrieve(url, path):
            if url.endswith('immutable-commit.tar.gz'):
                raise OSError('unavailable')
            self.write_archive(url, path)
        self.retrieve.side_effect = retrieve
        with self.assertLogs(dependencies.log, level='WARNING'):
            self.download(mirrors=['https://example.invalid/mirror'])
        self.assertEqual(self.archive.read_bytes(), self.content)
        self.assertEqual(self.retrieve.call_count, 2)

    def test_missing_hash_fails_before_download(self):
        self.manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'No checked-in hash'):
            self.download()
        self.retrieve.assert_not_called()

    def test_invalid_hash_fails_before_download(self):
        self.manifest.write_text(json.dumps({self.url: {'sha256': 'abc'}}))
        with self.assertRaisesRegex(ValueError, 'Invalid SHA-256 hash'):
            self.download()
        self.retrieve.assert_not_called()

    def test_checked_in_manifest(self):
        with open(dependencies.MANIFEST) as source:
            manifest = json.load(source)
        self.assertTrue(manifest)
        for url, entry in manifest.items():
            with self.subTest(url=url):
                self.assertTrue(url.startswith('https://'))
                self.assertTrue(entry.get('url', url).startswith('https://'))
                self.assertEqual(dependencies.get_dependency(url), entry)


if __name__ == '__main__':
    unittest.main()
