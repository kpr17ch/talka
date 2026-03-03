# Voice Bridge Prototype

Voice-Bridge-Prototyp fuer OpenClaw + Telegram Mirror:

- Browser Voice-In mit Async-Flow (`/api/voice/turn/start` + Polling auf `/api/voice/turn/status/{turn_id}`)
- STT (OpenAI Whisper API oder lokal)
- OpenClaw Agent-Call per CLI mit `--deliver`
- Optionales Spiegeln von Nutzer-Transkript nach Telegram (`openclaw message send`, parallel zum Agent-Call)
- Text-Orchestrator (`raw_text -> speak_text`)
  - bevorzugt vorhandene `VOICE`/`VOICE_SUMMARY` Bloecke aus Agent-Antworten
  - filtert Code/Pfade/URLs fuer Voice
  - haengt optional Detail-Hinweis an (`... Details in Telegram`)
- ElevenLabs TTS (mp3 base64)
- Sehr simples Frontend (weiße Seite + runder Voice-Button)

## Architektur

```text
Browser (mic)
  -> FastAPI /api/voice/turn/start
    -> STT (OpenAI/local)
    -> sofortige ACK-Response (status=processing)
    -> Background Job
       -> openclaw agent --json --deliver ...
       -> Orchestrator (speech cleanup)
       -> ElevenLabs TTS
  -> FastAPI /api/voice/turn/status/{turn_id} (Polling)
  <- JSON (completed|failed + final payload)
```

## CLI-Basis (live verifiziert auf Hetzner)

Getestet am 2026-02-24 UTC auf `openclaw 2026.2.19-2`:

- `openclaw agent send` existiert dort **nicht**.
- Relevanter Command ist `openclaw agent`.
- Erfolgreicher JSON-Turn:

```bash
openclaw agent --json --channel telegram --to <chat_id> --deliver --message "..."
```

Hinweis: Wenn du eigene Session-IDs setzen willst, wird in diesem Projekt
`--session-id <id>` plus `--reply-channel/--reply-to` fuer Delivery genutzt.

## Projektstruktur

```text
app/
  main.py
  config.py
  stt.py
  openclaw_client.py
  orchestrator.py
  tts.py
  models.py
  logging_setup.py
  rate_limit.py
  errors.py
frontend/
  index.html
  app.js
  styles.css
deploy/
  systemd/voice-bridge.service
  nginx/voice-bridge.conf
  nginx/voice-bridge-rate-limit.conf
tests/
```

## Lokales Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8089
```

Dann `http://127.0.0.1:8089` öffnen.

## API

### `POST /api/voice/turn/start`

`multipart/form-data`:

- `audio` (required)
- `conversation_id` (optional)
- `client_turn_id` (optional)

Erfolgsresponse:

- `turn_id`
- `conversation_id`
- `user_text`
- `ack_text` (derzeit leer, ACK deaktiviert)
- `ack_audio_base64` (derzeit `null`, ACK deaktiviert)
- `ack_audio_mime` (derzeit `null`, ACK deaktiviert)
- `status` (`processing`)
- `poll_after_ms`

### `POST /api/voice/wake/turn/start`

Wake-Word Probe + optional Turn-Start in einem Request.
Matching ist tolerant (fuzzy) und erlaubt einen kleinen Prefix-Offset.

`multipart/form-data`:

- `audio` (required)
- `conversation_id` (optional)
- `client_turn_id` (optional)
- `wake_phrase` (optional, default aus `WAKE_PHRASE`)

Wake-Matching-Parameter (Env):

- `WAKE_PHRASE_SIMILARITY_THRESHOLD` (default `0.8`)
- `WAKE_PHRASE_MAX_OFFSET_TOKENS` (default `2`)

Response:

- `transcript`
- `wake_phrase`
- `wake_detected`
- `turn_started`
- `turn` (`VoiceTurnStartResponse`, wenn `turn_started=true`)

### `GET /api/voice/turn/status/{turn_id}`

Response:

- `status=processing` (inkl. `progress_stage`, `progress_message`)
- oder `status=completed` + `result` (gleiches Schema wie `VoiceTurnResponse`)
- oder `status=failed` + `error`
- oder `status=cancelled`

### `POST /api/voice/turn/cancel/{turn_id}`

Bricht einen laufenden Turn ab (falls `status=processing`).

Response:

- `status=cancelled`
- `status=already_done`
- `status=not_found`

### `POST /api/voice/turn`

Legacy sync endpoint (weiterhin vorhanden). Wartet bis OpenClaw/TTS fertig sind.

`multipart/form-data`:

- `audio` (required)
- `conversation_id` (optional)
- `client_turn_id` (optional)

Erfolgsresponse:

