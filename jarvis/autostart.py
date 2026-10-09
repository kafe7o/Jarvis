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


def make_icon(path: Path) -> Path | None:
    """Draw the Jarvis arc-reactor icon (.ico on Windows, .png elsewhere). Needs Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return None
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 248, 248), fill=(7, 12, 22, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((58, 58, 198, 198), fill=(79, 209, 255, 150))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(28)))
    draw = ImageDraw.Draw(img)
    draw.ellipse((28, 28, 228, 228), outline=(79, 209, 255, 255), width=10)
    for i in range(10):  # segmented middle ring
        draw.arc((56, 56, 200, 200), i * 36 + 6, i * 36 + 30, fill=(140, 230, 255, 255), width=14)
    draw.ellipse((96, 96, 160, 160), fill=(220, 248, 255, 255), outline=(79, 209, 255, 255), width=6)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".ico":
        img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        img.save(path)
    return path


def shortcut(workdir: Path) -> str:
    """Put a "Jarvis" icon on the desktop (and in the Start menu) that opens the app window."""
    python = Path(sys.executable)
    home = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
    if sys.platform.startswith("win"):
        pythonw = python.with_name("pythonw.exe")
        target = pythonw if pythonw.exists() else python
        icon = make_icon(home / "jarvis.ico")

        def q(value) -> str:  # PowerShell single-quoted string
            return "'" + str(value).replace("'", "''") + "'"

        ps = (
            "$s = New-Object -ComObject WScript.Shell; "
            "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { "
            "$l = $s.CreateShortcut((Join-Path $dir 'Jarvis.lnk')); "
            f"$l.TargetPath = {q(target)}; $l.Arguments = '-m jarvis app'; "
            f"$l.WorkingDirectory = {q(workdir)}; $l.Description = 'J.A.R.V.I.S.'; "
            + (f"$l.IconLocation = {q(icon)}; " if icon else "")
            + "$l.Save() }"
        )
        done = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
        if done.returncode != 0:
            return f"Не успях да направя иконата: {done.stderr.strip()[:200]}"
        return "Иконата Jarvis е на работния плот и в менюто Start."
    if sys.platform.startswith("linux"):
        icon = make_icon(home / "jarvis.png")
        entry = (
            "[Desktop Entry]\nType=Application\nName=Jarvis\nComment=J.A.R.V.I.S.\n"
            f"Exec=sh -c 'cd \"{workdir}\" && \"{python}\" -m jarvis app'\n"
            + (f"Icon={icon}\n" if icon else "")
            + "Terminal=false\nCategories=Utility;\n"
        )
        apps = Path.home() / ".local/share/applications/jarvis.desktop"
        apps.parent.mkdir(parents=True, exist_ok=True)
        apps.write_text(entry, encoding="utf-8")
        apps.chmod(0o755)
        return f"Jarvis е добавен в менюто с приложения ({apps})."
    if sys.platform == "darwin":
        command = Path.home() / "Desktop/Jarvis.command"
        command.write_text(f'#!/bin/sh\ncd "{workdir}" && exec "{python}" -m jarvis app\n', encoding="utf-8")
        command.chmod(0o755)
        return f"Иконата Jarvis е на работния плот ({command})."
    return "Иконите не се поддържат на тази система."
