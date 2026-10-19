# Threat model

## Assets

- exact source, plan, master, and manifest byte identity;
- the order and evidence of human approval and technical QA;
- unambiguous recovery after an interrupted local operation;
- isolation of runtime media and private production systems from public Git.

## In scope

- accidental or concurrent file mutation;
- stale state and skipped lifecycle gates;
- process interruption between multi-file updates;
- a second cooperating process attempting the same project or heavy stage;
- symlink and path-traversal input at the artifact boundary;
- a lease override that aliases an unrelated local file;
- accidental overwrite of an unrelated master;
- accidental inclusion of generated media, runtime state, or credential-shaped
  files in the current tree or reachable Git history;
- accidental disclosure through reachable commit or annotated-tag messages;
- substitution of a future release asset after it was built from a tagged
  source revision.

## Trust assumptions

- the local user, Python interpreter, FFmpeg binaries, kernel, and filesystem
  are trusted;
- advisory-lock users cooperate with the same lease contract;
- SHA-256 collision resistance is adequate for artifact identity;
- the project directory is on a POSIX filesystem that supports advisory locks,
  hard links, atomic rename, and directory `fsync`.

## Out of scope

- a privileged attacker who can replace both an artifact and all evidence;
- remote storage authenticity, cloud durability, OAuth, or publisher identity;
- sandboxing untrusted media or FFmpeg itself;
- content, copyright, accessibility, mathematical, or editorial correctness;
- distributed consensus across hosts or filesystems;
- absolute protection from sudden power loss on arbitrary storage hardware;
- a provenance attestation proving that the reviewed source itself is benign.

## Controls

| Risk | Control |
|---|---|
| Stale bytes | Full SHA-256 at every downstream gate. |
| Mutation during hash | File identity checked before and after the read. |
| Partial JSON | Same-directory temp file, file `fsync`, rename, directory `fsync`. |
| Partial state transition | Self-hashed before/after write-ahead journal. |
| Master overwrite | Hard-link creation fails if the target exists. |
| Hidden queue | Non-blocking leases return `LeaseBusy`. |
| Lease path alias | Exclusive creation or an exact versioned ownership marker; unowned existing bytes are never modified. |
| Arbitrary encode injection | Named profile and strict edit-plan keys; no raw args. |
| Diagnostic path disclosure | Public subprocess errors expose only the tool and exit status. |
| Private-data import | Fresh history, synthetic-only fixtures, current-tree, every reachable historical path/blob and Git-metadata denylist audit, ignored runtime evidence. |
| Release asset substitution | SHA-256 evidence plus GitHub-hosted Sigstore build-provenance attestations bound to the exact future tag workflow. |
| Stale engine or changed sampled codec behavior | Required numeric/runtime versions, versioned profiles and a deterministic H.264/AAC canary; drift blocks reuse and recovery promotion. |
| Technically decodable but unusable loudness | Finite, profile-bound input measurements of the encoded master; byte and engine rechecks around QA. |
| Packaging drift or unrelated green CI | Offline clean-source wheel comparison and latest successful CI/CodeQL runs for the exact main commit. |

The engine canary samples behavior; it is not a complete codec attestation or
an exhaustive inventory of dynamically linked code. Informational build IDs
are opaque hashes, never raw configuration paths. Unknown engine formats fail
closed. Loudness is a technical synthetic gate and does not establish human
listening quality. Native Windows support has not been proved; POSIX primitives
remain unchanged. Details and compatibility rules are in the
[v0.2 contract](v0.2-contract.md).

Security issues should be reported through the private process in
[`SECURITY.md`](../SECURITY.md), not a public issue.
