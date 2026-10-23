#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
service_source="$project_dir/systemd/iis-dashboard.service"
desktop_source="$project_dir/systemd/iis-dashboard.desktop"
service_target="/etc/systemd/system/iis-dashboard.service"
application_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

if ! command -v tmux >/dev/null 2>&1; then
    printf 'Installing tmux...\n'
    sudo apt-get install -y tmux
fi

mkdir -p "$application_dir" "$autostart_dir"
ln -sfn "$desktop_source" "$application_dir/iis-dashboard.desktop"
ln -sfn "$desktop_source" "$autostart_dir/iis-dashboard.desktop"
sudo ln -sfn "$service_source" "$service_target"
sudo systemctl daemon-reload
sudo systemctl enable --now iis-dashboard.service

printf '\nIIS dashboard installed.\n'
printf 'Open now: %s open\n' "$project_dir/Scripts/iis_dashboard.sh"
printf 'Attach in this terminal: %s attach\n' "$project_dir/Scripts/iis_dashboard.sh"
