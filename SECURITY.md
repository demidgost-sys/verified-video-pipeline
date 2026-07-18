# Security policy

## Supported versions

Until 1.0, only the latest tagged minor release receives security fixes.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** private security-advisory flow
for this repository. Do not post credentials, private media, filesystem paths,
or exploit details in a public issue.

Include:

- affected version and operating system;
- the smallest synthetic reproducer;
- expected and actual fail-closed behavior;
- whether artifact integrity, path isolation, locking, recovery, or evidence is
  affected.

You should receive an acknowledgement within seven days. A fix timeline depends
on severity and reproducibility; no public disclosure date is promised before
the report is understood and mitigated.

## Secret and data policy

The project requires no account or network credential. Pull requests containing
real footage, transcripts, OAuth files, tokens, cookies, channel identifiers,
publication records, local absolute paths, or runtime project state will be
closed and the Git history cleaned before any release. If a real secret is ever
committed, it must be rotated even if the commit is later removed.

The repository gate scans current candidates, every path and blob in every
reachable commit tree, commit messages, annotated-tag messages, and Git
identities for credential-shaped filenames, common token signatures, private
emails, account identifiers, user-home paths, binary content, and runtime
evidence. GitHub-generated noreply identities are the only public-email
exception. It is a preventive review layer, not a substitute for revocation
when a real secret has existed in Git.

Future release assets are bound to the tagged source by GitHub Artifact
Attestations. This proves workflow identity and artifact digests; it does not
make reviewed source trustworthy by itself. Verify both the attestation policy
and the checked-out source before relying on an artifact.

The release workflow keeps build, attestation, and publication permissions in
separate jobs. Publication additionally requires a live no-bypass immutable-tag
ruleset and repository Immutable Releases as administrator-verified defense in
depth. The least-privilege workflow rechecks only the public ruleset shape; it
does not store an administrative secret or claim to prove hidden bypass state.

See the explicit [threat model](docs/threat-model.md) for trust assumptions and
non-goals.
