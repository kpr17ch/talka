---
name: Talka Voice Bridge GitOps
description: This skill should be used when the user asks to "deploy talka", "push talka to github", "pull on hetzner", "update voice-bridge server", "ship voice bridge", or asks how Talka web/backend workflow should run from local to Hetzner.
version: 0.1.0
---

# Talka Voice Bridge GitOps

Use this skill for all Talka web/backend delivery tasks.

## Scope

- Local source path: `~/Projects/Private/talka`
- Server deploy path: `/opt/voice-bridge`
- Service name: `voice-bridge.service`
- Host alias: `hetzner`

## Mandatory workflow

1. Make code changes locally in `~/Projects/Private/talka`.
2. Commit locally with a Conventional Commit message.
3. Push to GitHub `main`.
4. Deploy by pulling on Hetzner in `/opt/voice-bridge`.
5. Restart `voice-bridge.service`.
6. Verify `/api/health`.

Never treat server edits as source of truth.

## Commands

Local commit/push:

```bash
cd ~/Projects/Private/talka
git status
git add -A
git commit -m "feat: <summary>"
git push origin main
```

Deploy to Hetzner:

```bash
cd ~/Projects/Private/talka
./deploy/scripts/deploy-hetzner
```

Manual fallback:

```bash
ssh hetzner 'cd /opt/voice-bridge && BRANCH=main ./deploy/scripts/voice-bridge-pull-deploy'
```

## Verification checklist

- `ssh hetzner 'systemctl is-active voice-bridge.service'` => `active`
- `ssh hetzner 'curl -fsS http://127.0.0.1:8089/api/health'` => `status=ok`
- If OpenCLAW is part of the task: verify `openclaw gateway status --json` on Hetzner.

## Parity rule

For API/state-flow changes in this repo, evaluate matching changes in `../talka-ios` in the same work cycle.

## Reference

See `docs/git-deploy-workflow.md` for the full project runbook.
