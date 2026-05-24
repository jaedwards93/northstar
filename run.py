"""
Start mock Northstar (:8001) and middleware (:8000) for local demo.

From repo root (venv activated):

  python run.py

Then in another terminal:

  python harness/simulate.py

Press Ctrl+C here to stop servers this script started.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time

from harness.simulate import check_services

HEALTH_TIMEOUT_SEC = 30

_procs: list[subprocess.Popen[bytes]] = []
_we_started = False


def _spawn(args: list[str], *, extra_env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(args, env=env)
    _procs.append(proc)
    return proc


def start_services() -> None:
    global _we_started

    if check_services() is None:
        print("Services already running on :8001 and :8000.")
        return

    print("Starting mock Northstar (:8001) and middleware (:8000)...")
    py = sys.executable
    _spawn([py, "-m", "uvicorn", "mock_northstar.app.main:app", "--host", "127.0.0.1", "--port", "8001"])
    _spawn([py, "-m", "uvicorn", "middleware.app.main:app", "--host", "127.0.0.1", "--port", "8000"])
    _we_started = True

    deadline = time.time() + HEALTH_TIMEOUT_SEC
    while time.time() < deadline:
        if check_services() is None:
            print("Services ready. Open http://127.0.0.1:8000/console/")
            print("Run harness in another terminal: python harness/simulate.py")
            return
        time.sleep(0.5)

    stop_services()
    print("Timed out waiting for services.", file=sys.stderr)
    sys.exit(1)


def stop_services() -> None:
    for proc in _procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in _procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    _procs.clear()


def _on_exit() -> None:
    if _we_started:
        print("\nStopping services...")
        stop_services()


def wait_for_interrupt() -> None:
    print("Press Ctrl+C to stop servers started by this script.")
    try:
        while True:
            time.sleep(0.5)
            for proc in _procs:
                if proc.poll() is not None:
                    print(f"Service exited unexpectedly (code {proc.returncode}).", file=sys.stderr)
                    sys.exit(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    atexit.register(_on_exit)
    start_services()
    if _we_started:
        wait_for_interrupt()
    else:
        print("Use another terminal for: python harness/simulate.py")


if __name__ == "__main__":
    main()
