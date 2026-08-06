"""
VESPER — integrated desktop assistant with animated frontend (Windows)
-----------------------------------------------------------------------
One app now: it listens on your mic, controls your PC (open/close apps,
volume, file search, scripts), speaks back calling you "boss", and drives
a live animated web interface in your browser — all connected.

SETUP (Command Prompt, in this folder):
    pip install SpeechRecognition pyttsx3 pyaudio flask pywin32

RUN:
    python vesper_server.py

It will open http://localhost:5000 in your browser automatically.
The terminal window must stay open — that's where the voice engine runs.
"""

import os
import sys
import glob
import time
import ctypes
import queue
import threading
import subprocess
import webbrowser
import difflib
from datetime import datetime

from flask import Flask, jsonify, request, Response

import speech_recognition as sr
import pyttsx3

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

try:
    import pythoncom
    HAVE_PYTHONCOM = True
except ImportError:
    HAVE_PYTHONCOM = False

# ---------------------------------------------------------------------------
# CONFIG — edit for your machine
# ---------------------------------------------------------------------------

APP_MAP = {
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "file explorer": "explorer",
    "explorer": "explorer",
    "task manager": "taskmgr",
    "control panel": "control",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "chrome": "chrome",
    "edge": "msedge",
    "spotify": "spotify",
    "vs code": "code",
    "visual studio code": "code",
    "cmd": "cmd",
    "command prompt": "cmd",
    "settings": "start ms-settings:",
}

# process image names, for "close [app]" -> taskkill
CLOSE_MAP = {
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "paint": "mspaint.exe",
    "task manager": "Taskmgr.exe",
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "spotify": "Spotify.exe",
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
}

WEBSITE_MAP = {
    "youtube": "https://youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "google": "https://google.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
}

SEARCH_ROOTS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
]

SCRIPTS_DIR = os.path.join(os.path.expanduser("~"), "VesperScripts")

ADDRESS = "boss"  # what Vesper calls you

# ---------------------------------------------------------------------------
# SHARED STATE (frontend polls this)
# ---------------------------------------------------------------------------

state_lock = threading.Lock()
state = {
    "status": "idle",          # idle | listening | processing | speaking
    "heard": "",
    "response": "Vesper online. Awaiting instruction.",
    "tasks": [],                # {"name": str, "done": bool}
    "log": [],                  # recent action log, newest first
}


def update_state(**kwargs):
    with state_lock:
        state.update(kwargs)


def push_log(entry):
    with state_lock:
        state["log"].insert(0, entry)
        state["log"] = state["log"][:12]


# ---------------------------------------------------------------------------
# VOICE
# ---------------------------------------------------------------------------

speech_queue = queue.Queue()


def _speak_now(text: str):
    """Speak one utterance with a fresh engine instance. Reusing a single
    pyttsx3 engine across multiple say()/runAndWait() calls is known to
    silently hang on the second and later calls on Windows — creating a
    new engine each time avoids that."""
    if HAVE_PYTHONCOM:
        pythoncom.CoInitialize()
    try:
        eng = pyttsx3.init()
        eng.setProperty("rate", 168)
        eng.setProperty("volume", 1.0)
        for v in eng.getProperty("voices"):
            if "david" in v.name.lower() or "male" in v.name.lower():
                eng.setProperty("voice", v.id)
                break
        eng.say(text)
        eng.runAndWait()
        try:
            eng.stop()
        except Exception:
            pass
        del eng
    except Exception as e:
        print(f"TTS error: {e}")
    finally:
        if HAVE_PYTHONCOM:
            pythoncom.CoUninitialize()


