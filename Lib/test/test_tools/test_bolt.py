"""Exercise the BOLT build rules without requiring an LLVM installation."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


MAKE = shutil.which('make')
MAKEFILE = Path(__file__).resolve().parents[3] / 'Makefile.pre.in'


@unittest.skipUnless(os.name == 'posix' and MAKE, 'requires POSIX and make')
@unittest.skipUnless(MAKEFILE.is_file(), 'requires the source Makefile')
class TestBolt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([MAKE, '--version'], capture_output=True, text=True)
        if 'GNU Make' not in result.stdout:
            raise unittest.SkipTest('requires GNU Make')

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.env = os.environ.copy()
        for name in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES',
                     'BOLT_INSTRUMENT_FLAGS', 'BOLT_APPLY_FLAGS',
                     'PROFILE_TASK', 'RUNSHARED'):
            self.env.pop(name, None)

    def configure(self, *, shared=False):
        source = MAKEFILE.read_text(encoding='utf-8')

        def section(start, end):
            offset = source.index(start)
            return source[offset:source.index(end, offset)]

        rules = section('# List of binaries that BOLT runs on.',
                        '# Compile and run with gcov')
        rules += section('# Build the interpreter\n', '\nplatform:')
        rules += section('libpython$(LDVERSION).so:', '\nlibpython3.so:')
        python = shlex.quote(sys.executable)
        binaries = '$(BUILDPYTHON)'
        if shared:
            binaries += ' $(INSTSONAME)'
        for name, value in {
            '@BOLT_BINARIES@': binaries,
            '@BOLT_INSTRUMENT_FLAGS@': '',
            '@BOLT_APPLY_FLAGS@': '',
            '@LLVM_BOLT@': f'{python} bolt.py',
            '@MERGE_FDATA@': f'{python} merge.py',
            '@PREBOLT_RULE@': 'build-inputs',
        }.items():
            rules = rules.replace(name, value)
        dependencies = '$(LDLIBRARY)' if shared else ''
        (self.directory / 'Makefile').write_text(
            'SHELL=/bin/sh -e\n'
            '.DEFAULT_GOAL := bolt-opt\n'
            'BUILDPYTHON=python\n'
            'LDVERSION=3.16\n'
            'LDLIBRARY=libpython3.16.so\n'
            'INSTSONAME=libpython3.16.so.1.0\n'
            'MACHDEP=linux\n'
            'LN=ln\n'
            'LIBRARY_OBJS=library.o\n'
            f'LINK_PYTHON_DEPS={dependencies}\n'
            f'LINKCC={python} link.py\n'
            f'BLDSHARED={python} link.py\n'
            '.PHONY: build-inputs\n'
            'build-inputs: $(BUILDPYTHON)\n\n' + rules,
            encoding='utf-8',
        )
        (self.directory / 'link.py').write_text('''\
import pathlib
import shutil
import sys

args = sys.argv[1:]
output = pathlib.Path(args[args.index('-o') + 1])
source = next(arg for arg in args if arg.endswith('.o'))
shutil.copyfile(source, output)
output.chmod(0o755)
''', encoding='utf-8')
        (self.directory / 'bolt.py').write_text('''\
import json
import pathlib
import sys

args = sys.argv[1:]
stage = 'instrument' if '-instrument' in args else 'apply'
source = pathlib.Path(args[0]).read_text()
if '# BOLT ' in source:
    sys.exit('BOLT requires a pristine input')
with pathlib.Path('invocations.jsonl').open('a') as log:
    log.write(json.dumps({'stage': stage, 'args': args}) + '\\n')
failure = pathlib.Path('fail-' + stage)
if failure.exists():
    failure.unlink()
    sys.exit('injected optimizer failure')
output = pathlib.Path(args[args.index('-o') + 1])
output.write_text(source + '\\n# BOLT ' + stage + '\\n')
output.chmod(0o755)
''', encoding='utf-8')
        (self.directory / 'merge.py').write_text(
            "print('profile data')\n", encoding='utf-8')
        (self.directory / 'Programs').mkdir()
        self.write_input('Programs/python.o', 'interpreter-1')
        self.write_input('library.o', 'library-1')

    def write_input(self, name, version):
        (self.directory / name).write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding='utf-8')

    def make(self, *args, fail=False):
        result = subprocess.run(
            [MAKE, '--no-print-directory', '-j2', *args],
            cwd=self.directory, env=self.env, capture_output=True, text=True,
        )
        output = result.stdout + result.stderr
        if fail:
            self.assertNotEqual(result.returncode, 0, output)
        else:
            self.assertEqual(result.returncode, 0, output)
        return output

    def invocations(self):
        return [json.loads(line) for line in
                (self.directory / 'invocations.jsonl').read_text().splitlines()]

    def age_build(self):
        # Make timestamp ordering explicit, including on coarse filesystems.
        now = time.time()
        for names, offset in (
            (('Programs/python.o', 'library.o'), 4),
            (('libpython3.16.so', 'libpython3.16.so.1.0'), 3),
            (('python', 'profile-bolt-config-stamp'), 2),
            (('profile-bolt-stamp',), 1),
        ):
            for name in names:
                path = self.directory / name
                if path.exists():
                    os.utime(path, (now - offset, now - offset))

    def assert_version(self, name, version, *, optimized=True):
        content = (self.directory / name).read_text()
        self.assertIn(f"'{version}'", content)
        if optimized:
            self.assertIn('# BOLT apply', content)
        else:
            self.assertNotIn('# BOLT ', content)

    def test_unchanged_build(self):
        self.configure(shared=True)
        self.make()
        self.age_build()
        before = self.invocations()
        self.make()
        self.assertEqual(self.invocations(), before)

    def test_relinked_interpreter(self):
        self.configure()
        self.make()
        self.age_build()
        self.write_input('Programs/python.o', 'interpreter-2')
        self.make()
        self.assert_version('python', 'interpreter-2')

    def test_relinked_shared_library(self):
        self.configure(shared=True)
        self.make()
        self.age_build()
        self.write_input('library.o', 'library-2')
        self.make()
        self.assert_version('python', 'interpreter-1')
        self.assert_version('libpython3.16.so.1.0', 'library-2')

    def test_shared_library_dependency(self):
        self.configure(shared=True)
        self.make()
        self.age_build()
        self.write_input('library.o', 'library-2')
        self.make('libpython3.16.so')
        self.make('profile-bolt-stamp', 'LINK_PYTHON_DEPS=')
        self.assert_version('libpython3.16.so.1.0', 'library-2')

    def test_changed_configuration(self):
        self.configure()
        self.make()
        for variable, value in (
            ('BOLT_INSTRUMENT_FLAGS', '-instrument-option'),
            ('BOLT_APPLY_FLAGS', '-apply-option'),
            ('PROFILE_TASK', "-c 'print(42)'"),
        ):
            with self.subTest(variable=variable):
                self.age_build()
                before = len(self.invocations())
                self.make(f'{variable}={value}')
                self.assertEqual(len(self.invocations()), before + 2)
                self.assert_version('python', 'interpreter-1')
                self.make(f'{variable}={value}')
                self.assertEqual(len(self.invocations()), before + 2)

    def test_repeated_profiling(self):
        self.configure(shared=True)
        self.make()
        (self.directory / 'profile-bolt-stamp').unlink()
        self.make()
        self.assertEqual(len(self.invocations()), 8)
        self.assert_version('python', 'interpreter-1')
        self.assert_version('libpython3.16.so.1.0', 'library-1')

    def test_retry_after_failure(self):
        self.configure(shared=True)
        self.make()
        for stage in ('instrument', 'apply'):
            with self.subTest(stage=stage):
                self.age_build()
                (self.directory / f'fail-{stage}').touch()
                flags = f'BOLT_APPLY_FLAGS=-retry-{stage}'
                self.make(flags, fail=True)
                self.assertFalse((self.directory / 'profile-bolt-stamp').exists())
                self.make(flags)
                self.assert_version('python', 'interpreter-1')
                self.assert_version('libpython3.16.so.1.0', 'library-1')

    def test_clean_bolt(self):
        self.configure(shared=True)
        self.make()
        self.make('clean-bolt')
        self.assert_version('python', 'interpreter-1', optimized=False)
        self.assert_version('libpython3.16.so.1.0', 'library-1', optimized=False)
        self.assertFalse((self.directory / 'profile-bolt-stamp').exists())
        self.make()
        self.assert_version('python', 'interpreter-1')
        self.assert_version('libpython3.16.so.1.0', 'library-1')

    def test_clean_bolt_does_not_restore_removed_binaries(self):
        self.configure(shared=True)
        self.make()
        for name in ('python', 'libpython3.16.so.1.0'):
            (self.directory / name).unlink()
        self.make('clean-bolt')
        for name in ('python', 'libpython3.16.so.1.0'):
            self.assertFalse((self.directory / name).exists())


if __name__ == '__main__':
    unittest.main()
