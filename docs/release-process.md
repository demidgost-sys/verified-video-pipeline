# Release process

Future releases are built from an exact annotated Git tag and published by the
tag-only GitHub Actions workflow. Three jobs separate building untrusted tagged
source, signing already verified bytes, and creating the GitHub Release. The
workflow also re-verifies the publicly observable tag-ruleset shape and exact
live remote tag immediately before publication.

## Trust boundary

The build job has only:

- `contents: read`

It installs the release/test Python packages from `requirements/release.txt`
with `pip install --require-hashes`, then installs this project with
`--no-deps --no-build-isolation`. It checks the complete public tree and
reachable history, runs the full test and style suite, builds the source archive
and wheel with build isolation disabled, and verifies the wheel in a new virtual
environment with the synthetic FFmpeg demo. It uploads exactly one immutable
Actions artifact identified by the upload action's artifact ID. It cannot mint
an OIDC token, write an attestation, or create a release.

After the wheel is installed, both `vvp --version` and the imported
`verified_video_pipeline.__version__` must equal the version derived from the
tag. The local-wheel install uses `--no-index --no-deps`; a filename or
metadata-only match is insufficient.

The attest job has only:

- `contents: read`
- `id-token: write`
- `attestations: write`
- `artifact-metadata: write`

It installs no build or test dependency and performs no packaging. It checks out
the exact tagged source, downloads the same artifact ID, and runs the
standard-library release verifier. That verifier recomputes the artifact names,
sizes, and SHA-256 digests from the files, requires the canonical evidence to
match those values, validates `SHA256SUMS`, and only then invokes
`actions/attest` for all four files.

The publish job has only:

- `contents: write`
- `attestations: read`

It downloads the same immutable artifact ID, verifies `SHA256SUMS` and canonical
evidence again, and verifies every artifact attestation against this repository,
`.github/workflows/release.yml`, the exact tag ref, and the exact source commit.
In its final step it re-reads the publicly observable tag-ruleset shape and the
live remote annotated-tag object, refuses an existing release, and only then calls
`gh release create --verify-tag`. A workflow rerun, missing API access, policy
drift, a lightweight or moved tag, a tag outside `origin/main`, or a version
mismatch stops the workflow.

## Locked build environment

The release dependency file is specific to GitHub-hosted Ubuntu x86_64 and
CPython 3.11. Every listed Python distribution has an accepted SHA-256 hash;
unlisted transitive packages and isolated build-environment downloads are
forbidden. `setuptools` is the exact build backend declared in both the lock
file and `pyproject.toml`.

This lock covers the Python release/test package set. It does not claim that the
moving `ubuntu-latest` image or the FFmpeg package installed by the runner is
byte-reproducible. The workflow records their behavior through the test and
synthetic-demo gates, and it does not claim reproducible wheel bytes without an
independent rebuild comparison.

## GitHub controls and trust boundary

Two administrator-configured controls provide defense in depth:

- exactly one active repository ruleset named `Immutable release tags`,
  targeting only `refs/tags/v*.*.*`, with exactly the `update` and `deletion`
  restrictions and no bypass actor;
- repository Immutable Releases with `enabled: true`.

The least-privilege `GITHUB_TOKEN` cannot read the administrator-only Immutable
Releases setting or hidden bypass information. The maintainer therefore verifies
those two properties outside the workflow; they are explicit trust assumptions,
not claims made by release evidence. No long-lived administrative token is stored
for CI.

The publish job does verify the publicly observable ruleset name, target,
enforcement, include pattern, and exact rule types through GitHub REST API
`2026-03-10`. The same final shell step then verifies the exact live tag object
and creates the release, minimizing the policy-to-write window. The cryptographic
fail-closed boundary remains verification of every artifact attestation against
the exact workflow/ref/commit, followed by the exact live-tag check and refusal
to replace an existing release.

## Maintainer procedure

