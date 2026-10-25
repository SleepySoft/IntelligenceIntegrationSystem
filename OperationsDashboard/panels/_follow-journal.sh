#!/usr/bin/env bash

set -euo pipefail

unit="${1:?systemd unit is required}"
label="${2:-$unit}"
waiting_message_shown=0

while true; do
    load_state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null || true)"
    if [[ -n "$load_state" && "$load_state" != "not-found" ]]; then
        break
    fi
    if (( waiting_message_shown == 0 )); then
        printf '%s log\n\n' "$label"
        printf "Unit '%s' is not installed. Waiting for it to become available...\n" "$unit"
        printf 'This view checks again every 5 seconds.\n'
        waiting_message_shown=1
    fi
    sleep 5
done

exec journalctl --follow --unit "$unit" --lines 200 --output short-iso --no-hostname
