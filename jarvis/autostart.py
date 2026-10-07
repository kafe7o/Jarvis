"""Start Jarvis automatically at login (`jarvis daemon`) on Linux, macOS and Windows."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def install(workdir: Path) -> str:
    python = sys.executable
    if sys.platform.startswith("linux"):
        unit = Path.home() / ".config/systemd/user/jarvis.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "[Unit]\nDescription=J.A.R.V.I.S. personal assistant\nAfter=network-online.target\n\n"
            f"[Service]\nWorkingDirectory={workdir}\nExecStart={python} -m jarvis daemon\nRestart=always\nRestartSec=5\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "jarvis.service"], check=False)
        return f"Автостарт: {unit} (спиране: systemctl --user disable --now jarvis)"
    if sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/com.jarvis.assistant.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            "<key>Label</key><string>com.jarvis.assistant</string>\n"
            f"<key>ProgramArguments</key><array><string>{python}</string><string>-m</string><string>jarvis</string><string>daemon</string></array>\n"
            f"<key>WorkingDirectory</key><string>{workdir}</string>\n"
            "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>\n"
            f"<key>StandardErrorPath</key><string>{Path.home()}/.jarvis/daemon.log</string>\n"
            "</dict></plist>\n",
            encoding="utf-8",
        )
        subprocess.run(["launchctl", "load", "-w", str(plist)], check=False)
        return f"Автостарт: {plist}"
    if sys.platform.startswith("win"):
        startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
        pythonw = Path(python).with_name("pythonw.exe")
        script = startup / "jarvis.vbs"
        script.write_text(
            'Set sh = CreateObject("WScript.Shell")\n'
            f'sh.CurrentDirectory = "{workdir}"\n'
            f'sh.Run """{pythonw if pythonw.exists() else python}"" -m jarvis daemon", 0, False\n',
            encoding="utf-8",
        )
        return f"Автостарт: {script}"
    return "Автостартът не се поддържа на тази система."
