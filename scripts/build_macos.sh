#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller certifi

rm -rf build dist

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name DailyPulse \
  --icon assets/DailyPulse.icns \
  --add-data "config.example.json:." \
  --add-data "config.deepseek.example.json:." \
  --add-data ".env.example:." \
  --add-data "assets:assets" \
  daily_pulse_app.py

hdiutil create \
  -volname DailyPulse \
  -srcfolder dist/DailyPulse.app \
  -ov \
  -format UDZO \
  dist/DailyPulse-macOS.dmg

echo "Built dist/DailyPulse-macOS.dmg"