def tts_worker():
    """Dedicated speech thread. Prints available voices once at startup
    for diagnostics, then speaks each queued utterance with a fresh
    engine instance (see _speak_now)."""
    if HAVE_PYTHONCOM:
        pythoncom.CoInitialize()
    try:
        probe = pyttsx3.init()
        voice_names = [v.name for v in probe.getProperty("voices")]
        print(f"TTS ready. Available voices: {voice_names}")
        del probe
    except Exception as e:
        print(f"TTS FAILED TO INITIALIZE: {e}")
        return
    if HAVE_PYTHONCOM:
        pythoncom.CoUninitialize()

    while True:
        text, done_event = speech_queue.get()
        print(f"VESPER: {text}")
        update_state(status="speaking", response=text)
        push_log(text)
        _speak_now(text)
        update_state(status="idle")
        done_event.set()


def say(text: str):
    """Queue text to be spoken and block until it's done (mirrors the old
    synchronous behavior so callers don't start listening mid-sentence)."""
    done_event = threading.Event()
    speech_queue.put((text, done_event))
    done_event.wait(timeout=15)


recognizer = sr.Recognizer()
mic = sr.Microphone()


def listen_once() -> str:
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        update_state(status="listening")
        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            update_state(status="idle")
            return ""
    update_state(status="processing")
    try:
        text = recognizer.recognize_google(audio)
        update_state(heard=text)
        return text.lower()
    except sr.UnknownValueError:
        update_state(status="idle")
        return ""
    except sr.RequestError:
        say(f"Speech service unreachable, {ADDRESS}. Check your connection.")
        return ""


# ---------------------------------------------------------------------------
# SYSTEM ACTIONS
# ---------------------------------------------------------------------------

def open_app_or_site(name: str):
    name = name.strip().lower()

    for key, url in WEBSITE_MAP.items():
        if key in name:
            webbrowser.open(url)
            say(f"Opening {key}, {ADDRESS}.")
            return

    if name.startswith("http") or ".com" in name or ".org" in name:
        url = name if name.startswith("http") else f"https://{name}"
        webbrowser.open(url)
        say(f"Opening {name}, {ADDRESS}.")
        return

    match = difflib.get_close_matches(name, APP_MAP.keys(), n=1, cutoff=0.5)
    if not match:
        match = [k for k in APP_MAP if k in name or name in k]
    if match:
        key = match[0]
        cmd = APP_MAP[key]
        try:
            if cmd.startswith("start "):
                os.system(cmd)
            else:
                subprocess.Popen(cmd, shell=True)
            say(f"Opening {key}, {ADDRESS}.")
        except Exception:
            say(f"I could not launch {key}, {ADDRESS}.")
        return

    say(f"I don't recognise \"{name}\" as an app, {ADDRESS}.")


def close_app(name: str):
    name = name.strip().lower()
    match = difflib.get_close_matches(name, CLOSE_MAP.keys(), n=1, cutoff=0.5)
    if not match:
        match = [k for k in CLOSE_MAP if k in name or name in k]
    if not match:
        say(f"I don't have a close mapping for \"{name}\", {ADDRESS}.")
        return
    key = match[0]
    proc = CLOSE_MAP[key]
    try:
        subprocess.run(["taskkill", "/IM", proc, "/F"], capture_output=True)
        say(f"Closing {key}, {ADDRESS}. Task done.")
    except Exception:
        say(f"Could not close {key}, {ADDRESS}.")


def search_and_open_file(query: str):
    query = query.strip().lower()
    if not query:
        say(f"Give me a filename to search for, {ADDRESS}.")
        return
    matches = []
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        matches.extend(glob.glob(os.path.join(root, "**", f"*{query}*"), recursive=True))
    matches = [m for m in matches if os.path.isfile(m)]
    if not matches:
        say(f"No file matching \"{query}\" found in your search folders, {ADDRESS}. "
            f"Add more folders to SEARCH_ROOTS if it's somewhere else.")
        return
    target = matches[0]
    try:
        os.startfile(target)
        say(f"Found and opened {os.path.basename(target)}, {ADDRESS}. Task done.")
    except Exception:
        say(f"Found the file but could not open it, {ADDRESS}.")


