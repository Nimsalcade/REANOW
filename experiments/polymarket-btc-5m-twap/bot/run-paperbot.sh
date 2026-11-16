#!/usr/bin/env bash
# Keep the real-time paper bot running; restart on exit.
cd "$(dirname "$0")"
while true; do
  python3 -I paperbot.py >> data/paperbot.log 2>&1
  echo "$(date -u +%FT%TZ) paperbot exited $?, restarting" >> data/paperbot.log
  sleep 2
done
