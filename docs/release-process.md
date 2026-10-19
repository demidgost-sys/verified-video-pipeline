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
After those verifications it materializes only the regular, uniquely named
release-check helper from the attested source archive into a fresh temporary
file. The write-enabled publish job performs no repository checkout. The helper
checks exact-main-commit CI/CodeQL and writes the release notes.
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
independent rebuild comparison. v0.2 now performs that comparison: after the
candidate wheel is built, `prepare_release.py reproduce` extracts the exact
canonical tagged archive in a fresh directory, builds offline with the same
locked backend and SOURCE_DATE_EPOCH, and requires identical wheel SHA-256.
This proves reproduction within that toolchain, not across arbitrary runners.

Before publication, the latest push CI and CodeQL runs for the exact main
commit must both have completed successfully. The release notes record their
specific run links, the release run, artifact digests, schema/profile versions
and known limits. A successful run on another commit or an older green run
followed by failure cannot satisfy that gate.

## Clean-clone rebuild checklist

The following commands rebuild published v0.2.1 from its existing annotated tag
in an isolated Linux clone. They produce local unsigned artifacts and do not
push or publish anything. For a new version, first merge the reviewed source
commit and follow the maintainer procedure below with an unused tag.

```bash
git clone https://github.com/demidgost-sys/verified-video-pipeline.git vvp-release-check
cd vvp-release-check
git fetch --tags origin
git switch --detach refs/tags/v0.2.1
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements/release.txt
python -m pip install --no-deps --no-build-isolation -e .
python scripts/audit_public_tree.py
python -m compileall -q src tests scripts
ruff check src tests scripts
ruff format --check src tests scripts
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)
python -m unittest discover -s tests -v
VVP_MODEL_PROFILE=deep python -m unittest discover -s tests -p test_model.py -v
commit=$(git rev-parse HEAD)
tag_object=$(git rev-parse refs/tags/v0.2.1)
python scripts/prepare_release.py check --tag v0.2.1 \
  --source-ref refs/tags/v0.2.1 --expected-sha "$commit" \
  --main-ref refs/remotes/origin/main --event-name push --run-attempt 1
python scripts/prepare_release.py archive --tag v0.2.1 --output-dir dist
python -m pip wheel . --no-index --no-deps --no-build-isolation --wheel-dir dist
python scripts/prepare_release.py reproduce --tag v0.2.1 --release-dir dist
python scripts/prepare_release.py finalize --tag v0.2.1 \
  --source-ref refs/tags/v0.2.1 --source-commit "$commit" \
  --tag-object "$tag_object" --release-dir dist
python scripts/prepare_release.py verify --tag v0.2.1 \
  --source-ref refs/tags/v0.2.1 --source-commit "$commit" \
  --tag-object "$tag_object" --release-dir dist
(cd dist && sha256sum --check --strict SHA256SUMS)
python3.11 -m venv /tmp/vvp-clean-wheel
/tmp/vvp-clean-wheel/bin/python -m pip install --no-index --no-deps dist/*.whl
/tmp/vvp-clean-wheel/bin/vvp --version
/tmp/vvp-clean-wheel/bin/vvp doctor
/tmp/vvp-clean-wheel/bin/vvp demo /tmp/vvp-clean-demo
/tmp/vvp-clean-wheel/bin/vvp status /tmp/vvp-clean-demo --verify
```

Use fresh unused `/tmp` paths for each rehearsal. The hash lock is for the
Linux/Python 3.11 release environment; macOS PR tests use the development extra.
Check the exact 0.2.1 CLI/import version and READY report. Preserve rehearsal
logs and hashes as local evidence, explicitly labelled unsigned. Inspect
`requirements/release.txt` and the exact source before running it. The real
release remains the tag-only build/attest/publish workflow and consumer
verification below, including external CI/CodeQL and the GitHub controls.

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
5. Set `tag` to an unused `vMAJOR.MINOR.PATCH` name matching both reviewed
   package version declarations, then run `git tag -a "$tag" -m "Release $tag"`.
   Published tags, including `v0.2.1`, must not be recreated.
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

Start from a fresh clone, download the published release into its own directory, and
first verify the checksums:

```bash
git clone https://github.com/demidgost-sys/verified-video-pipeline.git
cd verified-video-pipeline
git fetch --tags origin
gh release download v0.2.1 --dir verify-v0.2.1
(cd verify-v0.2.1 && sha256sum --check --strict SHA256SUMS)
```

On macOS, `(cd verify-v0.2.1 && shasum -a 256 -c SHA256SUMS)` performs the
checksum check. Then obtain the three expected source identities from the
evidence and prove that the local annotated tag resolves to those exact objects:

```bash
tag=v0.2.1
ref="refs/tags/${tag}"
evidence=verify-v0.2.1/release-evidence.json
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
  verify-v0.2.1/verified_video_pipeline-0.2.1-py3-none-any.whl \
  --repo demidgost-sys/verified-video-pipeline \
  --signer-workflow demidgost-sys/verified-video-pipeline/.github/workflows/release.yml \
  --signer-digest "${commit}" \
  --source-ref refs/tags/v0.2.1 \
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
to v0.2.1 and later releases produced by this workflow.

## Failed v0.2.0 attempt

v0.2.0 permanently identifies commit bcfb286fea0652dc5ff91b0135d179ac56990ca2.
Its first tag-only run stopped in the build job because two synthetic tests
inherited the tagged project SOURCE_DATE_EPOCH. No attestation or release was
created. The tag remains unchanged; release reruns stay forbidden. The fix in
v0.2.1 isolates fixture epochs and runs PR CI and rehearsals with the same
release environment.

## Published v0.2.1

Following owner confirmation, [v0.2.1](https://github.com/demidgost-sys/verified-video-pipeline/releases/tag/v0.2.1)
was published from commit `a2d6d81e076ac8c750bd9ae3e0a11dfd58283cdb`.
Its annotated tag object is `2ee6b6c8f35ecb0c2e0e84b0b67e1fca16ee2cd8`.
The [first tag-only run](https://github.com/demidgost-sys/verified-video-pipeline/actions/runs/39333427387)
completed the build, attestation, and publication jobs successfully.

All four assets were downloaded from the immutable GitHub Release and passed
external attestation verification against that source commit, tag ref, and
release workflow with self-hosted runners denied. A fresh Linux clone verified
the canonical source archive, wheel, evidence, and checksums.
[Issue #5](https://github.com/demidgost-sys/verified-video-pipeline/issues/5)
was closed after these checks passed.
