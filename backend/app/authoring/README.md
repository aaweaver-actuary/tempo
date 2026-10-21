# Saved-source authoring applications

**Current unmerged source; deployment unverified.** This folder groups saved-source
application decisions. It is distinct from daily Study practice and from
[authored/imported Studies](../studies/README.md) chapters and exercises.

[Discovery acceptance](discovery/README.md) contains the existing prepared-choice
acceptance application and its narrow facts/transaction capabilities. No other
Discovery recommendation, worker, provider, database or queue implementation has
moved here in this slice. This placement does not consolidate the other existing
legacy Discovery admission path.

Domain implementation imports its own internals, explicit shared contracts and
pure Chess libraries. Outer composition supplies concrete implementations through
those contracts. A sibling domain's public entry point is still an implementation
import and is not a permitted shortcut. Database implementation remains in the
single repository-root database tree; physical colocation does not permit
cross-owner table access. Ordinary Study has no dependency on Discovery or Games.
