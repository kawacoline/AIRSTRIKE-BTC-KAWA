"""
auto_updater.py
Background auto-updater daemon for Airstrike BTC Kawa.

Runs as a daemon thread inside the main bot process:
  - Every 30 seconds: git pull --ff-only
  - If requirements.txt changed: pip install -r requirements.txt --quiet
  - Logs all activity via callback
  - Thread-safe, non-blocking, catches all errors
"""
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional


# How often to check for updates (seconds)
PULL_INTERVAL = 30

# Project root — where .git lives
PROJECT_ROOT = Path(__file__).parent.resolve()
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"


def _file_hash(filepath: Path) -> str:
    """Get SHA-256 hash of a file, or empty string if file doesn't exist."""
    if not filepath.exists():
        return ""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def _run_cmd(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


class AutoUpdater:
    """
    Background daemon that polls git for updates and installs new dependencies.
    
    Usage:
        updater = AutoUpdater(log_callback=my_log_func)
        updater.start()   # starts background thread
        updater.stop()    # stops gracefully
    """

    def __init__(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
        interval: int = PULL_INTERVAL,
    ):
        self.log_callback = log_callback
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_req_hash: str = _file_hash(REQUIREMENTS_FILE)
        self.last_pull_status: str = "Pending..."
        self.last_pull_time: Optional[float] = None
        self.pulls_with_updates: int = 0

    def _log(self, msg: str):
        full_msg = f"[AutoUpdater] {msg}"
        if self.log_callback:
            self.log_callback(full_msg)
        else:
            print(full_msg)

    def start(self):
        """Start the background updater thread."""
        if self._thread and self._thread.is_alive():
            self._log("Already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="AutoUpdater",
            daemon=True,
        )
        self._thread.start()
        self._log(f"Started — polling every {self.interval}s")

    def stop(self):
        """Signal the updater thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._log("Stopped.")

    def _loop(self):
        """Main loop — runs in background thread."""
        # Wait a bit on startup so the bot can finish initializing
        self._stop_event.wait(5)

        while not self._stop_event.is_set():
            try:
                self._do_pull()
            except Exception as e:
                self._log(f"Unexpected error: {e}")
                self.last_pull_status = f"Error: {str(e)[:40]}"

            # Sleep in small increments so we can respond to stop quickly
            for _ in range(self.interval):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _do_pull(self):
        """Execute a single git pull cycle."""
        # Snapshot requirements hash BEFORE pull
        hash_before = _file_hash(REQUIREMENTS_FILE)

        # Run git pull --ff-only
        code, stdout, stderr = _run_cmd(
            ["git", "pull", "--ff-only"],
            cwd=PROJECT_ROOT,
        )
        self.last_pull_time = time.time()

        if code != 0:
            error_msg = stderr or stdout or "Unknown error"
            # Don't spam logs for common non-errors
            if "not a git repository" in error_msg.lower():
                self.last_pull_status = "Not a git repo"
                self._log("Not a git repository — auto-update disabled")
                self._stop_event.set()  # Stop polling
                return
            self.last_pull_status = f"Pull failed: {error_msg[:40]}"
            self._log(f"git pull failed: {error_msg}")
            return

        # Check if anything was updated
        if "already up to date" in stdout.lower() or "already up-to-date" in stdout.lower():
            self.last_pull_status = "Up to date"
            return

        # New code was pulled!
        self.pulls_with_updates += 1
        self._log(f"New code pulled: {stdout[:80]}")
        self.last_pull_status = "Updated!"

        # Check if requirements.txt changed
        hash_after = _file_hash(REQUIREMENTS_FILE)
        if hash_after != hash_before and hash_after:
            self._log("requirements.txt changed — installing new dependencies...")
            pip_code, pip_out, pip_err = _run_cmd(
                [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE), "--quiet"],
                cwd=PROJECT_ROOT,
            )
            if pip_code == 0:
                self._log("Dependencies updated successfully.")
                self.last_pull_status = "Updated + deps installed"
            else:
                self._log(f"pip install failed: {pip_err[:80]}")
                self.last_pull_status = "Updated (pip failed)"
        else:
            self._log("No dependency changes.")

        self._log("Restarting bot to apply new updates...")
        time.sleep(2)
        # Use os.execv to completely replace the current process with a fresh one
        os.execv(sys.executable, ['python'] + sys.argv)


if __name__ == "__main__":
    # Standalone test
    def test_log(msg):
        print(f"  {time.strftime('%H:%M:%S')} {msg}")

    print("Testing AutoUpdater (will run 2 cycles)...")
    updater = AutoUpdater(log_callback=test_log, interval=5)
    updater.start()
    time.sleep(15)
    updater.stop()
    print(f"Status: {updater.last_pull_status}")
    print(f"Updates found: {updater.pulls_with_updates}")
