import importlib
import json
import os
import os.path
import sys
import sysconfig
import string
import unittest
from pathlib import Path
from unittest import mock

from test.support import is_android, is_apple_mobile, is_wasm32

BASE_PATH = Path(
    __file__,  # Lib/test/test_build_details.py
    '..',  # Lib/test
    '..',  # Lib
    '..',  # <src/install dir>
).resolve()
MODULE_PATH = BASE_PATH / 'Tools' / 'build' / 'generate-build-details.py'

try:
    # Import "generate-build-details.py" as "generate_build_details"
    spec = importlib.util.spec_from_file_location(
        "generate_build_details", MODULE_PATH
    )
    generate_build_details = importlib.util.module_from_spec(spec)
    sys.modules["generate_build_details"] = generate_build_details
    spec.loader.exec_module(generate_build_details)
except (FileNotFoundError, ImportError):
    generate_build_details = None


class FormatTestsBase:
    @property
    def contents(self):
        """Install details file contents. Should be overridden by subclasses."""
        raise NotImplementedError

    @property
    def data(self):
        """Parsed install details file data, as a Python object."""
        return json.loads(self.contents)

    def key(self, name):
        """Helper to fetch subsection entries.

        It takes the entry name, allowing the usage of a dot to separate the
        different subsection names (eg. specifying 'a.b.c' as the key will
        return the value of self.data['a']['b']['c']).
        """
        value = self.data
        for part in name.split('.'):
            value = value[part]
        return value

    def test_top_level_container(self):
        self.assertIsInstance(self.data, dict)
        for key, value in self.data.items():
            with self.subTest(key=key):
                if key in ('schema_version', 'base_prefix', 'base_interpreter',
                           'platform'):
                    self.assertIsInstance(value, str)
                elif key in ('language', 'implementation', 'abi', 'suffixes',
                             'libpython', 'c_api', 'arbitrary_data'):
                    self.assertIsInstance(value, dict)

    def test_base_prefix(self):
        self.assertIsInstance(self.key('base_prefix'), str)

    def test_base_interpreter(self):
        """Test the base_interpreter entry.

        The generic test wants the key to be missing. If your implementation
        provides a value for it, you should override this test.
        """
        with self.assertRaises(KeyError):
            self.key('base_interpreter')

    def test_platform(self):
        self.assertEqual(self.key('platform'), sysconfig.get_platform())

    def test_language_version(self):
        allowed_characters = string.digits + string.ascii_letters + '.'
        value = self.key('language.version')

        self.assertLessEqual(set(value), set(allowed_characters))
        self.assertTrue(sys.version.startswith(value))

    def test_language_version_info(self):
        value = self.key('language.version_info')

        self.assertEqual(len(value), sys.version_info.n_fields)
        for part_name, part_value in value.items():
            with self.subTest(part=part_name):
                sys_version_value = getattr(sys.version_info, part_name)
                self.assertEqual(part_value, sys_version_value)

    def test_implementation(self):
        impl_ver = sys.implementation.version
        for key, value in self.key('implementation').items():
            with self.subTest(part=key):
                if key == 'version':
                    self.assertEqual(len(value), len(impl_ver))
                    for part_name, part_value in value.items():
                        self.assertFalse(isinstance(sys.implementation.version, dict))
                        getattr(sys.implementation.version, part_name)
                        sys_implementation_value = getattr(impl_ver, part_name)
                        self.assertEqual(sys_implementation_value, part_value)
                else:
                    self.assertEqual(getattr(sys.implementation, key), value)


needs_installed_python = unittest.skipIf(
    sysconfig.is_python_build(),
    'This test can only run in an installed Python',
)


