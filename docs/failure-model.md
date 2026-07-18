# Failure and recovery model

## Expected outcomes

| Failure | Result | Automatic action |
|---|---|---|
| Source changes after plan approval | `IntegrityError` | None; build remains blocked. |
| Approved plan changes | `IntegrityError` | None; build remains blocked. |
| Trim extends beyond registered source | `ContractError` | Approval remains blocked. |
| Render duration does not match trim | `PipelineError` | Master is not promoted or registered. |
| Second heavy operation starts | `LeaseBusy` | None; caller retries later. |
| Lease path already contains unowned bytes | `ContractError` | Existing bytes and mode remain unchanged. |
| FFmpeg exits non-zero or times out | `PipelineError` | No master is registered. |
| Process stops during render before a receipt | Untrusted staging file | Next build stops for operator review; it never assumes ownership. |
| Process stops after build receipt | Recoverable receipt | `recover` verifies and finishes no-clobber promotion. |
| Process stops after state journal | Recoverable WAL | `recover` rolls the exact recorded target forward. |
| Current state matches neither WAL side | `RecoveryRequired` | None; operator review is required. |
| Existing unrelated `master.mp4` | `RecoveryRequired` | Never overwrite it. |
| Master changes after QA | `IntegrityError` | Manifest creation remains blocked. |
| Journal or build receipt changes | `RecoveryRequired` | None; self-hash mismatch fails closed. |

## Idempotence

Evidence JSON is written only when absent or canonically identical. A completed
`build` returns the already registered master after rehashing it. Recovery may
be run repeatedly after success; with no pending record it is a no-op.

The pipeline does not interpret an arbitrary existing file as its own output.
That is why a master without a valid receipt or registered state is an explicit
recovery case rather than an overwrite opportunity.

Read-only `status` reports unregistered staging or master entries as blockers
and routes the operator to review; it never follows or repairs those entries.

## Crash checkpoints tested

The unit suite injects process failures after WAL preparation and after state
application. The integration suite injects a failure after a verified build
receipt but before artifact promotion. The suite also covers same-size source
mutation, post-QA master mutation, lock contention, journal corruption, and a
full synthetic source-to-manifest run.

These tests demonstrate application-level behavior. They do not emulate kernel
failure or certify power-loss persistence on every filesystem.
