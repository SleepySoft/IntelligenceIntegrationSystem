#!/usr/bin/env bash
# dashboard:title=系统资源监控

set -euo pipefail

if command -v btop >/dev/null 2>&1; then
    exec btop
fi

while true; do
    clear
    top -b -n 1 | head -n 30
    sleep 2
done
