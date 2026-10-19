# Releasing

The maintainer publishes each release by hand. The
[release workflow](../.github/workflows/release.yml) builds the distribution files,
checks them and the release metadata, and publishes them to PyPI with trusted
publishing when a GitHub release is published. A run started by hand builds and checks
the files and uploads nothing.

## Before the first release

1. Make the repository public. The release workflow and PyPI's page link to it.
2. On PyPI, open "Publishing" in the account sidebar and add a pending publisher for
   GitHub Actions: the project `opencited`, the repository `danielnorlan/opencited`,
   the workflow `release.yml` and the environment `pypi`. PyPI creates the project at
   the first upload ([PyPI's guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)).
3. In the repository settings, create the environment `pypi`, limit its deployments
   to tags that match `v*`, and add yourself as a required reviewer, so that a
   published release waits for your approval before anything reaches PyPI.

## Each release

1. In a pull request, set the version in `pyproject.toml` and in both `version` fields
   of `server.json`, move the entries under "Unreleased" in `CHANGELOG.md` to a section
   for the version, update the links at the end of that file for the new tag, and
   merge it. `scripts/check_release.py`, which `make check` and CI run, refuses
   versions that disagree. For the first release, the same pull request removes the
   README's notes that the package is not on PyPI, since PyPI shows the README as the
   package's page and keeps it as it was uploaded.
2. Run the release workflow by hand on `main` under Actions, and check that it
   passes. It validates `server.json` against the MCP Registry's schema, builds the
   files, checks them with `twine check`, lists what an upload would send and starts
   the server from the wheel. It uploads nothing and uses no credential, so it cannot
   show whether the pending publisher is set up right.
3. Publish a GitHub release for a new tag `v` and the version, such as `v0.1.0`, on
   `main`. The workflow refuses a tag that does not match the version, a README that
   still says the package is not on PyPI and a commit that is not on `main`. It
   checks the files again, waits for the `pypi` environment's approval, builds the
   files again, compares their SHA-256 with the checked ones and uploads them.
4. List the server in the MCP Registry after the upload, since the registry checks
   the `mcp-name` line in the README that PyPI shows. Install `mcp-publisher` as the
   [registry's quickstart](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/quickstart.mdx)
   describes, then run `mcp-publisher login github` and `mcp-publisher publish` in the
   repository root.