def search_and_open_folder(query: str):
    query = query.strip().lower()
    if not query:
        say(f"Give me a folder name to search for, {ADDRESS}.")
        return
    matches = []
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        matches.extend(glob.glob(os.path.join(root, "**", f"*{query}*"), recursive=True))
    matches = [m for m in matches if os.path.isdir(m)]
    if not matches:
        say(f"No folder matching \"{query}\" found in your search folders, {ADDRESS}.")
        return
    target = matches[0]
    try:
        os.startfile(target)
        say(f"Found and opened folder {os.path.basename(target)}, {ADDRESS}. Task done.")
    except Exception:
        say(f"Found the folder but could not open it, {ADDRESS}.")


def run_script(name: str):
    name = name.strip().lower()
    if not os.path.isdir(SCRIPTS_DIR):
        say(f"Scripts folder does not exist, {ADDRESS}.")
        return
    candidates = []
    for ext in (".py", ".bat", ".ps1", ".cmd", ".java"):
        candidates.extend(glob.glob(os.path.join(SCRIPTS_DIR, f"*{name}*{ext}")))
    if not candidates:
        say(f"No script matching \"{name}\" found in the scripts folder, {ADDRESS}. "
            f"Drop it into {SCRIPTS_DIR} first.")
        return
    path = candidates[0]

    if path.endswith(".java"):
        run_java_file(path)
        return

    say(f"Running {os.path.basename(path)}, {ADDRESS}.")
    try:
        if path.endswith(".py"):
            subprocess.Popen([sys.executable, path], shell=True)
        elif path.endswith(".ps1"):
            subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", path], shell=True)
        else:
            subprocess.Popen(path, shell=True)
        say(f"Task done, {ADDRESS}.")
    except Exception as e:
        say(f"The script failed to run, {ADDRESS}.")
        print(e)


def run_java_file(path: str):
    """Java needs compiling before it can run. This compiles the .java
    file with javac, then runs the resulting class with java — both must
    be installed and on PATH (part of a JDK, not just a JRE)."""
    folder = os.path.dirname(path)
    filename = os.path.basename(path)
    class_name = os.path.splitext(filename)[0]

    say(f"Compiling {filename}, {ADDRESS}.")
    try:
        compile_result = subprocess.run(
            ["javac", filename], cwd=folder, capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        say(f"Java compiler not found, {ADDRESS}. Install a JDK and make sure javac is on PATH.")
        return
    except subprocess.TimeoutExpired:
        say(f"Compilation timed out, {ADDRESS}.")
        return

    if compile_result.returncode != 0:
        say(f"Compilation failed, {ADDRESS}. Check the terminal for the error.")
        print(compile_result.stderr)
        return

    say(f"Compiled. Running {class_name}, {ADDRESS}.")
    try:
        subprocess.Popen(["java", class_name], cwd=folder, shell=True)
        say(f"Task done, {ADDRESS}.")
    except FileNotFoundError:
        say(f"Java runtime not found, {ADDRESS}. Install a JDK and make sure java is on PATH.")
    except Exception as e:
        say(f"The program failed to run, {ADDRESS}.")
        print(e)


# volume control via virtual media keys (no extra install needed)
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def _press_media_key(vk, taps=1):
    for _ in range(taps):
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)


def volume_up():
    _press_media_key(VK_VOLUME_UP, taps=3)
    say(f"Volume increased, {ADDRESS}.")


def volume_down():
    _press_media_key(VK_VOLUME_DOWN, taps=3)
    say(f"Volume decreased, {ADDRESS}.")


def toggle_mute():
    _press_media_key(VK_VOLUME_MUTE)
    say(f"Toggled mute, {ADDRESS}.")


# task list, shared with the frontend
def add_task(name):
    name = name.strip()
    if not name:
        say(f"No task name given, {ADDRESS}.")
        return
    with state_lock:
        state["tasks"].append({"name": name, "done": False})
    say(f"Logged \"{name}\", {ADDRESS}. I will remind you exactly once.")


