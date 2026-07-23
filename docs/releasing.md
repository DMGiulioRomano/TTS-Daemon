# Releasing

A release is cut by pushing a `vX.Y.Z` **git tag**. That one tag triggers two
workflows in parallel:

| Workflow | What it publishes |
| --- | --- |
| `.github/workflows/release.yml` | the package to **PyPI** (OIDC trusted publishing) + a **GitHub Release** with notes from `CHANGELOG.md` |
| `.github/workflows/docker-publish.yml` | the multi-arch image (`amd64` + `arm64`) to **`ghcr.io/dmgiulioromano/tts-daemon`** |

Neither uses an external secret: PyPI uses OIDC, GHCR uses the built-in
`GITHUB_TOKEN`.

## One-time setup

1. **PyPI trusted publisher.** On both <https://pypi.org> and
   <https://test.pypi.org>, add a trusted publisher for the `tts-daemon`
   project pointing at this repo, workflow `release.yml`, and the matching
   environment name (`pypi` / `testpypi`). No API token is stored.
2. **GHCR package.** The first tag creates the package under the account as
   *private*; open its package settings once to make it public and link it to
   this repository. Later tags need nothing.

## Cutting a release

1. Bump the version in **both** `pyproject.toml` and
   `src/tts_daemon/__init__.py` (they must match — `scripts/check_version.py`
   enforces it, and also that they match the tag).
2. In `CHANGELOG.md`, turn `## [Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]`, and update the link definitions at the
   bottom. `scripts/release_notes.py` extracts this section for the GitHub
   Release, so it must be non-empty.
3. Commit, then tag and push:

   ```sh
   git commit -am "Release X.Y.Z"
   git tag vX.Y.Z
   git push origin vX.Y.Z      # this is what publishes
   ```

   The version must be higher than the last one on PyPI — PyPI rejects a
   re-upload of an existing version.

## Dry runs (no publishing)

Validate the pipelines before tagging, from the Actions tab:

- **Release** → *Run workflow* builds and publishes to **TestPyPI** only.
- **Publish Docker image** → *Run workflow* builds both arches but pushes
  nothing (it skips the GHCR login).
