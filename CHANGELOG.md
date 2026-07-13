# Changelog

All notable public changes to Open Nova are documented here.

## [1.0.1] - 2026-07-13

### Fixed

- Rebind managed Dashboard, watchdog, nova-RAG, and Scheduler LaunchAgents from
  stale concrete release/venv directories to stable Runtime pointers during an
  update, including installations whose plist is more than one release behind.
- Publish the source commit actually loaded by Dashboard and nova-RAG health
  endpoints and require it to match the promoted candidate before commit.
- Generate new managed-service definitions from stable Runtime source and venv
  paths so future updates do not retain a prior release directory.
- Refuse managed-service writes when an explicitly selected Runtime cannot be
  validated, instead of falling back to a different default Runtime.

### Security and release integrity

- Fail closed when GitHub's latest published Release is explicitly marked
  `WITHDRAWN`, even if GitHub still reports it as non-draft and non-prerelease.
- Preserve exact pre-update plist bytes for transactional rollback and verify
  canonical service bindings again before promotion, restore, verification,
  and commit.
- Add a locked, minimal-environment release builder that proves the complete
  source file set remains unchanged and emits deterministic manifests,
  Runtime/package artifacts, provenance, and checksums.

## [1.0.0] - 2026-07-13 (WITHDRAWN)

v1.0.0 is retained as immutable public audit history but must not be installed
or recommended. Post-publication E2E found that a successful update could leave
managed background services executing an older concrete source directory.

### Added

- Initial public source release of the Open Nova local AI operations runtime.
- Guided macOS installation, local Runtime management, guarded updates, and
  operational diagnostics.
- Foundation, Dashboard, Nova-Task, and optional nova-RAG product surfaces.
- English and Chinese user documentation, a static release page, and
  contributor-facing automated regression suites.

### Security and release integrity

- Default installs and updates resolve the latest stable GitHub Release to an
  exact full commit before source checkout.
- Draft, prerelease, missing, malformed, rate-limited, abbreviated, symbolic,
  and non-commit source selections fail closed.
- The hosted one-liner is versioned at `v1.0.0` and is fresh-install-only.
- Runtime secrets remain in the Runtime-local private secret store and are
  excluded from source and release artifacts.

[1.0.1]: https://github.com/Neo-Isshin/open-nova/releases/tag/v1.0.1
[1.0.0]: https://github.com/Neo-Isshin/open-nova/releases/tag/v1.0.0
