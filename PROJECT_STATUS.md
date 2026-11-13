# Development frozen

The owner froze active development on 2026-11-13. This repository is retained
as an engineering portfolio case and a reproducible reference implementation.

The preserved software release is
[v0.2.1](https://github.com/demidgost-sys/verified-video-pipeline/releases/tag/v0.2.1),
from commit `a2d6d81e076ac8c750bd9ae3e0a11dfd58283cdb`.
Source, tests, release tooling, published artifacts, and verification records
remain available. See the [portfolio case](docs/portfolio-case-study.md).

## Operating policy

- Feature development, production adapters, and new releases are deferred.
- The v0.3 and v1.0 roadmap items are retained as historical ideas.
- Weekly model and CodeQL schedules are removed. The deep model can still be
  run manually; CI and CodeQL still validate explicit pushes and pull requests.
- Dependabot version-update PRs are paused with `open-pull-requests-limit: 0`.
  Security alerts and security-update controls are not disabled by that limit.
- Security reports use the existing private reporting channel. This frozen
  reference has no promised patch or response schedule.

Work resumes only after a new explicit owner request defining its scope. A
real video workflow would need its own acceptance evidence before any claim
of production readiness or time savings. Published tags and assets must retain
their existing identity.
