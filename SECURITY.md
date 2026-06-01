# Security Policy

## Supported Versions

ASO-Light is pre-release. Fixes are applied to the latest `main` branch.

| Version | Supported |
|---------|-----------|
| `main`  | ✅ |
| older tags | ❌ |

## Reporting a Concern

If you find a security issue, please report it privately rather than opening a public issue.

- Preferred: open a [GitHub Security Advisory](https://github.com/overx-ai/aso-light/security/advisories/new)
- Or email the maintainers at the address listed on the GitHub organization profile

Please include:
- A description of the issue and its impact
- Steps to reproduce
- Affected version or commit

## What to Expect

- We aim to acknowledge reports within 3 business days.
- We'll keep you updated on progress toward a fix.
- We'll credit you in the release notes once a fix ships, unless you prefer to stay anonymous.
- Please give us a reasonable window to release a fix before any public disclosure.

## Handling Credentials

ASO-Light stores sensitive data — Apple `.p8` keys and API credentials. A few notes for operators:

- `.p8` keys are encrypted at rest with Fernet. Set a strong `FERNET_KEY` and back it up; losing it makes stored credentials unrecoverable.
- Never commit `.env` files or `.p8` keys. The `.gitignore` already excludes them.
- Run the backend behind TLS in any networked deployment.
- Rotate `SECRET_KEY`, `JWT_SECRET_KEY`, and `FERNET_KEY` if you suspect they have been exposed.
