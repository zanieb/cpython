"""Fetch native dependency archives and verify their checked-in SHA-256 hashes."""

# Shared with multissltests.py, which supports older interpreters.
# ruff: noqa: UP032

import hashlib
import json
import logging
import os
import re
import tempfile

try:
    from urllib.request import urlretrieve
except ImportError:
    from urllib import urlretrieve


log = logging.getLogger(__name__)
MANIFEST = os.path.join(os.path.dirname(__file__), 'dependency-hashes.json')


def get_dependency(url, manifest=None):
    with open(manifest or os.environ.get('CPYTHON_DEPENDENCY_MANIFEST')
              or MANIFEST) as source:
        dependencies = json.load(source)
    try:
        dependency = dependencies[url]
    except KeyError:
        raise ValueError('No checked-in hash for {}'.format(url))
    if not re.match(r'\A[0-9a-f]{64}\Z', dependency['sha256']):
        raise ValueError('Invalid SHA-256 hash for {}'.format(url))
    return dependency


def verify(filename, expected):
    digest = hashlib.sha256()
    with open(filename, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(
            'SHA-256 mismatch for {}: expected {}, got {}'.format(
                filename, expected, actual))


def download(url, filename, retrieve=None, manifest=None, mirrors=()):
    """Verify cached archives; publish new downloads only after verification.

    Mirrors must serve the same bytes as the primary URL. A hash mismatch is
    fatal, while a transport failure may fall back to the next mirror.
    """
    filename = os.path.abspath(filename)
    dependency = get_dependency(url, manifest)
    expected = dependency['sha256']
    if os.path.isfile(filename):
        verify(filename, expected)
        return filename

    directory = os.path.dirname(filename)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    descriptor, temporary = tempfile.mkstemp(
        prefix=os.path.basename(filename) + '.', suffix='.part', dir=directory)
    os.close(descriptor)
    try:
        retrieve = retrieve or urlretrieve
        urls = [dependency.get('url', url)] + list(mirrors)
        for index, candidate in enumerate(urls):
            try:
                retrieve(candidate, temporary)
            except OSError:
                if index == len(urls) - 1:
                    raise
                log.warning('Download failed: %s', candidate)
            else:
                verify(temporary, expected)
                os.rename(temporary, filename)
                return filename
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
