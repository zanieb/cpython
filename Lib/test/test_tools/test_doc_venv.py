"""Tests for documentation environment invalidation."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test import support
from test.support import os_helper
from test.test_tools import basepath

SCRIPT = Path(basepath) / 'Doc' / 'tools' / 'create_venv.py'
if not SCRIPT.is_file():
    raise unittest.SkipTest('documentation sources are unavailable')
spec = importlib.util.spec_from_file_location('create_doc_venv', SCRIPT)
create_doc_venv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(create_doc_venv)


class DocVenvTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.venv = self.root / 'venv'
        self.requirements = self.root / 'pylock.toml'
        self.requirements.write_text('runtime dependencies')
        self.bootstrap = self.root / 'bootstrap.txt'
        self.bootstrap.write_text('installer dependencies')
        self.build = self.root / 'build.txt'
        self.build.write_text('build dependencies')
        self.creations = 0
        self.fail_install = False
        self.run = self.enterContext(patch.object(
            create_doc_venv.subprocess, 'run', side_effect=self.fake_run))

    def fake_run(self, command, **kwargs):
        if command[1:3] == ['-m', 'venv']:
            self.creations += 1
            (self.venv / 'bin').mkdir(parents=True)
            (self.venv / 'pyvenv.cfg').write_text('home = /python')
            (self.venv / 'bin/python3').write_text('python')
        elif self.fail_install:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    def create(self, **kwargs):
        with support.captured_stdout():
            create_doc_venv.create_venv(
                self.venv, self.requirements, self.bootstrap,
                build_requirements=self.build, uv='', **kwargs)

    def test_unchanged_inputs_reuse_environment(self):
        self.create()
        self.run.reset_mock()
        self.create()
        self.run.assert_not_called()
        self.assertEqual(self.creations, 1)

    def test_changed_installation_inputs_recreate_environment(self):
        self.create()
        for path in (self.requirements, self.bootstrap, self.build):
            with self.subTest(path=path):
                stale_package = self.venv / 'removed-dependency'
                stale_package.touch()
                path.write_text(path.read_text() + '\nchanged')
                before = self.creations
                self.create()
                self.assertEqual(self.creations, before + 1)
                self.assertFalse(stale_package.exists())

    def test_changed_installation_options_recreate_environment(self):
        self.create()
        self.create(pip_options='--no-build-isolation')
        self.assertEqual(self.creations, 2)

    def test_changed_compiler_flags_recreate_environment(self):
        self.create()
        with patch.dict(create_doc_venv.os.environ, {'CFLAGS': '-O0'}):
            self.create()
        self.assertEqual(self.creations, 2)

    def test_installation_failure_is_not_cached(self):
        self.fail_install = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.create()
        self.assertFalse(self.venv.exists())
        self.fail_install = False
        self.create()
        self.assertTrue((self.venv / create_doc_venv.STAMP).is_file())
        self.assertEqual(self.creations, 2)

    def test_non_environment_directory_is_not_removed(self):
        self.venv.mkdir()
        sentinel = self.venv / 'user-file'
        sentinel.touch()
        with self.assertRaisesRegex(ValueError, 'non-venv directory'):
            self.create()
        self.assertTrue(sentinel.exists())

    @os_helper.skip_unless_symlink
    def test_symlink_is_not_followed(self):
        target = self.root / 'target'
        target.mkdir()
        self.venv.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.create()
        self.assertTrue(target.is_dir())


if __name__ == '__main__':
    unittest.main()
