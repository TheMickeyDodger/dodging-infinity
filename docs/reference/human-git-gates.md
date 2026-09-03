# Human Git gates

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

**[IMPLEMENTED / PROVEN]** Three deterministic one-shot human
authorization gates protect commit, branch push, and release-tag push.
They are enforced by the installed Git guards and pinned by the Herdr
guard suites. The authority model they implement is on the wiki
[Authority and Safety](../wiki/Authority-and-Safety.md) page.

## Why the gates are separate

Each gate authorizes exactly one action against exactly one state. A
commit approval binds a staged diff; it says nothing about pushing the
resulting commit. A push approval binds a commit SHA and a remote ref; it
says nothing about a tag. A tag approval binds one annotated tag object.
No approval inherits from another, none is reusable, and each carries a
short TTL. The chain is deliberately broken at every link: mission
authorization does not authorize a commit, a commit does not authorize a
push, a push does not authorize a merge, a merge does not authorize a
release, and a release does not authorize deployment.

Remote missions carry `delivery_authority = none`. No Telegram message,
plain text or approval callback, can operate any of these gates today.
Exact, one-shot Telegram-native delivery approvals are a Phase-I
requirement in the
[Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md), not
implemented behavior.

## Human commit gate

Stage the exact desired result.

Then:

```bash
herdctl approve-commit --repo example-repo
```

Authorization is bound to:

- repository/worktree
- branch
- HEAD
- exact staged diff hash
- short TTL

A changed staged diff, branch, or HEAD invalidates the approval.

For Codex Operator flows after explicit human confirmation:

```bash
herdctl approve-commit --yes
```

Codex may then execute the exact commit.

## Human push gate

Inspect:

```bash
git status -sb
git log --oneline origin/main..HEAD
```

Authorize:

```bash
herdctl approve-push --repo example-repo
```

Then:

```bash
git push
```

A dry run does not consume approval:

```bash
git push --dry-run
```

Push remains independently authorized from commit.

## Human release-tag gate

Create an annotated tag:

```bash
git tag -a vX.Y.Z -m "Dodging Infinity vX.Y.Z"
```

Authorize exactly that tag:

```bash
herdctl approve-push --tag vX.Y.Z
```

Push:

```bash
herdctl push-tag vX.Y.Z
```

Authorization binds to the exact tag ref and object.

## What a gate does not authorize

- A commit approval does not authorize a push, a PR, a tag, a release, a
  deployment, or a merge.
- A push approval does not authorize a tag push, a merge, a release, or a
  deployment. `git push --dry-run` does not consume it.
- A tag approval authorizes exactly one tag ref and tag-object SHA. It does
  not authorize a release or a deployment.
- No gate can be operated by an agent, a transport, or a UI on its own.
  `herdctl approve-commit --yes` exists for Operator flows and is used only
  after explicit human confirmation.
- Git itself permits bypass forms such as `git push --no-verify`.
  Runtime-level command protections and role contracts complement the
  deterministic Git guards; they do not replace human authorization.
