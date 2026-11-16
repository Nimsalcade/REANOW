#!/usr/bin/env bash
# Keep the recorder running; restart on exit.
cd "$(dirname "$0")"
export NODE_USE_ENV_PROXY=1 NODE_NO_WARNINGS=1
export NODE_EXTRA_CA_CERTS="${NODE_EXTRA_CA_CERTS:-/root/.ccr/ca-bundle.crt}"
while true; do
  node recorder.cjs >> data/recorder.log 2>&1
  echo "$(date -u +%FT%TZ) recorder exited $?, restarting" >> data/recorder.log
  sleep 2
done
