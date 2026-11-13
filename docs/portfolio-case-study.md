# Verified Video Pipeline: keeping approval tied to exact bytes

A video file can keep the same name after its content changes. A successful
check of the previous file then says nothing about the replacement. This
Python reference implementation makes that relationship explicit: source,
approved edit plan, rendered master, QA evidence, and release manifest are
bound through SHA-256 identities.

Active development was [frozen on 2026-11-13](../PROJECT_STATUS.md). The project
is retained as an engineering case with a published, attested release.

## Ownership and tools

Demid Valiullin set the project boundary and acceptance expectations, retained
the human-approval requirement, and explicitly authorized publication of the
exact version and source commit. Codex substantially assisted with the
implementation, tests, documentation, and verification work. The portfolio
claim is project ownership and evaluation of an AI-assisted implementation.

## What the implementation demonstrates

The lifecycle runs from source registration through plan approval, rendering,
technical QA, and manifest creation. A changed upstream file invalidates the
previous evidence. Status inspection remains read-only.

A self-hashed write-ahead journal records state transitions. A separate build
receipt supports recovery when rendering and state updates are interrupted.
Master promotion uses a same-filesystem hard link that refuses replacement of
an existing file. Project and machine-wide leases reject conflicting heavy
operations instead of adding work to an invisible queue.

The media adapter uses real FFmpeg and FFprobe operations: a single trim,
H.264/AAC encoding, full decoding, and measured loudness on the encoded output.
An engine fingerprint includes a synthetic codec canary so that relevant
engine changes block reuse of old evidence.

## The failed release exposed a missing test condition

The first v0.2.0 tag run
[failed before packaging](https://github.com/demidgost-sys/verified-video-pipeline/actions/runs/39331449543).
Two tests created temporary Git repositories but inherited the release job's
`SOURCE_DATE_EPOCH`. Their fixture timestamps differed from the release commit,
so the production timestamp guard correctly rejected them. The earlier local
rehearsal had set the epoch after the tests and missed this combination.

[PR #18](https://github.com/demidgost-sys/verified-video-pipeline/pull/18)
isolated the fixture environment and made CI set the epoch before the full
suite. The production guard stayed strict. The failed tag was preserved, and
the corrected source received a separately authorized v0.2.1 release.

## Evidence for the result

[v0.2.1](https://github.com/demidgost-sys/verified-video-pipeline/releases/tag/v0.2.1)
was published from `a2d6d81e076ac8c750bd9ae3e0a11dfd58283cdb`.
Its [tag-only run](https://github.com/demidgost-sys/verified-video-pipeline/actions/runs/39333427387)
passed 133 tests in the GitHub-hosted Ubuntu/Python 3.11 release environment.
The wheel was installed in a clean environment and rebuilt offline from the
canonical source archive with identical bytes under the locked toolchain.

Build, attestation, and publication use separate jobs. After publication, all
four downloaded assets passed external GitHub attestation verification bound
to the exact source commit, tag ref, signer workflow, and GitHub-hosted
runners: the source archive, wheel, `release-evidence.json`, and `SHA256SUMS`.
[Issue #5](https://github.com/demidgost-sys/verified-video-pipeline/issues/5)
was closed after that verification. The
[release process](release-process.md) records the reproduction and consumer
verification commands.

## Why development stopped

The engineering contracts have synthetic acceptance evidence. A real OBS
recording, the owner's complete editing workflow, and any time savings have
not been validated. The current adapter accepts one continuous trim, uses
only the first audio stream, and limits each media command to 120 seconds of
processing. Native Windows is unsupported; Linux/macOS POSIX filesystems are
the supported boundary, with WSL used as a Linux environment.

For ordinary mathematical-video preparation, the owner chose to stop active
development after reviewing simpler recording, editing, and FFmpeg options.
The useful portfolio result is a reviewable reliability design, a documented
release failure and fix, and reproducible evidence. Production adoption and
editing-time savings remain unproven.

## Short portfolio description

AI-assisted Python reliability reference for human-approved video artifacts,
with exact-byte QA, crash-recoverable state, and reproducible releases verified
through GitHub Artifact Attestations. Owner responsibility covered scope,
acceptance expectations, and release decisions.
