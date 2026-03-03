# Talka Project Memory

- Scope: This repo (`talka`) is the web/backend source for the same product as `../talka-ios`.
- Canonical deploy target on Hetzner: `/opt/voice-bridge` (systemd service: `voice-bridge.service`).
- Sync rule (critical): Any API/behavior/state-flow change here must be evaluated and mirrored in `../talka-ios` in the same work cycle (or explicitly documented why not).
- Feature parity baseline:
  - voice turn async flow (`/api/voice/turn/start` + `/api/voice/turn/status/{turn_id}`)
  - conversation continuity via `conversation_id`
  - same first ACK voice path (prefer ElevenLabs ACK audio from backend)
  - same final TTS response behavior
- Git/deploy workflow (default):
  - local repo is source of truth for changes
  - commit locally (Conventional Commits) and push to GitHub
  - deploy by pulling on Hetzner in `/opt/voice-bridge`
  - default deploy command from local: `./deploy/scripts/deploy-hetzner`
  - restart `voice-bridge.service` after pull
  - verify after deploy: `systemctl is-active voice-bridge.service` and `curl -fsS http://127.0.0.1:8089/api/health`
  - avoid direct ad-hoc edits on Hetzner except emergency hotfixes
- Handoff requirement: mention parity status (`web/backend`, `ios`) in final update.
