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
