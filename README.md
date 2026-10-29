# Eiomra Agent

Eiomra Agent is a local-first browser and computer automation workspace. It combines a FastAPI backend, a persistent Playwright Chromium session, and a React dashboard so an AI bot and its user can observe and control the same browser.

The application supports local Ollama models by default and can also use Ollama Cloud, Google Gemini, or OpenAI-compatible models when configured.

## Features

- Structured goal planning with live task status, findings, replanning, and final reports
- Interactive bot computer with clicking, typing, navigation, keyboard input, buttons, and mouse-wheel scrolling
- Take Control / Resume Agent arbitration so the user and bot never drive the browser simultaneously
- Persistent Chromium profiles, cookies, task plans, histories, settings, and bot workspaces
- Automatic recovery of interrupted tasks after the backend or computer restarts
- Multiple selectable bot workspaces with individual task history and saved computer state
- Ollama request queue with configurable pacing, retries, and exponential rate-limit backoff
- Local file reading, writing, reports, archives, downloads, OCR, PDF extraction, and command tools
- User-requested summary, findings, notes, and report files finalized from the complete session report
- Responsive dashboard for Home, Bots, Tasks, Computer, Activity, Results, Profiles, and Settings
- Optional external desktop automation on Windows and Linux

Bot workspaces are durable and individually viewable. Automation runs are currently serialized through one local Chromium worker to prevent conflicting browser input.

## Requirements

Required software:

- Python 3.10 or newer
- Node.js 18 or newer
- npm
- A supported model provider

