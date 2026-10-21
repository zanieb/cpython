# Maintaining CI dependencies

CI installs committed locks for the environments covered by this check.
Dependency updates should change the source requirements and their generated lock
together, without refreshing unrelated environments.

Regenerate Python locks locally using `make -C Doc lock` or the command recorded
in the generated file. Review the complete version and artifact-hash diff.

After regenerating a lock, run
`python3 Tools/build/check_ci_dependencies.py --record PATH_TO_LOCK` from the
repository root, using a repository-relative lock path, and commit the updated
input record. It associates each lock with its source requirements
and constraints, so editing either side requires an input-record update. The record
cannot prove that a lock was regenerated or that its transitive dependencies are
complete. The check also compares plain `name==version` pins, verifies that `pylock`
artifacts have SHA-256 metadata in the expected format, and compares the bootstrap
`pip` version and hash with the bundled wheel. Run the consuming jobs to check
compatibility.
