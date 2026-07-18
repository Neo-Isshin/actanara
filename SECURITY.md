# Security Policy

## Supported versions

Security fixes are provided for the current stable Actanara release line.

| Version | Supported |
| --- | --- |
| 1.0.1 | Yes |
| 1.0.0 | No — withdrawn |
| Earlier private development versions | No public support |

## Reporting a vulnerability

Please use
[GitHub private vulnerability reporting](https://github.com/Neo-Isshin/actanara/security/advisories/new)
for security-sensitive reports. Do not include credentials, private Runtime
data, access tokens, personal paths, or exploit details in a public issue.

For non-sensitive security hardening questions, use the
[public issue tracker](https://github.com/Neo-Isshin/actanara/issues).

## Security boundary

Actanara is local-first. Its Dashboard and nova-RAG services default to
loopback interfaces, external-agent RAG operations are read-only, and provider
secrets are stored beneath the user Runtime with private permissions. The
installer and updater fail closed when source identity or release integrity
cannot be proven.

Operators are responsible for securing their local account, selected model and
embedding providers, backups, and any deliberately configured remote access.