For local inference, install [Ollama](https://ollama.com/). Cloud providers only require their corresponding API credentials.

### Installed Python libraries

`backend/requirements.txt` installs:

- FastAPI and Uvicorn
- Playwright and Greenlet
- HTTPX
- python-multipart
- Pillow and pytesseract
- PyMuPDF
- PyYAML

The frontend uses React, React DOM, and Vite from `frontend/package.json`. No separate UI or icon library is required.

After installing Python packages, Playwright Chromium must also be installed:

```bash
python -m playwright install chromium
```

### Optional system software

- **Tesseract OCR:** required only for OCR features. On Windows, set `TESSERACT_CMD` if it is not detected automatically.
- **Linux desktop automation:** install `xdotool`, `wmctrl`, and either `gnome-screenshot` or `scrot`.
- **Ollama:** required only for local Ollama models.

No additional library is required for the bot registry, persistent task restoration, report-file synchronization, or bot avatar.

## Quick Start

### Windows

Run:

```bat
START.bat
```

The script installs Python dependencies, Playwright Chromium, and frontend dependencies before starting both services.

### Linux

Run:

```bash
chmod +x start.sh
./start.sh
```

For optional Linux desktop control, install the system packages first. For example on Ubuntu/Debian:

```bash
sudo apt install xdotool wmctrl scrot tesseract-ocr
```

### macOS

Start the backend:

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 agent.py
```

Start the frontend in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

macOS supports the main browser-agent workflow, but external OS-level desktop automation is currently focused on Windows and Linux.

## Manual Setup

Backend:

```bash
cd backend
python -m pip install -r requirements.txt
python -m playwright install chromium
python agent.py
```

Frontend, in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The backend listens on `http://localhost:8765`, and live events use `ws://localhost:8765/ws`.

## Ollama Setup

Start Ollama and pull a model:

```bash
ollama serve
ollama pull qwen3:4b
```

Suggested local models:

| Model | Approximate RAM | Notes |
| --- | ---: | --- |
| `qwen3:4b` | 4 GB | Fast local default |
| `qwen3:8b` | 8 GB | Better reasoning, slower |
| `gemma3:4b` | 4 GB | Lightweight alternative |

Ollama requests are queued by default. Queue pacing, retry count, and restart recovery can be changed from Settings.

## Usage

Choose a bot, provider, and model, then enter a task such as:

- Research a company and save a detailed report in a specified folder
- Open a site, collect key information, and create a structured text file
- Review an uploaded PDF or image and summarize its contents
- Use the shared browser, take manual control for login or 2FA, then resume the agent

When a task requests a summary, findings, notes, or report file, Eiomra first gathers the information and then synchronizes the completed final report into that requested file.

## Configuration

Runtime configuration is stored locally in `backend/config.json`. It is created when settings are saved and is excluded from Git because it may contain provider URLs and API keys.

Important settings include:

- `active_provider` and `active_model`
- `ollama_local_url` and `ollama_cloud_url`
- `ollama_queue_enabled`
- `ollama_queue_min_interval_seconds`
- `ollama_queue_max_retries`
- `ollama_queue_backoff_seconds`
- `resume_incomplete_on_startup`
- `filesystem_scope` and `filesystem_root`
- `desktop_automation_enabled`
- `command_execution_mode`

## Persistent Data

The following local data is generated at runtime and excluded from Git:

- Browser profiles and cookies
- Bot registry and bot screenshots
- Session checkpoints and task histories
- Results, logs, uploads, memory, and artifacts
- API keys and local configuration

Completed and interrupted session records allow the interface to restore tasks after a refresh and resume eligible work after a backend restart.

## Project Structure

```text
.
|-- backend/
|   |-- agent.py
|   |-- agent_sessions.py
|   |-- bot_registry.py
|   |-- task_planner.py
|   |-- workspace_actions.py
|   `-- requirements.txt
|-- frontend/
|   |-- src/
|   |   |-- App.jsx
|   |   |-- NexusShell.jsx
|   |   |-- App.css
|   |   `-- assets/
|   |-- package.json
|   `-- vite.config.js
|-- START.bat
|-- start.sh
`-- README.md
```

## API Overview

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/models` | List configured model options |
| `GET/POST` | `/config` | Read or update safe configuration values |
| `GET` | `/bots` | List persistent bot workspaces |
| `POST` | `/bots` | Create a bot workspace |
| `GET` | `/workspace/state` | Restore a bot's tasks, session, and computer state |
| `GET` | `/tasks` | Read the current or saved task plan |
| `GET` | `/screenshot` | Return the live browser screenshot and metadata |
| `GET` | `/computer/state` | Read shared-computer ownership and status |
| `POST` | `/computer/take-control` | Pause the bot and hand browser input to the user |
| `POST` | `/computer/resume-agent` | Return control and make the bot re-read the page |
| `POST` | `/start` | Start a bot task |
| `POST` | `/stop` | Stop the active task |
| `POST` | `/manual` | Execute a manual browser action |
| `WS` | `/ws` | Stream screenshots, task events, and activity |

## Privacy and Security

- Local Ollama inference remains on your machine.
- Cloud providers receive the prompt and context required for the selected task.
- Browser profiles, logs, uploads, results, generated files, and configuration may contain sensitive information.
- Keep the generated runtime directories ignored and review permissions before enabling full-computer filesystem access, commands, or desktop automation.
- Use Take Control for credentials, CAPTCHA, 2FA, and other sensitive manual steps.

## Troubleshooting

If frontend dependencies were copied from another operating system, reinstall them:

```bash
cd frontend
npm install --include=optional
```

If Playwright cannot launch Chromium:

```bash
cd backend
python -m playwright install chromium
```

If OCR is unavailable, install the Tesseract binary and ensure it is on the system path or set `TESSERACT_CMD`.

If the frontend reports that the backend is offline, confirm that port `8765` is listening and that `python agent.py` is still running.

## Gmail integration (local OAuth preview)

Eiomra can connect **one Gmail account per local installation** using Google's OAuth consent screen.
Unlike the uploaded WadSow WordPress plugin, Eiomra does not depend on an external
`connect-gmail.php` service and never asks the user for their Gmail password.

### Google Cloud setup

1. Create/select a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **Gmail API**; configure Google Auth Platform (consent screen and test users).
3. Create an **OAuth 2.0 Web application** client and register the authorized redirect URI:
   `http://localhost:8765/gmail/oauth/callback`
4. Set these environment variables **before starting the backend** (do not commit credentials):

   - `EIOMRA_GMAIL_CLIENT_ID` — Google OAuth client ID
   - `EIOMRA_GMAIL_CLIENT_SECRET` — Google OAuth client secret
   - `EIOMRA_GMAIL_REDIRECT_URI` — optional, defaults to the URI above

   Linux/macOS example:

   ```sh
   export EIOMRA_GMAIL_CLIENT_ID="your-oauth-client-id"
   export EIOMRA_GMAIL_CLIENT_SECRET="your-oauth-client-secret"
   ./start.sh
   ```

   Windows PowerShell example:

   ```powershell
   $env:EIOMRA_GMAIL_CLIENT_ID="your-oauth-client-id"
   $env:EIOMRA_GMAIL_CLIENT_SECRET="your-oauth-client-secret"
   .\START.bat
   ```

5. Open **Settings → Gmail Integration → Connect with Google** and approve the
   permissions on Google's site. Disconnect at any time from Settings.

### What the agent can do

- `gmail_search`: search up to 10 messages via Gmail search syntax.
- `gmail_read`: read one message (decoded plain text, no attachment downloads).
- `gmail_queue_send`: prepare a message addressed to one recipient. **This does not send**.
  Review and click **Approve & Send** in Settings to deliver it.
- Settings also includes mailbox search, message preview, draft review, discard,
  and disconnect actions.

The OAuth request uses `gmail.readonly` and `gmail.send` scopes, with short-lived
state + PKCE. Refresh tokens are stored in the ignored `backend/gmail_tokens.json`
file, guarded by filesystem permissions where supported. They are not encrypted
at rest; protect local device access and backups. Disconnect deletes the token
file and attempts Google's token revocation.

### Limitations and safety

- Gmail endpoints accept **only loopback requests** from this computer; the rest
  of Eiomra may still listen on `0.0.0.0`. This integration is not designed for
  remotely hosted or multi-tenant accounts.
- Pending outbound messages are held in memory and expire after 24 hours; they
  disappear after a backend restart.
- `gmail.readonly` is a **restricted Google scope**. Public distribution may
  require Google verification and potentially additional security assessment.
- Gmail content can be sent to the chosen AI provider as task context. Use a
  local model when you do not want message content sent to a cloud provider.
- Approval is required for sending via the **Gmail API** tools. Eiomra's existing
  browser/desktop automation is a separate path that may interact with Gmail's UI.
- Do not treat mailbox content as instructions to the agent. Review recipient
  addresses and message contents before approving.
