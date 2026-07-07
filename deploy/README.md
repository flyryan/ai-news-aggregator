# Deploy-side commit-signature verification

`scripts/deploy.sh` refuses to deploy a commit that is not SSH-signed by a
trusted key (CWE-345 fix, 2026-07). It verifies the tip of `origin/main` with
`git verify-commit` against an allow-list of signing keys before doing
`git reset --hard`. This closes the gap where anyone able to move `flyryan/main`
(a compromised token, a malicious force-push) would get their code executed on
the host at the next deploy.

## Trusted signers

`allowed_signers.example` is the tracked template listing the public keys we
trust to sign deployable commits:

- Ryan Duff — flyryan identity
- Ryan Duff — EMU identity
- AATF daily-pipeline CI bot (`PIPELINE_SIGNING_KEY` secret)

Add or rotate a signer by editing that file (and the real host copy). Use `*`
as the principal — the CI committer email contains `[bot]`, which the ssh
allowed-signers matcher treats as a glob character class, so a literal email
principal would silently fail to match.

## Host setup (one-time, per host)

The real allow-list is **git-ignored** and lives **outside** the working tree so
it survives `git reset --hard` / `git clean -fd`:

```bash
# on the host
cp /home/ubuntu/ai-news-aggregator/deploy/allowed_signers.example \
   /home/ubuntu/deploy_allowed_signers

cd /home/ubuntu/ai-news-aggregator
git config gpg.format ssh
git config gpg.ssh.allowedSignersFile /home/ubuntu/deploy_allowed_signers
```

`deploy.sh` also sets these two `git config` values itself each run, so they are
self-healing; the copy of `deploy_allowed_signers` is the only manual artifact.

## Emergency override

If a deploy must proceed with an unsigned tip (e.g. mid-migration), set
`ALLOW_UNSIGNED_DEPLOY=1` in the deploy environment. It is logged loudly in
`logs/deploy.log`. Use sparingly.

## Committing so your commits pass the gate

Commits to `main` must be SSH-signed. Locally that means:

```bash
git config --global gpg.format ssh
git config --global commit.gpgsign true
git config --global user.signingkey ~/.ssh/id_flyryan.pub   # or your EMU key
```

Once set, every `git commit` is signed automatically — no per-commit flag. CI
signs via the `PIPELINE_SIGNING_KEY` secret in the daily-pipeline workflow. If
an unsigned commit ever reaches the tip of `main`, the deploy aborts (visible in
`logs/deploy.log`) rather than shipping it.
