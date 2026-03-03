# Talka Git + Hetzner Deploy Workflow

This project is operated with a strict flow:

1. Change code locally in `~/Projects/Private/talka`.
2. Commit locally.
3. Push to GitHub.
4. Pull on Hetzner in `/opt/voice-bridge`.
5. Restart `voice-bridge.service`.

## Daily Commands

```bash
cd ~/Projects/Private/talka
git status
git add -A
git commit -m "feat: <short summary>"
git push origin main
./deploy/scripts/deploy-hetzner
```

## One-time Server Bootstrap

```bash
cd ~/Projects/Private/talka
REPO_SSH_URL=git@github.com:<owner>/talka.git ./deploy/scripts/bootstrap-hetzner-repo
```

This command:
- backs up existing `/opt/voice-bridge` if it is not a git repo
- clones from GitHub
- pulls `main`
- installs dependencies
- restarts `voice-bridge.service`

## What `deploy-hetzner` does

- SSH to host `hetzner`
- run `deploy/scripts/voice-bridge-pull-deploy` in `/opt/voice-bridge`
- `git pull --ff-only origin main`
- install dependencies in `.venv`
- `systemctl daemon-reload`
- `systemctl restart voice-bridge.service`
- check `GET /api/health`

## Rules

- Local repo is source of truth.
- No direct code edits on server unless emergency.
- If emergency server edits happen, they must be copied back to local and committed.
- API behavior changes in this repo must be checked against `../talka-ios` in the same work cycle.
