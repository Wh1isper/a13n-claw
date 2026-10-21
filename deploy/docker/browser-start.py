"""A shared visible browser: CDP automation and noVNC operate the same profile."""

import fcntl
import json
import os
import signal
import subprocess
import time
from pathlib import Path

os.environ["DISPLAY"] = ":99"
profile = Path("/browser-profile")
# This deployment owns the profile exclusively, including after container crashes.
# Hold the kernel lock in children too, so a surviving process retains ownership.
owner = (profile / ".claw-owner").open("a")
fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
    (profile / name).unlink(missing_ok=True)
# /tmp belongs to this dedicated container and can survive docker restart.
for name in ("/tmp/.X99-lock", "/tmp/.X11-unix/X99"):
    Path(name).unlink(missing_ok=True)
preferences = profile / "Default" / "Preferences"
preferences.parent.mkdir(parents=True, exist_ok=True)
value = json.loads(preferences.read_text()) if preferences.exists() else {}
value.setdefault("download", {}).update(
    default_directory="/downloads", prompt_for_download=False, directory_upgrade=True
)
preferences.write_text(json.dumps(value))
children = []
stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    children.append(
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
            pass_fds=(owner.fileno(),),
        )
    )
    for _ in range(100):
        if stopping or children[0].poll() is not None:
            raise SystemExit("Display startup interrupted")
        if (
            subprocess.run(
                ["xdpyinfo"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            == 0
        ):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("Display did not become ready")
    commands = [
        ["openbox"],
        [
            "chromium",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=9222",
            "--user-data-dir=/browser-profile",
            "about:blank",
        ],
        [
            "x11vnc",
            "-display",
            ":99",
            "-forever",
            "-shared",
            "-localhost",
            "-nopw",
            "-rfbport",
            "5900",
        ],
        ["websockify", "--web=/usr/share/novnc", "0.0.0.0:6080", "127.0.0.1:5900"],
    ]
    children.extend(subprocess.Popen(command, pass_fds=(owner.fileno(),)) for command in commands)
    while not stopping:
        if any(child.poll() is not None for child in children):
            raise SystemExit("A required browser process exited")
        time.sleep(0.5)
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
