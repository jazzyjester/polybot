#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
( sleep 2 && open "http://127.0.0.1:8765/" ) &
python3 -m polybot.web
