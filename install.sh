#!/usr/bin/env bash
# One-step install of J.A.R.V.I.S. on Linux or macOS:  bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "Нужен е Python 3.10+ (python.org)."; exit 1; }

if [[ "$(uname)" == "Darwin" ]]; then
  command -v brew >/dev/null && brew install ffmpeg portaudio android-platform-tools || true
elif command -v apt-get >/dev/null; then
  sudo apt-get install -y python3-venv ffmpeg libportaudio2 adb || true
fi

python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[all]"
python -m playwright install chromium

jarvis setup
echo
echo "Пусни го с:  . .venv/bin/activate && jarvis serve"
