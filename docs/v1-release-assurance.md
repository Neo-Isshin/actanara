# Open Nova v1.0.1 Release Assurance

## Scope

The v1.0.1 assurance review covers public package metadata, source curation,
installer and updater safety, Runtime payload cleanliness, Foundation,
Dashboard, Scheduler, Nova-Task, nova-RAG, migration compatibility, and
release-page behavior.

It also covers the corrective controls added after v1.0.0 post-publication E2E
found that managed services could remain bound to an older concrete source
directory after an otherwise successful update.

## Test matrix overview

- package version, license, metadata, source manifests, and distribution files;
- bootstrap and update source-selection failure matrices, including an explicit
  `WITHDRAWN` Release title;
- stale multi-generation LaunchAgent normalization, stable source/venv binding,
  exact plist rollback, and loaded-source commit provenance;
- fresh install, full upgrade, rollback, service handoff, and data retention;
- Foundation, Pipeline, Settings, Scheduler, Dashboard, Nova-Task, and nova-RAG;
- release-clean true-positive and false-positive fixtures;
- Python, shell, JavaScript, Markdown-link, privacy, and secret scans;
- desktop and mobile Release Page behavior;
- two independent frozen-source full-suite runs.

## Final automated result

The frozen v1.0.1 public source was exercised in two independent full-suite
runs, each with a fresh virtual environment and disposable Runtime. Each run
completed:

- **1,414 run**
- **0 failures**
- **0 errors**
- **2 expected skips**

The two expected skips are the explicitly opt-in real-launchd Scheduler
handoff tests. Their non-live contracts remain covered by the isolated suite;
release E2E uses unique temporary service labels and a disposable Runtime.
Slow pathological timeout guards and the real 85k-chunk nova-RAG candidate
profile were enabled and passed rather than skipped. The frozen source file-set
hash remained unchanged across both runs.

## Critical and blocker disposition

The immutable v1.0.0 release is withdrawn and is not recommended for install or
update. Its tag and artifacts remain unchanged for audit. v1.0.1 adds
transactionally durable service-definition rebinding, stable paths for new
managed definitions, and Dashboard/nova-RAG health provenance tied to the
candidate's exact full source commit. All identified Critical and Blocker
findings are closed; none remain open for this release.

## Known non-gate limitations

- The guided one-liner and managed service workflow are macOS-first.
- Linux and Windows are not first-class one-liner targets in v1.0.x.
- Model and embedding quality, availability, quotas, and billing remain
  properties of the providers selected by the operator.
- The real Dashboard Browser gate is destructive by design and therefore runs
  only against a disposable seeded Runtime.

## Conclusion

Status: **Release Ready**.
