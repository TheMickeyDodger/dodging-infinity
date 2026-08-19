# Security Policy

## Supported versions

Security fixes are currently provided for the latest Dodging Infinity release line.

| Version | Supported |
| --- | --- |
| 0.3.x | Yes |
| < 0.3 | No |

## Reporting a vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Use GitHub private vulnerability reporting for this repository when available. Include enough information to reproduce and understand the impact of the issue, including affected components, relevant configuration, and a minimal proof of concept when appropriate.

Please avoid including secrets, credentials, private repository contents, or unrelated user data in a report.

## Security-sensitive areas

Changes involving the following areas deserve additional scrutiny:

- repository and worktree isolation
- Git commit and push authorization guards
- runtime permissions and command execution
- cross-repository child-Herdr orchestration
- policy and rule enforcement
- dependency and completion gating
- prompt delivery and runtime settlement
- handling of local configuration, tokens, and credentials

Security fixes should include regression coverage whenever practical.