- `turn_id`
- `conversation_id`
- `user_text`
- `raw_text`
- `speak_text`
- `audio_base64` (kann `null` sein bei TTS-Fehler)
- `audio_mime`
- `timings_ms`
  - `request_prep`
  - `stt`
  - `user_text_mirror`
  - `openclaw`
  - `orchestrator`
  - `tts`
  - `audio_encode` (subset von `tts`, nur Base64-Encode)
  - `response_build`
  - `total`
- `meta`
  - `user_text_mirror_attempted`
  - `user_text_mirror_sent` (`null`, wenn Mirror asynchron nach der Response laeuft)

Response Header:

- `Server-Timing` (z. B. `prep;dur=.., stt;dur=.., mirror;dur=.., agent;dur=.., tts;dur=.., total;dur=..`)

Frontend Debug:

- Browser-Konsole zeigt pro Turn `"[voice-bridge][latency_ms]"` mit Client- und Server-Latenzen.

Hinweis zu Timings:

- `user_text_mirror` kann `0` sein, wenn das Mirror-Posting asynchron im Hintergrund nach der Response laeuft.

### `GET /api/health`

Dependency-Status, Konfigurationsflags.

### `GET /api/version`

Service-Version.

## Produktion (Hetzner)

1. App nach `/opt/voice-bridge` deployen.
2. Venv + Dependencies installieren.
3. `/etc/voice-bridge.env` anlegen (auf Basis `.env.example`).
4. `voice-bridge.service` nach `/etc/systemd/system/` kopieren und starten.
5. Nginx-Configs aktivieren.
6. TLS mit Certbot ausstellen.

Optionales Hilfsskript:

```bash
./deploy.sh
```

### Laufender Git-Deploy-Flow (empfohlen)

```bash
git push origin main
./deploy/scripts/deploy-hetzner
```

Details: `docs/git-deploy-workflow.md`

Einmaliger Server-Bootstrap (falls `/opt/voice-bridge` noch kein Git-Repo ist):

```bash
REPO_SSH_URL=git@github.com:<owner>/talka.git ./deploy/scripts/bootstrap-hetzner-repo
```

## Systemd

Unit: [`deploy/systemd/voice-bridge.service`](deploy/systemd/voice-bridge.service)

## Nginx

- Site: [`deploy/nginx/voice-bridge.conf`](deploy/nginx/voice-bridge.conf)
- Rate-limit Zone: [`deploy/nginx/voice-bridge-rate-limit.conf`](deploy/nginx/voice-bridge-rate-limit.conf)

## HTTPS + Access Protection

- HTTPS via Certbot (Let's Encrypt)
- HTTP -> HTTPS redirect
- `auth_basic` protection via `/etc/nginx/.htpasswd-voice`
- Default virtual hosts return `444` for unknown hostnames

## Troubleshooting

- `OpenClaw binary not found`: `OPENCLAW_BIN` korrigieren oder PATH fixen.
- `OpenClawNonZeroExit`: CLI manuell testen mit identischem Command.
- Lange Tasks/Timeouts: Async-Flow nutzen (`/api/voice/turn/start` + `/status`), und `OPENCLAW_TIMEOUT_SECONDS` auf realistischen Wert fuer lange Jobs setzen (z. B. `300-900`).
- `STTError`: `OPENAI_API_KEY`/Whisper-Modell/Audioformat prüfen.
  - bei transienten OpenAI-Fehlern wird automatisch retryt (`STT_OPENAI_MAX_RETRIES`, `STT_OPENAI_RETRY_BACKOFF_MS`)
- `TTSError`: ElevenLabs Key/Voice-ID prüfen.
- Keine Telegram-Spiegelung: `OPENCLAW_CHANNEL`, `OPENCLAW_TO`, Channel-Status prüfen (`openclaw status`).
- Kein User-Text in Telegram: `MIRROR_USER_TEXT_TO_TELEGRAM=true` setzen und `USER_TEXT_MIRROR_TARGET` prüfen.

Logs:

```bash
journalctl -u voice-bridge.service -f
```

## Sicherheit

- Keine API-Keys in Logs.
- CORS-Allowlist (kein `*`).
- Upload-Limits + MIME-Check.
- App-Rate-Limit + optional Nginx-Rate-Limit.
- OpenClaw Gateway nicht direkt oeffentlich exponieren.

## OpenAI STT Setup (Production)

Use this helper on the server to securely set `OPENAI_API_KEY` and switch STT to OpenAI:

```bash
sudo /opt/voice-bridge/deploy/scripts/voice-bridge-configure-openai-stt
```

Optional (non-interactive):

```bash
sudo /opt/voice-bridge/deploy/scripts/voice-bridge-configure-openai-stt 'sk-...'
```

What it does:

- writes key to `/etc/voice-bridge.secrets` (mode `600`)
- sets `STT_PROVIDER=openai`
- sets `OPENAI_STT_MODEL=gpt-4o-transcribe`
- restarts `voice-bridge.service`
