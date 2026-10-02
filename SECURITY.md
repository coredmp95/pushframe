# Security Policy — pushframe

`pushframe` is an **unofficial** community CLI for Aura Frames digital
photo frames. It is not affiliated with, endorsed by, or connected to
Aura Frames Inc. It talks to undocumented, reverse-engineered endpoints
(`api.pushd.com`, AWS S3/SQS) over TLS, plus your own Google Photos
session for the album-sync path.

## Supported versions

| Version | Supported | Channels |
|---------|-----------|----------|
| 5.1.x   | ✅        | [PyPI](https://pypi.org/project/pushframe/), [APT](https://coredmp95.github.io/pushframe/), [GitHub Releases](https://github.com/coredmp95/pushframe/releases) |
| < 5.1   | ❌        | upgrade — every release ships to all three channels at once (one tag, every channel) |

## Reporting a vulnerability

**Do NOT open a public GitHub issue for a security problem.**

Use GitHub's private vulnerability reporting:
**https://github.com/coredmp95/pushframe/security/advisories/new**
(preferred — creates a private thread and a tracked advisory), or email
**coredmp95@gmail.com** with `[pushframe security]` in the subject.

Please include: the affected version/channel, reproduction steps or a
proof of concept, and the impact you believe it enables. You will get an
acknowledgment within **7 days** and a status update at least every
**14 days** until resolution. Credit in the advisory unless you ask
otherwise — coordinated disclosure, no legal action for good-faith
research on your own account/data.

## What is (and is not) a pushframe vulnerability

In scope:

- Anything that makes the CLI execute code, exfiltrate credentials, or
  write outside its documented areas (`~/.config/pushframe/`, the target
  frame, the directories the user names on the command line).
- Credential leakage: Aura credentials, Google session cookies, or the
  download cache being readable by other local users (expected: `0600`
  vault / `0700` dirs).
- Supply-chain: a compromised build or publish pipeline (PyPI, APT repo
  signature bypass, GitHub Actions workflow escalation).
- Unsafe handling of photo/video metadata (EXIF, HEIC) via Pillow /
  pillow-heif where a crafted file could crash or escape the parser.

Out of scope:

- The Aura / Pushd **service** itself (report to Aura Frames Inc.).
- Rate limiting or availability of Aura's API.
- The fact that the API is undocumented/reverse-engineered — that is the
  project's stated premise.
- Vulnerabilities only reachable by already-root local attackers.

## Credential handling (what the tool stores)

- Aura credentials: `~/.config/pushframe/` vault files, mode `0600`,
  read at run time; never logged (the logger redacts them; `--debug`
  logs request/response metadata, not passwords).
- Google Photos: cookies in the same config home (`0600`), a staging
  cache under `google-cache/`, and a `0600` manifest. `google-sync`
  never deletes from Google; hide/show only, frame-side.
- The CLI never sends credentials anywhere except the endpoints it is
  documented to talk to.

## Supply chain & signing

- **PyPI**: published exclusively via GitHub Actions **trusted
  publishing** (OIDC) from this repository's `release.yml` — there is no
  PyPI token anywhere. Releases fire only on `v*` tags on master.
- **APT** (`https://coredmp95.github.io/pushframe/`): the repository is
  signed; verify the key fingerprint:

  ```
  0EE2DB2DB1360C58C4B2E0BF2EC06828F722F391
  ```

  (`curl -fsSL https://coredmp95.github.io/pushframe/dists/pushframe.asc
  | gpg --show-keys`). If you ever see a different fingerprint, stop and
  report it.
- **Actions**: the workflows pin first-party actions by version
  (`actions/checkout@v7`, `astral-sh/setup-uv@v10.2.0`); the test
  workflow runs the credential-less offline suite plus a `pip-audit`
  dependency gate on every push/PR.

## Automated monitoring

- Dependabot security updates + Dependency Graph: **enabled** (all 20
  alerts opened to date are resolved; `pip-audit --strict` also gates
  every CI run).
- Secret scanning + push protection: **enabled** (a push carrying a
  secret is blocked).

## Hardening notes for users

- Install from the three documented channels only (PyPI, the signed APT
  repo, GitHub Releases). Any other mirror is untrusted by definition.
- `--debug` writes verbose logs to stderr — do not paste them publicly
  without reviewing (they can contain your frame names and photo paths).
- The `google-browser` extra installs Playwright; only use it on machines
  where you accept a Chromium download, and only for `google-link`.