def complete_task(name):
    name = name.strip().lower()
    with state_lock:
        target = None
        for t in state["tasks"]:
            if t["name"].lower() == name or name in t["name"].lower():
                target = t
                break
        if target:
            target["done"] = True
    if target:
        say(f"Acceptable. Proceed to the next, {ADDRESS}.")
    else:
        say(f"No matching task found, {ADDRESS}.")


# ---------------------------------------------------------------------------
# COMMAND ROUTING
# ---------------------------------------------------------------------------

def handle(text: str) -> bool:
    if not text:
        return True

    if any(p in text for p in ("shut down", "shutdown", "go to sleep", "goodbye", "exit")):
        say(f"Standing down, {ADDRESS}.")
        return False

    if text.startswith("close ") or text.startswith("quit "):
        close_app(text.split(" ", 1)[1])
        return True

    if text.startswith("open ") or text.startswith("launch "):
        open_app_or_site(text.split(" ", 1)[1])
        return True

    if text.startswith("go to ") or text.startswith("visit "):
        open_app_or_site(text.split(" ", 2)[-1])
        return True

    if "volume up" in text or "increase volume" in text:
        volume_up()
        return True
    if "volume down" in text or "decrease volume" in text or "lower volume" in text:
        volume_down()
        return True
    if "mute" in text:
        toggle_mute()
        return True

    if text.startswith("find file ") or text.startswith("search file ") or text.startswith("search for file "):
        search_and_open_file(text.split("file", 1)[-1])
        return True
    if text.startswith("find folder ") or text.startswith("search folder ") or text.startswith("search for folder "):
        search_and_open_folder(text.split("folder", 1)[-1])
        return True
    if text.startswith("find ") or text.startswith("search "):
        search_and_open_file(text.split(" ", 1)[1])
        return True

    if text.startswith("run script ") or text.startswith("run "):
        run_script(text.split(" ", 1)[-1].replace("script", "", 1).strip())
        return True

    if text.startswith("add task ") or text.startswith("remind me to "):
        add_task(text.split(" ", 2)[-1])
        return True
    if text.startswith("complete ") or text.startswith("finish ") or text.startswith("done with "):
        complete_task(text.split(" ", 1)[1])
        return True

    if "what can you do" in text or text == "help":
        say(f"I open and close apps, control volume, search files, run scripts, "
            f"and track tasks, {ADDRESS}. Just tell me what to do.")
        return True

    say(f"Unclear input, {ADDRESS}. State the command again.")
    return True


def assistant_loop():
    time.sleep(1)
    say(f"Vesper online. Awaiting instruction, {ADDRESS}.")
    running = True
    while running:
        text = listen_once()
        if text:
            running = handle(text)
        else:
            update_state(status="idle")


# ---------------------------------------------------------------------------
# FLASK APP / FRONTEND
# ---------------------------------------------------------------------------

app = Flask(__name__)