@unittest.skipIf(os.name != 'posix', 'Feature only implemented on POSIX right now')
@unittest.skipIf(is_wasm32, 'Feature not available on WebAssembly builds')
class CPythonBuildDetailsTests(unittest.TestCase, FormatTestsBase):
    """Test CPython's install details file implementation."""

    @property
    def location(self):
        if sysconfig.is_python_build():
            projectdir = sysconfig.get_config_var('projectbase')
            pybuilddir = os.path.join(projectdir, 'pybuilddir.txt')
            with open(pybuilddir, encoding='utf-8') as f:
                dirname = os.path.join(projectdir, f.read())
        else:
            dirname = sysconfig.get_path('stdlib')
        return os.path.join(dirname, 'build-details.json')

    @property
    def contents(self):
        with open(self.location, 'r', encoding='utf-8') as f:
            return f.read()

    def test_abi_flags(self):
        self.assertEqual(self.key('abi.flags'), list(sys.abiflags))

    def test_extension_suffixes(self):
        self.assertEqual(self.key('suffixes.extensions'),
                         importlib.machinery.EXTENSION_SUFFIXES)

    @needs_installed_python
    def test_location(self):
        self.assertTrue(os.path.isfile(self.location))

    # Override generic format tests with tests for our specific implemenation.

    @needs_installed_python
    @unittest.skipIf(
        is_android or is_apple_mobile,
        'Android and iOS run tests via a custom testbed method that changes sys.executable'
    )
    def test_base_interpreter(self):
        value = self.key('base_interpreter')

        # Skip check if installation is relocated
        if sysconfig._installation_is_relocated():
            self.skipTest("Installation is relocated")

        self.assertEqual(os.path.realpath(value), os.path.realpath(sys.executable))

    @needs_installed_python
    @unittest.skipIf(
        is_android or is_apple_mobile,
        "Android and iOS run tests via a custom testbed method that doesn't ship headers"
    )
    def test_c_api(self):
        value = self.key('c_api')

        # Skip check if installation is relocated
        if sysconfig._installation_is_relocated():
            self.skipTest("Installation is relocated")

        self.assertTrue(os.path.exists(os.path.join(value['headers'], 'Python.h')))
        version = sysconfig.get_config_var('VERSION')
        self.assertTrue(os.path.exists(os.path.join(value['pkgconfig_path'], f'python-{version}.pc')))

    @needs_installed_python
    @unittest.skipIf(
        is_android or is_apple_mobile,
        "Android and iOS run tests via a custom testbed method that doesn't ship libpython"
    )
    def test_libpython_static(self):
        value = self.data.get('libpython', {})
        if 'static' not in value:
            self.skipTest("No static libpython")

        if sysconfig._installation_is_relocated():
            self.skipTest("Installation is relocated")

        # The path must name an installed static library (an 'ar' archive).
        with open(value['static'], 'rb') as f:
            self.assertEqual(f.read(8), b'!<arch>\n')


@unittest.skipIf(generate_build_details is None,
                 "Failed to import generate-build-details")
