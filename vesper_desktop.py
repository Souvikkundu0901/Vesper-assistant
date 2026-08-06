"""
VESPER — personal desktop voice assistant (Windows)
-----------------------------------------------------
Runs locally on your machine. Listens for voice commands and acts on
your own computer: opens apps/websites, searches for files, and runs
scripts you point it at.

SETUP (run once in Command Prompt / PowerShell):
    pip install SpeechRecognition pyttsx3 pywin32 pipwin
    pipwin install pyaudio

    (pyaudio is the tricky one on Windows — pipwin installs a
    precompiled wheel. If pipwin fails, download the matching .whl
    from https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio and
    `pip install` that file directly.)

RUN:
    python vesper_desktop.py

CONFIGURE:
    Edit APP_MAP and SEARCH_ROOTS below to match your setup.
"""

import os
import sys
import glob
import subprocess
import webbrowser
import difflib

import speech_recognition as sr
import pyttsx3

# ---------------------------------------------------------------------------
# CONFIG — edit this section for your machine
# ---------------------------------------------------------------------------

# Friendly name -> what to launch. Values can be:
#   - a plain executable/command name Windows can resolve (e.g. "notepad")
#   - a full path to an .exe
#   - a URL (handled automatically as a website)
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

WEBSITE_MAP = {
    "youtube": "https://youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "google": "https://google.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
}

# Folders Vesper is allowed to search for files in. Add/remove as needed.
SEARCH_ROOTS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
]

# Folder where "run script [name]" looks for .py / .bat / .ps1 files.
SCRIPTS_DIR = os.path.join(os.path.expanduser("~"), "VesperScripts")

WAKE_WORD = None  # e.g. "vesper" to require "vesper, open notepad". None = always listening per command.

# ---------------------------------------------------------------------------
# VOICE
# ---------------------------------------------------------------------------

engine = pyttsx3.init()
engine.setProperty("rate", 168)
# Try to select a lower-pitched / male-sounding voice if available
for v in engine.getProperty("voices"):
    if "david" in v.name.lower() or "male" in v.name.lower():
        engine.setProperty("voice", v.id)
        break


def say(text: str):
    print(f"VESPER: {text}")
    engine.say(text)
    engine.runAndWait()


recognizer = sr.Recognizer()
mic = sr.Microphone()


def listen() -> str:
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        print("...listening...")
        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            return ""
    try:
        text = recognizer.recognize_google(audio)
        print(f"you said: {text}")
        return text.lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        say("Speech service unreachable. Check your connection.")
        return ""


# ---------------------------------------------------------------------------
# ACTIONS
# ---------------------------------------------------------------------------

def open_app_or_site(name: str):
    name = name.strip().lower()

    # website by name
    for key, url in WEBSITE_MAP.items():
        if key in name:
            webbrowser.open(url)
            say(f"Opening {key}.")
            return

    # direct URL spoken
    if name.startswith("http") or ".com" in name or ".org" in name:
        url = name if name.startswith("http") else f"https://{name}"
        webbrowser.open(url)
        say(f"Opening {name}.")
        return

    # known app, exact or fuzzy match
    match = difflib.get_close_matches(name, APP_MAP.keys(), n=1, cutoff=0.5)
    if not match:
        # substring match fallback
        match = [k for k in APP_MAP if k in name or name in k]
    if match:
        key = match[0]
        cmd = APP_MAP[key]
        try:
            if cmd.startswith("start "):
                os.system(cmd)
            else:
                subprocess.Popen(cmd, shell=True)
            say(f"Opening {key}.")
        except Exception:
            say(f"I could not launch {key}. Check the app is installed and on PATH.")
        return

    say(f"I don't recognise \"{name}\" as an app. Add it to APP_MAP if it exists on this machine.")


def search_and_open_file(query: str):
    query = query.strip().lower()
    if not query:
        say("Give me a filename to search for.")
        return

    matches = []
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        pattern = os.path.join(root, "**", f"*{query}*")
        matches.extend(glob.glob(pattern, recursive=True))

    matches = [m for m in matches if os.path.isfile(m)]

    if not matches:
        say(f"No file matching \"{query}\" found in your search folders.")
        return

    target = matches[0]
    try:
        os.startfile(target)
        say(f"Found and opened {os.path.basename(target)}.")
    except Exception:
        say("Found the file but could not open it.")


def run_script(name: str):
    name = name.strip().lower()
    if not os.path.isdir(SCRIPTS_DIR):
        say(f"Scripts folder does not exist. Create {SCRIPTS_DIR} and place scripts there.")
        return

    candidates = []
    for ext in (".py", ".bat", ".ps1", ".cmd"):
        candidates.extend(glob.glob(os.path.join(SCRIPTS_DIR, f"*{name}*{ext}")))

    if not candidates:
        say(f"No script matching \"{name}\" found in the scripts folder.")
        return

    path = candidates[0]
    say(f"Running {os.path.basename(path)}.")
    try:
        if path.endswith(".py"):
            subprocess.Popen([sys.executable, path], shell=True)
        elif path.endswith(".ps1"):
            subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", path], shell=True)
        else:
            subprocess.Popen(path, shell=True)
    except Exception as e:
        say("The script failed to run.")
        print(e)


# ---------------------------------------------------------------------------
# COMMAND ROUTING
# ---------------------------------------------------------------------------

def handle(text: str) -> bool:
    """Return False to signal shutdown."""
    if not text:
        return True

    if any(p in text for p in ("shut down", "shutdown", "go to sleep", "goodbye", "exit")):
        say("Standing down.")
        return False

    if text.startswith("open "):
        open_app_or_site(text[len("open "):])
        return True

    if text.startswith("launch "):
        open_app_or_site(text[len("launch "):])
        return True

    if text.startswith("go to ") or text.startswith("visit "):
        target = text.split(" ", 2)[-1]
        open_app_or_site(target)
        return True

    if text.startswith("find file ") or text.startswith("search file ") or text.startswith("search for file "):
        query = text.split("file", 1)[-1]
        search_and_open_file(query)
        return True

    if text.startswith("find ") or text.startswith("search "):
        query = text.split(" ", 1)[-1]
        search_and_open_file(query)
        return True

    if text.startswith("run script ") or text.startswith("run "):
        name = text.split(" ", 1)[-1].replace("script", "", 1).strip()
        run_script(name)
        return True

    if "what can you do" in text or "help" in text:
        say("I open apps and websites, search your files, and run scripts. Say open, "
            "find, or run, followed by a name.")
        return True

    say("Unclear input. State the command again.")
    return True


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    say("Vesper online. Awaiting instruction.")
    running = True
    while running:
        text = listen()
        if WAKE_WORD and WAKE_WORD not in text:
            continue
        if WAKE_WORD:
            text = text.replace(WAKE_WORD, "", 1).strip()
        running = handle(text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("Interrupted. Standing down.")