FRONTEND = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>VESPER</title>
<style>
  :root {
    --bg: #030608; --cyan: #4be8ff; --cyan-dim: #1b6b78; --red: #ff5c5c; --muted: #5c7f8a;
    --panel: rgba(8,18,22,0.75); --border: #163038;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; min-height: 100vh; background:
      radial-gradient(ellipse at 50% 25%, #06161c 0%, #030608 65%);
    color: #c9e8ee; font-family: 'Consolas', 'Courier New', monospace;
    display: flex; flex-direction: column; align-items: center; padding: 26px 20px 40px;
    position: relative; overflow-x: hidden;
  }
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background-image:
      repeating-linear-gradient(0deg, rgba(75,232,255,0.035) 0px, rgba(75,232,255,0.035) 1px, transparent 1px, transparent 42px),
      repeating-linear-gradient(90deg, rgba(75,232,255,0.035) 0px, rgba(75,232,255,0.035) 1px, transparent 1px, transparent 42px);
  }
  body::after {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 1;
    background: repeating-linear-gradient(0deg, rgba(0,0,0,0) 0px, rgba(0,0,0,0.12) 1px, rgba(0,0,0,0) 3px);
    animation: scan 9s linear infinite;
  }
  @keyframes scan { 0% { background-position-y: 0; } 100% { background-position-y: 400px; } }

  .corner { position: fixed; width: 42px; height: 42px; border: 2px solid var(--cyan-dim); opacity: 0.6; z-index: 2; }
  .corner.tl { top: 18px; left: 18px; border-right: none; border-bottom: none; }
  .corner.tr { top: 18px; right: 18px; border-left: none; border-bottom: none; }
  .corner.bl { bottom: 18px; left: 18px; border-right: none; border-top: none; }
  .corner.br { bottom: 18px; right: 18px; border-left: none; border-top: none; }

  .content { position: relative; z-index: 3; display: flex; flex-direction: column; align-items: center; width: 100%; max-width: 900px; }

  h1 { letter-spacing: 10px; font-weight: 400; color: var(--cyan); margin: 0; font-size: 22px;
    text-shadow: 0 0 12px rgba(75,232,255,0.6); }
  .sub { color: var(--muted); font-size: 10px; letter-spacing: 3px; margin: 4px 0 20px; text-transform: uppercase; }

  /* ---- gauge row ---- */
  .gauge-row { display: flex; justify-content: center; gap: 26px; margin-bottom: 10px; flex-wrap: wrap; }
  .gauge { position: relative; width: 108px; height: 108px; display: flex; align-items: center; justify-content: center; }
  .gauge svg { width: 100%; height: 100%; transform: rotate(-90deg); }
  .gauge circle { fill: none; stroke-width: 5; }
  .gauge circle.bg { stroke: rgba(75,232,255,0.12); }
  .gauge circle.fg { stroke: var(--cyan); stroke-linecap: round; filter: drop-shadow(0 0 4px rgba(75,232,255,0.6));
    transition: stroke-dashoffset 0.6s ease; }
  .gauge-inner { position: absolute; display: flex; flex-direction: column; align-items: center; }
  .gauge-value { font-size: 17px; color: var(--cyan); text-shadow: 0 0 6px rgba(75,232,255,0.5); }
  .gauge-label { font-size: 9px; color: var(--muted); letter-spacing: 2px; margin-top: 2px; text-transform: uppercase; }

  /* ---- core / radar ---- */
  .core-wrap { position: relative; width: 280px; height: 280px; margin: 14px 0 16px; }
  .ring { position: absolute; top: 0; left: 0; width: 100%; height: 100%; border-radius: 50%; }
  .ring.ticks {
    background: repeating-conic-gradient(rgba(75,232,255,0.55) 0deg 1.2deg, transparent 1.2deg 9deg);
    -webkit-mask: radial-gradient(circle, transparent 62%, #000 63%, #000 68%, transparent 69%);
    mask: radial-gradient(circle, transparent 62%, #000 63%, #000 68%, transparent 69%);
    animation: spin 40s linear infinite;
  }
  .ring.thin { inset: 8px; border: 1px solid rgba(75,232,255,0.3); animation: spin 22s linear infinite reverse; }
  .ring.dashed { inset: 34px; border: 1px dashed rgba(75,232,255,0.4); animation: spin 16s linear infinite; }
  .ring.sweep {
    inset: 34px; border-radius: 50%;
    background: conic-gradient(rgba(75,232,255,0.5) 0deg, rgba(75,232,255,0) 55deg, transparent 360deg);
    animation: spin 3.4s linear infinite;
    opacity: 0.8;
  }
  @keyframes spin { from { transform: rotate(0deg);} to { transform: rotate(360deg);} }

  .core {
    position: absolute; inset: 90px; border-radius: 50%;
    background: radial-gradient(circle, rgba(75,232,255,0.4), rgba(75,232,255,0.06) 68%, transparent 75%);
    box-shadow: 0 0 50px rgba(75,232,255,0.4), inset 0 0 30px rgba(75,232,255,0.25);
    display: flex; align-items: center; justify-content: center;
    transition: box-shadow 0.3s, background 0.3s; animation: pulse 2.6s ease-in-out infinite;
    border: 1px solid rgba(75,232,255,0.5);
  }
  .core.listening { animation: pulse 0.85s ease-in-out infinite; box-shadow: 0 0 75px rgba(75,232,255,0.7), inset 0 0 40px rgba(75,232,255,0.4); }
  .core.speaking { animation: pulse 0.45s ease-in-out infinite; box-shadow: 0 0 75px rgba(75,232,255,0.8), inset 0 0 40px rgba(75,232,255,0.5); }
  .core.alert { background: radial-gradient(circle, rgba(255,92,92,0.45), rgba(255,92,92,0.05) 70%); box-shadow: 0 0 70px rgba(255,92,92,0.55); border-color: rgba(255,92,92,0.6); }
  @keyframes pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.05); } }
  .core-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 14px var(--cyan); }

  .status { font-size: 12px; letter-spacing: 4px; text-transform: uppercase; color: var(--muted); margin-bottom: 18px; }
  .status.on { color: var(--cyan); text-shadow: 0 0 8px rgba(75,232,255,0.6); }

  .bars { display: flex; gap: 3px; align-items: flex-end; height: 30px; margin-bottom: 24px; }
  .bar { width: 3px; background: var(--cyan); border-radius: 1px; height: 3px; transition: height 0.1s; box-shadow: 0 0 6px rgba(75,232,255,0.7); }

  /* ---- panels ---- */
  .panels-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; width: 100%; max-width: 760px; }
  @media (max-width: 640px) { .panels-grid { grid-template-columns: 1fr; } }

  .panel {
    background: var(--panel); border: 1px solid var(--border);
    padding: 16px 20px; backdrop-filter: blur(4px);
    clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px));
  }
  .panel.full { grid-column: 1 / -1; }
  .panel h2 { font-size: 10px; text-transform: uppercase; letter-spacing: 3px; color: var(--muted); margin: 0 0 10px; }
  .line { font-style: italic; color: var(--cyan); font-size: 13px; min-height: 18px; text-shadow: 0 0 6px rgba(75,232,255,0.3); }
  .heard { color: var(--muted); font-size: 11px; margin-top: 6px; }

  .tasks { list-style: none; margin: 0; padding: 0; max-height: 140px; overflow-y: auto; }
  .tasks li { display: flex; align-items: center; gap: 10px; padding: 5px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
  .tasks li.done { color: var(--muted); text-decoration: line-through; }
  .dot-sm { width: 6px; height: 6px; border-radius: 50%; background: var(--cyan); flex-shrink: 0; box-shadow: 0 0 6px var(--cyan); }
  .tasks li.done .dot-sm { background: var(--border); box-shadow: none; }
  .empty { color: var(--muted); font-size: 12px; }

  .log { font-size: 10.5px; color: var(--muted); line-height: 1.9; max-height: 140px; overflow-y: auto; }

  /* ---- quick command bar ---- */
  .quickbar { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin: 18px 0 16px; max-width: 760px; }
  .qbtn {
    background: rgba(75,232,255,0.06); border: 1px solid var(--cyan-dim); color: var(--cyan);
    padding: 8px 14px; font-family: inherit; font-size: 11px; letter-spacing: 1px; text-transform: uppercase;
    cursor: pointer; clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
  }
  .qbtn:hover { background: rgba(75,232,255,0.16); border-color: var(--cyan); }

  .input-row { display: flex; gap: 8px; width: 100%; max-width: 760px; }
  .input-row input {
    flex: 1; background: var(--panel); border: 1px solid var(--border); color: #c9e8ee;
    padding: 10px 14px; font-size: 13px; font-family: inherit;
    clip-path: polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 10px 100%, 0 calc(100% - 10px));
  }
  .input-row button {
    background: rgba(75,232,255,0.08); border: 1px solid var(--cyan); color: var(--cyan);
    padding: 10px 20px; cursor: pointer; font-family: inherit; letter-spacing: 2px; text-transform: uppercase; font-size: 12px;
  }
  .input-row button:hover { background: rgba(75,232,255,0.18); }
