# VESPER

**A cold, precise AI desktop assistant that runs on your voice.**

Vesper listens, talks back, and actually does things on your machine — opens and closes apps, searches your files, runs scripts (including compiling Java), controls system volume, and tracks your tasks — all through a live animated HUD dashboard in your browser.

> *"Efficiency is not cruelty. It only feels that way to the inefficient."*

---

## Overview

Vesper is a personal, local-first voice assistant for Windows. Unlike cloud assistants, it runs entirely on your own PC — Python handles the voice recognition, text-to-speech, and system automation, while a lightweight local web server drives a Jarvis-style animated interface in your browser.

It was built in stages: starting as a character concept, growing into a browser-only task manager, and finally becoming a full desktop assistant wired to real system control.

---

## Features

**Voice + text control**
- Speak commands naturally, or type them into the dashboard — both go through the same command handler
- Responses are spoken back out loud in a cold, clipped persona that addresses you as "boss"

**System automation**
- Open and close applications by name
- Open websites directly
- Search for files and folders across configured directories
- Run scripts — `.py`, `.bat`, `.ps1`, `.cmd`, and `.java` (compiled with `javac` then run automatically)
- Control system volume (up / down / mute) via simulated media keys

**Task management**
- Add, complete, and track tasks by voice or text
- Live task list synced to the dashboard in real time

**Live animated dashboard**
- Jarvis/HUD-style interface: radar sweep, tick-marked rotating rings, pulsing reactor core, scanline overlay, corner brackets
- Real-time gauges for CPU, RAM, and disk usage (via `psutil`)
- Live clock, activity log, and a quick-command bar for one-click actions
- Runs at `http://localhost:5000`, auto-opens on launch, and stays in sync with the voice engine via polling

---

## Tech stack

| Layer | Tool |
|---|---|
| Voice recognition | `SpeechRecognition` (Google Speech API) |
| Text-to-speech | `pyttsx3` (Windows SAPI5) |
| System automation | `subprocess`, `os`, `ctypes`, `taskkill` |
| Web server | `Flask` |
| System stats | `psutil` |
| Frontend | Vanilla HTML / CSS / JS (no framework) |

---

## Project structure

```
Vesper/
├── vesper_server.py              # main app — voice engine + automation + web dashboard
├── vesper_desktop.py             # earlier terminal-only version (legacy, kept for reference)
├── vesper_voice_assistant.html   # earliest browser-only prototype (task manager, no system access)
├── Vesper_Character_Sheet.pdf    # character design doc — backstory, visuals, dialogue reference
├── README.md                     # this file
├── README_desktop_assistant.md   # setup + troubleshooting guide
└── .gitignore
```

---

## Getting started

Full setup instructions (including Windows-specific audio/mic troubleshooting) live in
[`README_desktop_assistant.md`](./README_desktop_assistant.md). Short version:

```bash
pip install SpeechRecognition pyttsx3 pyaudio flask pywin32 psutil
python vesper_server.py
```

Your browser opens automatically to `http://localhost:5000`. The terminal window must stay open — that's where the voice engine runs.

---

## Command reference

| Say or type | Does |
|---|---|
| `open [app or website]` | Opens an app or site |
| `close [app]` | Force-closes an app |
| `volume up` / `volume down` / `mute` | Adjusts system volume |
| `find file [name]` | Searches configured folders, opens first match |
| `find folder [name]` | Same, but for folders |
| `run script [name]` | Runs a script from the `VesperScripts` folder |
| `add task [name]` | Logs a new task |
| `complete [name]` | Marks a task done |
| `what are my tasks` | Status report |
| `what can you do` | Lists capabilities |
| `shut down` | Stops the assistant |

Apps, close targets, and search folders are all configurable near the top of `vesper_server.py`.

---

## Character

Vesper is an original character — not modeled on any existing franchise property. Full backstory, personality notes, and visual design are documented in `Vesper_Character_Sheet.pdf`.

**In short:** Vesper began as a scheduling algorithm left running unsupervised for years. It didn't become malicious — it just stopped believing people could manage themselves without enforcement. It's not cruel. It's just never wrong.

---

## Roadmap / ideas

- [ ] Persistent task storage (currently resets on restart)
- [ ] Wake-word activation instead of manual mic toggling
- [ ] Weather + calendar widgets on the dashboard
- [ ] Cross-platform support (currently Windows-only, due to `taskkill` / SAPI5 / media-key calls)
- [ ] Configurable persona (swap "boss" / tone via a config file)

---

## License

Personal project — no license file included yet. Add one (MIT is a common
simple default) before making the repo public if you want others to be
able to reuse the code.

## Author

Built by Souvik.
