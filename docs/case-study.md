# Case study: extracting the reliability story

## Context

Two private, isolated local video workflows accumulated useful safeguards as
their real release processes encountered interruptions, stale artifacts,
concurrent heavy tasks, and ambiguous publication state. Their business data,
media, accounts, and implementation history were not suitable for a public
portfolio repository.

## Decision

Instead of anonymizing a large production repository, this project started from
an empty Git history. Only behavior-level lessons were reimplemented:

- bind decisions and artifacts to exact bytes;
- separate human approval from automated candidate generation;
- serialize machine-heavy operations without a hidden queue;
- treat multi-file promotion as a recoverable transaction;
- keep status inspection read-only;
- make non-goals and external side effects explicit.

The maintainer attests that no source file, test fixture, release record,
absolute path, identifier, asset, or commit was copied from the private systems.

## Result

Version 0.1 is a compact standard-library Python package with an actual FFmpeg
acceptance path. A reviewer can understand the lifecycle, reproduce the happy
path in about a minute, and inspect failure behavior without access to private
media or third-party accounts.

The value is engineering judgment rather than feature count: a smaller public
contract is easier to audit, safer to share, and more credible than describing
an unverified zero-touch editor or uploader.

## Trade-offs

- The reference implementation gives up arbitrary FFmpeg flexibility to make
  the approved plan strict and auditable.
- It uses a local JSON journal instead of a database because the public scope is
  one POSIX workspace, not a distributed service.
- It favors roll-forward recovery. An intact prepared transition is completed,
  while ambiguous state always stops for review.
- It deliberately excludes production adapters until two independent real
  consumers prove that the abstraction is stable.