</style></head>
<body>
  <div class="corner tl"></div><div class="corner tr"></div>
  <div class="corner bl"></div><div class="corner br"></div>

  <div class="content">
    <h1>VESPER</h1>
    <div class="sub">personal ai system</div>

    <div class="gauge-row" id="gaugeRow"></div>

    <div class="core-wrap">
      <div class="ring ticks"></div>
      <div class="ring thin"></div>
      <div class="ring dashed"></div>
      <div class="ring sweep"></div>
      <div class="core" id="core"><div class="core-dot"></div></div>
    </div>

    <div class="status" id="statusText">idle</div>
    <div class="bars" id="bars"></div>

    <div class="quickbar">
      <button class="qbtn" onclick="quickCmd('what are my tasks')">status</button>
      <button class="qbtn" onclick="quickCmd('open chrome')">open chrome</button>
      <button class="qbtn" onclick="quickCmd('open notepad')">open notepad</button>
      <button class="qbtn" onclick="quickCmd('volume up')">vol +</button>
      <button class="qbtn" onclick="quickCmd('volume down')">vol -</button>
      <button class="qbtn" onclick="quickCmd('mute')">mute</button>
      <button class="qbtn" onclick="quickCmd('what can you do')">help</button>
    </div>

    <div class="panels-grid">
      <div class="panel full">
        <h2>Last response</h2>
        <div class="line" id="responseLine">""</div>
        <div class="heard" id="heardLine"></div>
      </div>

      <div class="panel">
        <h2>Tasks</h2>
        <ul class="tasks" id="taskList"><li class="empty">nothing yet</li></ul>
      </div>

      <div class="panel">
        <h2>Activity log</h2>
        <div class="log" id="logBox"></div>
      </div>
    </div>

    <div class="input-row" style="margin-top:16px;">
      <input id="cmdInput" placeholder="type a command, e.g. open chrome" />
      <button onclick="sendCmd()">send</button>
    </div>
  </div>

