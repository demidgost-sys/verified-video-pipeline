## Invariant

What failure mode or contract does this change address?

## Evidence

- [ ] Normal-path test added or updated
- [ ] Fail-closed-path test added or updated
- [ ] `ruff check src tests scripts` passes
- [ ] `ruff format --check src tests scripts` passes
- [ ] `python -m unittest discover -s tests -v` passes
- [ ] `python -m compileall -q src tests scripts` passes
- [ ] Relevant documentation is updated

## Public-data gate

- [ ] No real media, transcript, account, credential, publication record, or local path
- [ ] Any new asset is recorded in `ASSET_PROVENANCE.md`
- [ ] Any new dependency is justified and license-reviewed
