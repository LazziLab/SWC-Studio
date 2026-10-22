# Cutting a release

This walks through the full sequence to ship a SWC-Studio release that
supports the modular update mechanism. The actual publishing is
automated by `.github/workflows/release.yml`.

## Quick recipe

```bash
# 1. Bump the version locally (one source of truth → updater, pyproject, CITATION.cff)
python scripts/stamp_version.py --version 0.2.0
#    Then set date-released in CITATION.cff and move the Unreleased
#    entries in CHANGELOG.md under the new version.

# 2. Verify the bump took effect
python scripts/stamp_version.py --version 0.2.0 --check

# 3. Commit + push
git add pyproject.toml swcstudio/core/updater.py CITATION.cff CHANGELOG.md
git commit -m "Bump version to 0.2.0"
git push origin <your-branch>

# 4. Tag and push the tag — this triggers the release workflow
git tag v0.2.0
git push origin v0.2.0
```

GitHub Actions will then:

1. Build and inspect the layer zips, pip wheel, and `update_manifest.json`
2. Install the wheel into empty environments on Windows, macOS, and
   Linux for Python 3.10, 3.11, and 3.12
3. Deserialize each auto-labeler model file and run GUI and auto-labeling smoke tests
4. Build the modular macOS `.app` and modular Windows `.zip`
5. Create a **draft** GitHub Release `v0.2.0` and attach all assets
6. (Gated) Publish the wheel to PyPI

### Final manual step: publish the draft

The release lands as a draft visible only to repo maintainers, so you
can review the auto-generated notes and double-check the assets
before end users can download anything.

1. Go to https://github.com/LazziLab/SWC-Studio/releases
2. Find the draft release at the top (it has a "Draft" tag)
3. Click into it, edit notes if you want, verify all 7 assets are present:
   - `swcstudio-code-v0.2.0.zip`
   - `swcstudio-models-v0.2.0.zip`
   - `update_manifest.json`
   - `SWC-Studio-v0.2.0-macOS.zip`
   - `SWC-Studio-v0.2.0-Windows.zip`
   - `swcstudio-0.2.0-py3-none-any.whl`
   - `swcstudio-0.2.0.tar.gz`
4. Click **Publish release**

Only after this click do existing v0.1.0 users see "Update available"
in the in-app dialog, since the updater fetches
`releases/latest/download/update_manifest.json` which does not resolve
to drafts.

If you want fully-automatic publishing (no manual click), remove
`draft: true` from all three `softprops/action-gh-release@v2` blocks
in `.github/workflows/release.yml`.

## What ships in a release

For a tag `v0.2.0`, the workflow attaches these assets to the release:

| Asset                                  | Audience                          | Size  |
|----------------------------------------|-----------------------------------|-------|
| `swcstudio-code-v0.2.0.zip`            | Modular desktop users (code update; no models) | ~0.5 MB |
| `swcstudio-models-v0.2.0.zip`          | Modular desktop model updates (the `labeler-<version>/` folder) | ~25 MB (about 50 MB of model files) |
| `SWC-Studio-v0.2.0-macOS.zip`          | New / runtime-bumping Mac users   | varies by runtime |
| `SWC-Studio-v0.2.0-Windows.zip`        | New / runtime-bumping Win users   | varies by runtime |
| `swcstudio-0.2.0-py3-none-any.whl`     | pip users; includes the auto-labeler models | ~26 MB |
| `swcstudio-0.2.0.tar.gz`               | pip users (sdist fallback); includes the models | ~26 MB |
| `update_manifest.json`                 | the in-app updater                | ~1 KB |

The `update_manifest.json` is the key file that ties everything
together. The bundled updater fetches it from the
`releases/latest/download/update_manifest.json` URL (always points at
the most recent release), then offers to install whichever layer
changed. The workflow generates it through
`scripts/generate_release_manifest.py`; do not duplicate the schema in
workflow YAML.

## First-time setup

### PyPI Trusted Publisher (one-time)

Trusted publishing avoids the need for a PYPI_API_TOKEN secret.

1. Visit https://pypi.org/manage/account/publishing/
2. Add a "GitHub" publisher with:
   - PyPI Project Name:  `swcstudio`
   - Owner:              `LazziLab`
   - Repository name:    `SWC-Studio`
   - Workflow filename:  `release.yml`
   - Environment name:   `pypi`
3. In your GitHub repo, go to Settings -> Environments -> New environment
   - Name: `pypi`
   - Required reviewers: optional but recommended (adds a manual approval gate)

After this, every tag push produces a wheel and (after approval, if
configured) it appears on PyPI.

### Disable PyPI publish

If you'd rather not publish to PyPI yet, comment out the
`publish-pypi` job in `release.yml`. Layer zips and bundles will still
be attached to the GitHub Release.

## Dry runs

Use `workflow_dispatch` to test without tagging:

1. Go to Actions -> Release -> Run workflow
2. Set `version` to a valid PEP 440 pre-release, e.g. `0.3.0rc1` (`0.2.0-test` is rejected by the build)
3. Set `dry_run` to `true`

The workflow builds all the artifacts but skips upload to GitHub
Releases and PyPI. The artifacts are downloadable from the workflow
run page for inspection.

## Troubleshooting

**Workflow says "version mismatch"**
The `pyproject.toml` `version` and the tag don't agree. Run:
```bash
python scripts/stamp_version.py --version <X.Y.Z>
git commit -am "Bump version to X.Y.Z"
git tag vX.Y.Z
```

**macOS build fails with "developer cannot be verified"**
The released `.app` is unsigned. Users will see the Gatekeeper warning on first launch. Solutions in order of effort:

* Tell users: System Settings -> Privacy & Security -> Open Anyway the first time (macOS 15 dropped right-click -> Open); see docs/GETTING_STARTED.md#first-launch-on-macos
* Or in their Terminal: `xattr -cr /Applications/SWC-Studio.app`
* Long-term fix: Apple Developer ID + notarization (~$99/yr)

**PyPI publish fails with "trusted publisher not configured"**
Complete the one-time setup above, then re-run the failed `publish-pypi` job.

**The "Check for Updates" menu shows "Could not reach the update server"**
The release was tagged but the workflow didn't attach `update_manifest.json` (perhaps the workflow failed on that step). Re-run the workflow or manually upload the manifest from the workflow's `layer-artifacts` artifact.