<script>
const core = document.getElementById('core');
const statusText = document.getElementById('statusText');
const responseLine = document.getElementById('responseLine');
const heardLine = document.getElementById('heardLine');
const taskList = document.getElementById('taskList');
const logBox = document.getElementById('logBox');
const barsEl = document.getElementById('bars');
const gaugeRow = document.getElementById('gaugeRow');

for (let i = 0; i < 28; i++) {
  const b = document.createElement('div');
  b.className = 'bar';
  barsEl.appendChild(b);
}
let barTimer = null;

function animateBars(active) {
  if (barTimer) clearInterval(barTimer);
  if (!active) {
    document.querySelectorAll('.bar').forEach(b => b.style.height = '3px');
    return;
  }
  barTimer = setInterval(() => {
    document.querySelectorAll('.bar').forEach(b => {
      b.style.height = (3 + Math.random() * 27) + 'px';
    });
  }, 90);
}

const R = 48;
const CIRC = 2 * Math.PI * R;

const gaugeDefs = [
  { id: 'gCpu', label: 'CPU' },
  { id: 'gRam', label: 'RAM' },
  { id: 'gDisk', label: 'DISK' },
  { id: 'gTime', label: 'TIME' },
];

gaugeDefs.forEach(g => {
  const wrap = document.createElement('div');
  wrap.className = 'gauge';
  wrap.innerHTML =
    '<svg viewBox="0 0 120 120">' +
      '<circle class="bg" cx="60" cy="60" r="' + R + '"></circle>' +
      '<circle class="fg" id="' + g.id + '_ring" cx="60" cy="60" r="' + R + '" ' +
        'stroke-dasharray="' + CIRC + '" stroke-dashoffset="' + CIRC + '"></circle>' +
    '</svg>' +
    '<div class="gauge-inner">' +
      '<div class="gauge-value" id="' + g.id + '_val">--</div>' +
      '<div class="gauge-label">' + g.label + '</div>' +
    '</div>';
  gaugeRow.appendChild(wrap);
});