@unittest.skipIf(os.name != 'posix', 'POSIX target configuration')
class BuildDetailsTargetTests(unittest.TestCase):
    def generate_target(self, **config_vars):
        get_config_var = sysconfig.get_config_var

        def target_config_var(name):
            if name in config_vars:
                return config_vars[name]
            return get_config_var(name)

        with mock.patch.object(sysconfig, 'get_config_var', target_config_var):
            return generate_build_details.generate_data('1.0')

    def test_native_extension_suffixes(self):
        self.assertEqual(generate_build_details.get_extension_suffixes(),
                         importlib.machinery.EXTENSION_SUFFIXES)

    def test_cross_abi_and_platform(self):
        data = self.generate_target(
            ABIFLAGS='td', DYNLOADFILE='dynload_shlib.o', MACHDEP='linux',
            MULTIARCH='arm-linux-gnueabihf',
            EXT_SUFFIX='.cpython-316td-arm-linux-gnueabihf.so',
            ALT_SOABI='cpython-316t-arm-linux-gnueabihf',
            SOABI_PLATFORM='arm-linux-gnueabihf', Py_GIL_DISABLED=1)
        self.assertEqual(data['abi']['flags'], ['t', 'd'])
        self.assertEqual(data['abi']['extension_suffix'],
                         '.cpython-316td-arm-linux-gnueabihf.so')
        self.assertEqual(data['abi']['stable_abi_suffix'],
                         '.abi3t-arm-linux-gnueabihf.so')
        self.assertEqual(data['suffixes']['extensions'], [
            '.cpython-316td-arm-linux-gnueabihf.so',
            '.cpython-316t-arm-linux-gnueabihf.so',
            '.abi3t-arm-linux-gnueabihf.so', '.abi3t.so', '.so',
        ])
        self.assertEqual(data['implementation']['_multiarch'],
                         'arm-linux-gnueabihf')

    def test_cross_macos(self):
        data = self.generate_target(
            ABIFLAGS='', DYNLOADFILE='dynload_shlib.o', MACHDEP='darwin',
            EXT_SUFFIX='.cpython-316-darwin.so', ALT_SOABI=0,
            SOABI_PLATFORM='darwin', Py_GIL_DISABLED=0)
        self.assertEqual(data['abi']['flags'], [])
        self.assertEqual(data['abi']['stable_abi_suffix'], '.abi3-darwin.so')
        self.assertEqual(data['suffixes']['extensions'], [
            '.cpython-316-darwin.so', '.abi3-darwin.so', '.abi3.so',
            '.abi3t-darwin.so', '.abi3t.so', '.so',
        ])

    def test_untagged_platform(self):
        data = self.generate_target(
            DYNLOADFILE='dynload_shlib.o', MACHDEP='linux',
            EXT_SUFFIX='.cpython-316.so', ALT_SOABI=0,
            SOABI_PLATFORM=0, Py_GIL_DISABLED=0)
        self.assertEqual(data['suffixes']['extensions'], [
            '.cpython-316.so', '.abi3.so', '.abi3t.so', '.so',
        ])

    def test_cygwin(self):
        data = self.generate_target(
            DYNLOADFILE='dynload_shlib.o', MACHDEP='cygwin',
            EXT_SUFFIX='.cpython-316-x86_64-cygwin.dll')
        self.assertEqual(data['suffixes']['extensions'], [
            '.cpython-316-x86_64-cygwin.dll', '.dll',
        ])
        self.assertNotIn('stable_abi_suffix', data['abi'])

    def test_hpux(self):
        data = self.generate_target(
            DYNLOADFILE='dynload_hpux.o', MACHDEP='hp-ux',
            EXT_SUFFIX='.cpython-316.sl')
        self.assertEqual(data['suffixes']['extensions'], [
            '.cpython-316.sl', '.sl',
        ])
        self.assertNotIn('stable_abi_suffix', data['abi'])

    def test_no_dynamic_loading(self):
        for platform in ('wasi', 'emscripten'):
            with self.subTest(platform=platform):
                data = self.generate_target(
                    DYNLOADFILE='dynload_stub.o', MACHDEP=platform)
                self.assertEqual(data['suffixes']['extensions'], [])
                self.assertNotIn('extension_suffix', data['abi'])
                self.assertNotIn('stable_abi_suffix', data['abi'])
                self.assertFalse(
                    data['implementation']['supports_isolated_interpreters'])


