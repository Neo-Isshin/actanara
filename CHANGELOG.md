# Changelog

All notable public changes to Open Nova are documented here.

## [1.0.0] - 2026-07-13

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

[1.0.0]: https://github.com/Neo-Isshin/open-nova/releases/tag/v1.0.0
