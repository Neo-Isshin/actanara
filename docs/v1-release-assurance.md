# Open Nova v1.0.0 Release Assurance

## Scope

The v1.0.0 assurance review covers public package metadata, source curation,
installer and updater safety, Runtime payload cleanliness, Foundation,
Dashboard, Scheduler, Nova-Task, nova-RAG, migration compatibility, and
release-page behavior.

## Test matrix overview

- package version, license, metadata, source manifests, and distribution files;
- bootstrap and update source-selection failure matrices;
- fresh install, full upgrade, rollback, service handoff, and data retention;
- Foundation, Pipeline, Settings, Scheduler, Dashboard, Nova-Task, and nova-RAG;
- release-clean true-positive and false-positive fixtures;
- Python, shell, JavaScript, Markdown-link, privacy, and credential scans;
- desktop and mobile Release Page behavior;
- two independent frozen-source full-suite runs.

## Final automated result

Two independent frozen-source release-suite runs completed with the same
result:

- tests run: **1,370**;
- failures: **0**;
- errors: **0**;
- expected skips: **2**.

The two skips are the explicitly gated real-user-launchd scheduler tests. The
release runner replaces `launchctl` with a fail-closed test double and uses a
disposable venv, HOME, Runtime, location pointer, in-memory secret backend, and
fixed business clock for every run.

## Critical and blocker disposition

All identified v1 Critical and Blocker product findings are closed in the
curated public candidate. The update path fails closed unless a stable GitHub
Release tag resolves and peels to an exact full commit.

## Known non-gate limitations

- The guided one-liner and managed service workflow are macOS-first.
- Linux and Windows are not first-class one-liner targets in v1.0.0.
- Model and embedding quality, availability, quotas, and billing remain
  properties of the providers selected by the operator.
- The real Dashboard Browser gate is destructive by design and therefore runs
  only against a disposable seeded Runtime.

## Conclusion

Status: **Release Ready**.
