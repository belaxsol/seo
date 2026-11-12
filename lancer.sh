#!/bin/sh
cd "$(dirname "$0")"
echo "Installation (la première fois : téléchargement du navigateur, quelques minutes)..."
python3 -m pip install -q -r requirements.txt
python3 -m playwright install chromium
python3 app.py
