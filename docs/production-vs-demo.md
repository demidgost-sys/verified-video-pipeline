# Public demo versus production

## What is real here

- the file hashing and mutation detection;
- the state machine and gate ordering;
- the self-hashed compare-and-swap journal;
- the build receipt and no-clobber promotion;
- the project and machine-wide fail-fast leases;
- actual FFmpeg render, FFprobe inspection, and full decode;
- the tests and CI that exercise those contracts.

## What is synthetic

- all audiovisual content;
- the human reviewer label used by `vvp demo`;
- the edit decision and encode profile;
- all project identifiers and release evidence.

## What remains private or absent

This repository does not contain or connect to a production channel, media
archive, cloud drive, Telegram workflow, OAuth client, YouTube uploader, OBS
profile, transcript engine, thumbnail system, editorial research, real release
card, schedule, analytics, or customer data.

The code is not imported by any private production pipeline in version 0.1. A
passing synthetic demo is evidence of the public contracts only; it is not a
claim that a real recording can be autonomously edited and published.

## Adapter gate

A generic production adapter is considered only after the same contract is
validated by two independent real workflows. Until then, copying code back and
forth would create coupling without proving reuse. Even after that gate, live
publishing and human content review remain separate boundaries.
