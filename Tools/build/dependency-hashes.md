# Native dependency archives

`dependency-hashes.json` records the SHA-256 digest of native archives covered
by the Windows, Android, Apple, and multi-SSL download scripts. Keys identify the
URL requested by a build script. An optional `url` selects an immutable download
location, such as a commit archive instead of a branch archive.

Update the version in the consuming script and its manifest entry together.
Use the publisher's archive checksum or GitHub release asset digest when
available. For generated GitHub archives, resolve the dependency branch to a
commit and calculate the digest of the archive at that commit. Review both the
source revision and the digest before accepting an update.

Downloads are written to a temporary file and verified before being placed in
the archive cache. Cached archives are verified before extraction. A mismatch
fails the build. Remove a mismatching cached archive before retrying; failed new
downloads are removed automatically. Investigate the source and recorded digest
if a new download also fails verification. These checks do not authenticate an already extracted directory or a compiled-library cache.
The Windows Git fallback does not use archive downloads.

For custom repositories or additional SSL versions, supply a complete manifest
with `CPYTHON_DEPENDENCY_MANIFEST`, or pass `--dependency-manifest` to
`get_external.py` or `multissltests.py`. Missing entries fail before downloading.
