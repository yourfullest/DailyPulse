$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

python -m pip install --upgrade pip
python -m pip install pyinstaller certifi customtkinter

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name DailyPulse `
  --icon assets\DailyPulse.ico `
  --add-data "config.example.json;." `
  --add-data "config.deepseek.example.json;." `
  --add-data ".env.example;." `
  --add-data "assets;assets" `
  daily_pulse_app.py

Compress-Archive -Path dist\DailyPulse.exe, README.md -DestinationPath dist\DailyPulse-Windows.zip -Force

Write-Host "Built dist\DailyPulse-Windows.zip"