function setGauge(id, pct, text) {
  const ring = document.getElementById(id + '_ring');
  const val = document.getElementById(id + '_val');
  const offset = CIRC * (1 - Math.max(0, Math.min(100, pct)) / 100);
  ring.style.strokeDashoffset = offset;
  val.textContent = text;
}

async function poll() {
  try {
    const res = await fetch('/state');
    const s = await res.json();

    core.className = 'core';
    statusText.className = 'status';
    if (s.status === 'listening') { core.classList.add('listening'); statusText.classList.add('on'); animateBars(true); }
    else if (s.status === 'speaking') { core.classList.add('speaking'); statusText.classList.add('on'); animateBars(true); }
    else if (s.status === 'processing') { statusText.classList.add('on'); animateBars(false); }
    else { animateBars(false); }

    statusText.textContent = s.status;
    responseLine.textContent = '"' + s.response + '"';
    heardLine.textContent = s.heard ? ('heard: "' + s.heard + '"') : '';

    if (s.tasks.length === 0) {
      taskList.innerHTML = '<li class="empty">nothing yet</li>';
    } else {
      taskList.innerHTML = s.tasks.map(t =>
        '<li class="' + (t.done ? 'done' : '') + '"><span class="dot-sm"></span><span>' + t.name + '</span></li>'
      ).join('');
    }

    logBox.innerHTML = s.log.map(l => '<div>' + l + '</div>').join('');

    if (s.sys) {
      setGauge('gCpu', s.sys.cpu, Math.round(s.sys.cpu) + '%');
      setGauge('gRam', s.sys.ram, Math.round(s.sys.ram) + '%');
      setGauge('gDisk', s.sys.disk, Math.round(s.sys.disk) + '%');
      setGauge('gTime', 100, s.sys.time);
    }
  } catch (e) {}
}
setInterval(poll, 1000);
poll();

async function sendCommandText(text) {
  await fetch('/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
}

function quickCmd(text) { sendCommandText(text); }

async function sendCmd() {
  const input = document.getElementById('cmdInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendCommandText(text);
}
document.getElementById('cmdInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendCmd();
});
</script>
</body></html>
"""


def get_sys_stats():
    now = datetime.now()
    stats = {
        "time": now.strftime("%H:%M"),
        "day_num": now.strftime("%d"),
        "month": now.strftime("%b").upper(),
        "weekday": now.strftime("%A"),
        "cpu": 0,
        "ram": 0,
        "disk": 0,
    }
    if HAVE_PSUTIL:
        try:
            stats["cpu"] = psutil.cpu_percent(interval=0.0)
            stats["ram"] = psutil.virtual_memory().percent
            disk_path = "C:\\" if os.name == "nt" else "/"
            stats["disk"] = psutil.disk_usage(disk_path).percent
        except Exception:
            pass
    return stats


@app.route("/")
def index():
    return Response(FRONTEND, mimetype="text/html")


@app.route("/state")
def get_state():
    with state_lock:
        payload = dict(state)
    payload["sys"] = get_sys_stats()
    return jsonify(payload)


@app.route("/command", methods=["POST"])
def post_command():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip().lower()
    threading.Thread(target=handle, args=(text,), daemon=True).start()
    return jsonify({"ok": True})


def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    threading.Thread(target=tts_worker, daemon=True).start()
    threading.Thread(target=assistant_loop, daemon=True).start()
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
