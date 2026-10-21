"""Recreate the documentation environment when its tracked installation inputs change."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

STAMP = '.requirements-hash'


def create_venv(
    destination,
    requirements,
    pip_requirements,
    *,
    build_requirements=None,
    uv='uv',
    pip_options='',
):
    destination = Path(destination).absolute()
    if destination.is_symlink():
        raise ValueError(f'Refusing to replace a symlink: {destination}')
    uv_command = shlex.split(uv)
    installer_version = ''
    if uv_command and shutil.which(uv_command[0]):
        result = subprocess.run(
            [*uv_command, '--version'], capture_output=True, text=True
        )
        if result.returncode == 0:
            installer_version = result.stdout
    if not installer_version:
        uv_command = []

    files = [
        Path(__file__),
        Path(requirements),
        Path(pip_requirements),
        Path(sys.executable).resolve(),
    ]
    if build_requirements:
        files.append(Path(build_requirements))
    inputs = {
        'files': [
            (
                str(path.resolve()),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in files
        ],
        'python': sys.version,
        'installer': (uv_command, installer_version),
        'pip_options': pip_options,
        'build_flags': {
            name: os.environ.get(name, '')
            for name in ('CC', 'CXX', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS')
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(inputs, sort_keys=True).encode()
    ).hexdigest()
    stamp = destination / STAMP
    python = destination / 'bin' / 'python3'
    if (
        python.is_file()
        and stamp.is_file()
        and stamp.read_text() == fingerprint
    ):
        print(f'Documentation environment is current: {destination}')
        return
    if destination.exists():
        if not (destination / 'pyvenv.cfg').is_file():
            raise ValueError(
                f'Refusing to replace a non-venv directory: {destination}'
            )
        shutil.rmtree(destination)

    try:
        if uv_command:
            subprocess.run(
                [
                    *uv_command,
                    'venv',
                    '--python',
                    sys.executable,
                    str(destination),
                ],
                check=True,
            )
            installer = [
                *uv_command,
                'pip',
                'install',
                '--python',
                str(python),
            ]
        else:
            subprocess.run(
                [sys.executable, '-m', 'venv', str(destination)], check=True
            )
            installer = [str(python), '-m', 'pip', 'install']
            subprocess.run(
                [*installer, '--require-hashes', '-r', str(pip_requirements)],
                check=True,
            )
        if build_requirements:
            subprocess.run(
                [
                    *installer,
                    '--require-hashes',
                    '--only-binary=:all:',
                    '-r',
                    str(build_requirements),
                ],
                check=True,
            )
        subprocess.run(
            [*installer, *shlex.split(pip_options), '-r', str(requirements)],
            check=True,
        )
        stamp.write_text(fingerprint)
    except BaseException:
        if (destination / 'pyvenv.cfg').is_file():
            shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--requirements', required=True)
    parser.add_argument('--pip-requirements', required=True)
    parser.add_argument('--build-requirements')
    parser.add_argument('--uv', default='uv')
    parser.add_argument('--pip-options', default='')
    args = parser.parse_args()
    create_venv(
        args.destination,
        args.requirements,
        args.pip_requirements,
        build_requirements=args.build_requirements or None,
        uv=args.uv,
        pip_options=args.pip_options,
    )


if __name__ == '__main__':
    main()
