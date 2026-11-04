"""
run.py — one-step launcher for Fetch.

    python run.py            # set up (first time only), start the server, open the browser
    python run.py --no-browser

On first run it creates a .venv next to this file and installs the dependencies
into it, so nothing is installed globally.  Later runs start straight away.
Press Ctrl+C (or close the window) to stop the server.

Double-click wrappers: Fetch.bat (Windows), run.sh (macOS / Linux).
"""
from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PORT = 5000                       # must match the port in server.py
URL = f"http://localhost:{PORT}"
DEPS = ["flask>=3.0", "pypdf>=4.0", "bcrypt>=4.0"]
STAMP = VENV / ".deps-installed"  # records which dependency list is installed


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def ensure_environment() -> Path:
    if sys.version_info < (3, 10):
        sys.exit(f"Fetch needs Python 3.10 or newer (this is {sys.version.split()[0]}).")
    py = venv_python()
    if not py.exists():
        print("First run: creating a private Python environment in .venv ...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    want = hashlib.sha256("\n".join(DEPS).encode()).hexdigest()
    if not STAMP.exists() or STAMP.read_text().strip() != want:
        print("Installing dependencies (one time, needs internet) ...")
        r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *DEPS])
        if r.returncode:
            sys.exit("Could not install the dependencies. Check your internet connection and try again.")
        STAMP.write_text(want)
    return py


def main(argv: list[str]) -> int:
    open_browser = "--no-browser" not in argv
    if port_open():
        print(f"Something is already running on port {PORT} (probably Fetch). Opening it.")
        if open_browser:
            webbrowser.open(URL)
        return 0

    py = ensure_environment()
    print("Starting Fetch ...")
    proc = subprocess.Popen([str(py), "server.py"], cwd=ROOT)
    try:
        for _ in range(60):                       # wait up to ~15 s for the server
            if proc.poll() is not None:
                print("The server stopped unexpectedly; see the messages above.")
                return proc.returncode or 1
            if port_open():
                break
            time.sleep(0.25)
        else:
            print("The server did not start in time; see the messages above.")
            proc.terminate()
            return 1
        print(f"\nFetch is running at {URL}  (press Ctrl+C to stop)\n")
        if open_browser:
            webbrowser.open(URL)
        return proc.wait()
    except KeyboardInterrupt:
        print("\nStopping Fetch ...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
