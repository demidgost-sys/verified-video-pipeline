# Roadmap

The long-term goal is to build a public, clean-room release-assurance reference
that demonstrates exact-byte provenance, human-gated approvals, invalidation,
atomic recovery, and project isolation using synthetic fixtures—while all
production accounts, content, code, and history remain private.

## v0.1 — public proof

- [x] Fresh Git history and synthetic-only provenance.
- [x] Strict edit-plan and project lifecycle contracts.
- [x] SHA-256 binding and concurrent-mutation detection.
- [x] Atomic JSON and crash-recoverable state journal.
- [x] Fail-fast project and machine-wide media leases.
- [x] No-clobber master promotion and build receipt recovery.
- [x] FFmpeg/FFprobe synthetic E2E and mutation tests.
- [x] CI, security, contribution, licensing, and architecture documentation.

## v0.2 — strengthen the contract

- [ ] Record and detect relevant FFmpeg engine-contract drift.
- [ ] Add randomized transition and recovery-model tests without production
  fixtures.
- [ ] Add measured loudness checks behind a versioned profile.
- [ ] Publish signed release artifacts and a reproducible release checklist.
- [ ] Evaluate Windows abstractions without weakening POSIX guarantees.

## v0.3 — adapters after evidence

Entry gate: two independent real workflows must validate a stable generic
contract. The production repositories remain private and do not depend on this
package during the experiment.

- [ ] Design a generic OBS intake manifest using only synthetic fixtures here.
- [ ] Model resumable delivery against a fake transport, not a live account.
- [ ] Define compatibility tests before any private consumer imports the
  package.
- [ ] Publish only adapters whose privacy, license, and failure boundaries are
  independently reviewed.

## v1.0 — stable reference

- [ ] Freeze and document schema compatibility rules.
- [ ] Complete an external clean-room reproduction from the tagged release.
- [ ] Publish an anonymized outcome review with no private operational data.

Live upload, editorial judgment, and autonomous public release are not implied
by any milestone.