@unittest.skipIf(
    generate_build_details is None,
    "Failed to import generate-build-details"
)
@unittest.skipIf(os.name != 'posix', 'Feature only implemented on POSIX right now')
@unittest.skipIf(is_wasm32, 'Feature not available on WebAssembly builds')
class BuildDetailsRelativePathsTests(unittest.TestCase):
    @property
    def build_details_absolute_paths(self):
        data = generate_build_details.generate_data(schema_version='1.0')
        return json.loads(json.dumps(data))

    @property
    def build_details_relative_paths(self):
        data = self.build_details_absolute_paths
        generate_build_details.make_paths_relative(data, config_path=None)
        return data

    def test_round_trip(self):
        data_abs_path = self.build_details_absolute_paths
        data_rel_path = self.build_details_relative_paths

        self.assertEqual(data_abs_path['base_prefix'],
                         data_rel_path['base_prefix'])

        base_prefix = data_abs_path['base_prefix']

        top_level_keys = ('base_interpreter',)
        for key in top_level_keys:
            self.assertEqual(key in data_abs_path, key in data_rel_path)
            if key not in data_abs_path:
                continue

            abs_rel_path = os.path.join(base_prefix, data_rel_path[key])
            abs_rel_path = os.path.normpath(abs_rel_path)
            self.assertEqual(data_abs_path[key], abs_rel_path)

        second_level_keys = (
            ('libpython', 'dynamic'),
            ('libpython', 'dynamic_stableabi'),
            ('libpython', 'static'),
            ('c_api', 'headers'),
            ('c_api', 'pkgconfig_path'),

        )
        for part, key in second_level_keys:
            self.assertEqual(part in data_abs_path, part in data_rel_path)
            if part not in data_abs_path:
                continue
            self.assertEqual(key in data_abs_path[part],
                             key in data_rel_path[part])
            if key not in data_abs_path[part]:
                continue

            abs_rel_path = os.path.join(base_prefix, data_rel_path[part][key])
            abs_rel_path = os.path.normpath(abs_rel_path)
            self.assertEqual(data_abs_path[part][key], abs_rel_path)


@unittest.skipIf(
    generate_build_details is None,
    "Failed to import generate-build-details"
)
@unittest.skipIf(os.name != 'posix', 'Feature only implemented on POSIX')
@unittest.skipIf(is_wasm32, 'Feature not available on WebAssembly builds')
class BuildDetailsLibpythonTests(unittest.TestCase):
    LIBDIR = '/prefix/lib'
    LIBPL = '/prefix/lib/python3.X/config-3.X'

    def generate_libpython(self, **config_vars):
        """Generate the libpython section with overridden config vars."""
        config_vars = dict(LIBDIR=self.LIBDIR, LIBPL=self.LIBPL,
                           LIBRARY='libpython3.X.a', **config_vars)
        get_config_var = sysconfig.get_config_var
        def patched_get_config_var(name):
            if name in config_vars:
                return config_vars[name]
            return get_config_var(name)
        with mock.patch('sysconfig.get_config_var', patched_get_config_var):
            data = generate_build_details.generate_data(schema_version='1.0')
        return json.loads(json.dumps(data)).get('libpython', {})

    def test_static_only(self):
        libpython = self.generate_libpython(
            LDLIBRARY='libpython3.X.a', STATIC_LIBPYTHON=1,
            PYTHONFRAMEWORK='')
        self.assertNotIn('dynamic', libpython)
        self.assertEqual(libpython['static'],
                         os.path.join(self.LIBDIR, 'libpython3.X.a'))

    def test_shared_and_static(self):
        # The static library is only installed into LIBPL, not LIBDIR.
        libpython = self.generate_libpython(
            LDLIBRARY='libpython3.X.so', STATIC_LIBPYTHON=1,
            PYTHONFRAMEWORK='')
        self.assertEqual(libpython['dynamic'],
                         os.path.join(self.LIBDIR, 'libpython3.X.so'))
        self.assertEqual(libpython['static'],
                         os.path.join(self.LIBPL, 'libpython3.X.a'))

    def test_shared_without_static(self):
        libpython = self.generate_libpython(
            LDLIBRARY='libpython3.X.so', STATIC_LIBPYTHON=0,
            PYTHONFRAMEWORK='')
        self.assertNotIn('static', libpython)

    def test_framework(self):
        # Framework builds don't install the static library.  LIBPL contains
        # a libpython3.X.a symlink to the framework's shared library instead.
        libpython = self.generate_libpython(
            LDLIBRARY='Python.framework/Versions/3.X/Python',
            STATIC_LIBPYTHON=1, PYTHONFRAMEWORK='Python')
        self.assertNotIn('static', libpython)


if __name__ == '__main__':
    unittest.main()
