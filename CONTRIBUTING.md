# Contributing

Small, contract-preserving changes are welcome.

## Before opening a pull request

1. Keep fixtures synthetic. Do not add real media, accounts, publication data,
   local paths, or credentials.
2. Explain the failure mode and the invariant the change protects.
3. Add a deterministic test for both the normal and fail-closed paths.
4. Update the relevant contract or threat-model document.
5. Run:

   ```bash
   python -m pip install -e ".[dev]"
   ruff check src tests scripts
   ruff format --check src tests scripts
   python -m unittest discover -s tests -v
   python -m compileall -q src tests scripts
   ```

6. Inspect the staged file list and Git author email before pushing. Enable
   GitHub's **Keep my email addresses private** setting before opening a pull
   request: the public gate scans GitHub's synthetic PR merge commit as well as
   reachable commit and annotated-tag messages. Contributors remain responsible
   for keeping private data out before it reaches Git.

## Design rules

- No arbitrary shell or FFmpeg argument pass-through.
- No silent overwrite, lifecycle skip, automatic public side effect, or hidden
  retry queue.
- Status and audit paths stay read-only.
- New runtime dependencies require a written reason and license review.
- New assets require an entry in `ASSET_PROVENANCE.md` before commit.
- A production adapter needs evidence from two independent private consumers;
  public synthetic success alone is not enough.

## Commit and review scope

Prefer one intentional behavior change per pull request. A reviewer should be
able to state the before/after invariant, reproduce the test, and verify that no
private system became a dependency.

By contributing, you agree that your contribution is licensed under
Apache-2.0 as described in `LICENSE`.
