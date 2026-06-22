# AI Browser Agent

AI Browser Agent is a local web automation app that pairs a language model with a real Chromium browser. The backend uses FastAPI, Playwright, and your configured model provider; the frontend provides a live control panel for goals, browser state, screenshots, and manual actions.

The project is designed for local-first use. It can run with Ollama on your machine, and it also includes optional configuration for cloud providers.

## Features

- Autonomous browser navigation with Playwright Chromium
- Goal-based agent loop with page text, URL, title, screenshot, and element context
- Manual browser controls for navigation, clicking, typing, scrolling, and key presses
- Local Ollama support by default
- Optional OpenAI, Google Gemini, and Ollama Cloud configuration
- Session logs, result files, browser profiles, and uploaded attachments stored locally
- OCR and PDF support through optional Python/system dependencies
- Windows and Linux launch scripts, plus manual setup for macOS

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer
- npm
- Ollama, if you want to use local models

Optional:

- Tesseract OCR for OCR-enabled image or screen reading
- Linux desktop tools for desktop automation: `xdotool`, `wmctrl`, and `gnome-screenshot` or `scrot`

## Quick Start

### Windows

Run:

```bat
START.bat
```

The script installs backend dependencies, installs Playwright Chromium, installs frontend dependencies, starts both services, and opens the app at:

```text
http://localhost:3000
```

### Linux

Run:

```bash
chmod +x start.sh
./start.sh
```

The script installs dependencies, starts the backend and frontend, and opens the app when `xdg-open` is available.

### macOS

macOS can run the main web-agent workflow manually:

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 agent.py
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Then open:

```text
http://localhost:3000
```

Note: macOS desktop automation is not fully implemented yet. The browser agent itself should work, but OS-level app control currently has Windows and Linux-specific support.

## Manual Setup

Install and start the backend:

```bash
cd backend
python -m pip install -r requirements.txt
python -m playwright install chromium
python agent.py
```

Install and start the frontend in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

The backend listens on:

```text
http://localhost:8765
ws://localhost:8765/ws
```

## Ollama Setup

Install Ollama from the official website, then start the Ollama service:

```bash
ollama serve
```

Pull a model:

```bash
ollama pull qwen3:4b
```

Recommended local models:

| Model | Approx. RAM | Notes |
| --- | ---: | --- |
| `qwen3:4b` | 4 GB | Fast default choice |
| `qwen3:8b` | 8 GB | Better reasoning, slower |
| `gemma3:4b` | 4 GB | Lightweight alternative |

## Usage

Start the app, choose a model, and enter a goal such as:

- Search for the weather in Lagos, Nigeria
- Go to Wikipedia and summarize an article about space
- Find recent AI news and produce a short summary
- Open a site and extract key information

The agent can navigate, click, type, press keys, scroll, wait, go back, and stop when it believes the goal is complete.

## Configuration

Runtime configuration is stored in:

```text
backend/config.json
```

This file is intentionally ignored by Git because it can contain local provider URLs and API keys.

Important settings include:

- `active_provider`
- `active_model`
- `ollama_local_url`
- `google_api_key`
- `openai_api_key`
- `desktop_automation_enabled`
- `command_execution_mode`
- `filesystem_scope`

## Project Structure

```text
.
|-- backend/
|   |-- agent.py
|   |-- requirements.txt
|   |-- workspace_actions.py
|   |-- desktop_automation.py
|   `-- desktop_automation_portable.py
|-- frontend/
|   |-- src/
|   |-- index.html
|   |-- package.json
|   `-- vite.config.js
|-- START.bat
|-- start.sh
`-- README.md
```

Generated runtime folders such as logs, results, sessions, uploads, browser profiles, and artifacts are excluded from Git.

## API Overview

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/models` | List configured model options |
| `GET` | `/screenshot` | Return current browser screenshot and page metadata |
| `POST` | `/start` | Start an agent run |
| `POST` | `/stop` | Stop the active agent run |
| `POST` | `/manual` | Execute a manual browser action |
| `GET` | `/config` | Read safe configuration values |
| `POST` | `/config` | Update configuration |
| `WS` | `/ws` | Stream live browser updates |

## Privacy and Security

- Local Ollama inference stays on your machine.
- Cloud provider use depends on your configuration and sends prompts/context to that provider.
- Browser profiles, logs, uploaded files, results, and config files may contain sensitive data and are ignored by Git.
- Review generated actions before enabling automatic command or desktop execution.

## Troubleshooting

If the frontend fails after copying the project between operating systems, reinstall frontend dependencies:

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
```

If Playwright cannot launch Chromium:

```bash
cd backend
python -m playwright install chromium
```

If OCR is unavailable, install Tesseract and make sure the executable is available on your system path or configured through your environment.