1. Change both `project.version` in `pyproject.toml` and `__version__` in
   `src/verified_video_pipeline/__init__.py` to the same stable SemVer version.
2. Update `requirements/release.txt` deliberately if the Ubuntu/Python 3.11
   release package set changes, including every required distribution hash.
3. Merge that reviewed commit into `main` and make sure `origin/main` contains
   it.
4. Confirm the required ruleset and Immutable Releases setting are active.
5. Create an annotated tag whose name is exactly `vMAJOR.MINOR.PATCH`, for
   example `git tag -a v0.2.0 -m "Release v0.2.0"`.
6. Push that tag once. Do not move, delete and recreate, or reuse a release tag.
7. Review the workflow run and the resulting release. A failed or rerun release
   uses a new version and annotated tag rather than overwriting published
   evidence.

There is no manual-dispatch release path. The workflow also refuses a second
attempt of the same Actions run and refuses to update a GitHub Release that
already exists.

## Release assets

Every signed release contains:

- `verified-video-pipeline-VERSION.tar.gz` — clean `git archive` source at the
  tagged commit;
- `verified_video_pipeline-VERSION-py3-none-any.whl` — CLI wheel with no
  unconditional runtime dependencies, tested in a clean virtual environment;
- `release-evidence.json` — canonical JSON binding the annotated tag object,
  ref, commit, build checks, asset names, sizes, and SHA-256 digests;
- `SHA256SUMS` — strict checksums for the source archive, wheel, and evidence
  file.

All four files receive GitHub Artifact Attestations. Attestations establish
which workflow and source revision produced exact bytes; they do not prove that
the source is secure or suitable for a particular use.

## Consumer verification

Start from a fresh clone, download a future release into its own directory, and
first verify the checksums:

```bash
git clone https://github.com/demidgost-sys/verified-video-pipeline.git
cd verified-video-pipeline
git fetch --tags origin
gh release download v0.2.0 --dir verify-v0.2.0
(cd verify-v0.2.0 && sha256sum --check --strict SHA256SUMS)
```

On macOS, `(cd verify-v0.2.0 && shasum -a 256 -c SHA256SUMS)` performs the
checksum check. Then obtain the three expected source identities from the
evidence and prove that the local annotated tag resolves to those exact objects:

```bash
tag=v0.2.0
ref="refs/tags/${tag}"
evidence=verify-v0.2.0/release-evidence.json
test "$(jq -r '.source.ref' "${evidence}")" = "${ref}"
test "$(git cat-file -t "${ref}")" = "tag"
test "$(git rev-parse --verify "${ref}")" = \
  "$(jq -r '.source.tag_object' "${evidence}")"
test "$(git rev-parse --verify "${ref}^{commit}")" = \
  "$(jq -r '.source.commit' "${evidence}")"
```

Only after all four commands succeed, verify each downloaded file with the
exact attestation policy:

```bash
commit="$(jq -r '.source.commit' "${evidence}")"
gh attestation verify \
  verify-v0.2.0/verified_video_pipeline-0.2.0-py3-none-any.whl \
  --repo demidgost-sys/verified-video-pipeline \
  --signer-workflow demidgost-sys/verified-video-pipeline/.github/workflows/release.yml \
  --signer-digest "${commit}" \
  --source-ref refs/tags/v0.2.0 \
  --source-digest "${commit}" \
  --digest-alg sha256 \
  --deny-self-hosted-runners
```

Repeat the attestation command for the source archive,
`release-evidence.json`, and `SHA256SUMS`.

Successful verification proves that exact bytes were signed by the named
workflow for the named source identities. It does not prove that the source is
benign, correct, or appropriate for a particular use; review the tagged source
and workflow policy separately.

## Historical `v0.1.0`

`v0.1.0` predates this release workflow. It is an intentionally unsigned
historical source release: it has no GitHub Artifact Attestation and must not be
retagged, rebuilt, or described as signed. The signed-release contract applies
only to future versions created after this workflow enters `main`.
