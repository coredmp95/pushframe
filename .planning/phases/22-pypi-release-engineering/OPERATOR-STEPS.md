# OPERATOR STEPS — first release v5.0.0 (phase 22)

One-time setup (~15 min), then every future release is exactly:
`git tag vX.Y.Z && git push origin vX.Y.Z`.

Do the steps in order. Everything else is already automated by
`.github/workflows/release.yml`.

## 1. TestPyPI account + 2FA

- https://test.pypi.org/account/register/ — register (email verification).
- Enable 2FA: Account settings → 2FA (TOTP app or passkey; PyPI requires
  2FA since 2024).

## 2. TestPyPI trusted-publisher declaration

You do NOT upload anything by hand — you declare, in advance, WHO may
publish the `pushframe` project. PyPI calls this a "pending publisher"
(the project doesn't exist until the first OIDC upload).

- Go to https://test.pypi.org/manage/account/publishing/
- Fill the form with EXACTLY:

| Field | Value |
|---|---|
| PyPI project name | `pushframe` |
| Owner | `coredmp95` |
| Repository | `pushframe` |
| Workflow name | `release.yml` |
| Environment name | `release` |

- Save. A mismatch in ANY field = 403 at upload time.

## 3. PyPI account + 2FA

- https://pypi.org/account/register/ + enable 2FA (same drill).
- IMPORTANT: keep the TestPyPI and PyPI passwords/usernames distinct in
  your password manager; they are separate accounts.

## 4. PyPI trusted-publisher declaration

- https://pypi.org/manage/account/publishing/ — same table as step 2,
  exactly the same five values.
- Also claim the project name now if PyPI offers "project name
  reservation" — protects `pushframe` on real PyPI before the first upload.

## 5. GitHub: `release` environment

- https://github.com/coredmp95/pushframe/settings/environments/new
- Name: `release` (must match the workflow + declarations, case-sensitive).
- Optional but recommended: add yourself as a required reviewer — the
  release then pauses for your click before touching PyPI/APT.

## 6. GitHub secret: APT signing key

The release workflow republishes the APT repo from CI; it needs the
private signing key (the same one backed up in your Vault).

```bash
gh secret set PUSHFRAME_APT_SIGNING_KEY --repo coredmp95/pushframe \
  < ~/.config/pushframe-apt-key/signing-key.asc
```

(or web UI: Settings → Secrets and variables → Actions → New repository
secret). The secret holds the ARMORED secret key; the workflow extracts
the fingerprint itself.

## 7. First release

```bash
git switch master && git pull
git tag v5.0.0 && git push origin v5.0.0
```

- Watch: https://github.com/coredmp95/pushframe/actions (workflow `release`).
- Job order: check → pypi-test & deb → apt & pypi.
- ~6-8 min total (the deb build dominates).

## 8. Verify

```bash
# rehearsal proof (container, TestPyPI):
scripts/test-install-journey-container.sh test
# real proof (container, PyPI):
scripts/test-install-journey-container.sh live
# plus: https://pypi.org/project/pushframe/ shows README + disclaimer;
# https://github.com/coredmp95/pushframe/releases/tag/v5.0.0 has the .deb;
# https://coredmp95.github.io/pushframe/ serves the updated APT repo.
```

## Failure playbook

- **`403 Forbidden` at a `uv publish` step** — trusted-publisher
  declaration mismatch. Check, in order: (1) workflow filename is exactly
  `release.yml` in the declaration; (2) environment name is exactly
  `release` (the workflow's `environment:` and the declaration must be
  identical, case included); (3) owner/repo spellings; (4) the project
  name field on the declaration matches pyproject's `name`. Fix the
  declaration in the (Test)PyPI UI and re-run the FAILED job from the
  Actions UI (releases are idempotent per version).
- **`check` job fails: version mismatch** — the tag doesn't equal
  pyproject/`__init__`. Never edit the tag; move it properly:
  `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`, fix the
  version in pyproject+`__init__` (they are lockstep-guarded by tests),
  re-tag.
- **`apt` job fails** — usually the secret: `gh secret list` shows
  `PUSHFRAME_APT_SIGNING_KEY`; re-set it from the Vault copy
  (`vault kv get -field=signing_key_asc kv/pushframe/apt-signing-key`).
  A 403 on the gh-pages push means the workflow's `permissions:` block was
  edited — restore `contents: write`.
- **Same version already exists on (Test)PyPI** — versions are immutable.
  New upload = new version (e.g. 5.0.1). The APT repo and the GitHub
  Release tolerate re-runs; PyPI never does.
