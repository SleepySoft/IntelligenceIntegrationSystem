#!/usr/bin/env bash

set -euo pipefail

session_name="${IIS_DASHBOARD_SESSION:-iis-monitor}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
self="$script_dir/$(basename "${BASH_SOURCE[0]}")"
overview_script="$script_dir/iis_system_overview.sh"

require_tmux() {
    if ! command -v tmux >/dev/null 2>&1; then
        printf 'tmux is required. Install it with: sudo apt-get install tmux\n' >&2
        exit 1
    fi
}

session_exists() {
    tmux has-session -t "$session_name" 2>/dev/null
}

pane_command() {
    local command_path="$1"
    shift
    printf '%q' "$command_path"
    printf ' %q' "$@"
}

start_dashboard() {
    require_tmux
    if session_exists; then
        return 0
    fi

    local command
    local overview_pane web_pane crawler_pane vector_pane
    command="$(pane_command /usr/bin/env IIS_DASHBOARD_COMPACT=1 "$overview_script")"
    tmux new-session -d -s "$session_name" -n wall -c "$project_dir" "$command"
    overview_pane="$(tmux display-message -p -t "$session_name:wall" '#{pane_id}')"
    tmux select-pane -t "$overview_pane" -T 'Overview'

    command="$(pane_command "$self" log iis-web.service 'IIS Web')"
    web_pane="$(tmux split-window -d -h -P -F '#{pane_id}' -t "$overview_pane" -c "$project_dir" "$command")"
    tmux select-pane -t "$web_pane" -T 'IIS Web log'

    command="$(pane_command "$self" log iis-crawler.service 'IIS Crawler')"
    crawler_pane="$(tmux split-window -d -v -P -F '#{pane_id}' -t "$overview_pane" -c "$project_dir" "$command")"
    tmux select-pane -t "$crawler_pane" -T 'Crawler log'

    command="$(pane_command "$self" log iis-vectordb.service 'IIS VectorDB')"
    vector_pane="$(tmux split-window -d -v -P -F '#{pane_id}' -t "$web_pane" -c "$project_dir" "$command")"
    tmux select-pane -t "$vector_pane" -T 'VectorDB log'
    tmux select-layout -t "$session_name:wall" tiled
    tmux select-pane -t "$overview_pane"

    command="$(pane_command "$overview_script")"
    tmux new-window -d -t "$session_name:" -n overview -c "$project_dir" "$command"

    command="$(pane_command "$self" log iis-web.service 'IIS Web')"
    tmux new-window -d -t "$session_name:" -n web-log -c "$project_dir" "$command"

    command="$(pane_command "$self" log iis-crawler.service 'IIS Crawler')"
    tmux new-window -d -t "$session_name:" -n crawler-log -c "$project_dir" "$command"

    command="$(pane_command "$self" log iis-vectordb.service 'IIS VectorDB')"
    tmux new-window -d -t "$session_name:" -n vector-log -c "$project_dir" "$command"

    if command -v btop >/dev/null 2>&1; then
        tmux new-window -d -t "$session_name:" -n btop -c "$project_dir" btop
    else
        command="$(pane_command "$self" fallback-monitor)"
        tmux new-window -d -t "$session_name:" -n system -c "$project_dir" "$command"
    fi

    tmux set-option -t "$session_name" mouse on
    tmux set-option -t "$session_name" history-limit 50000
    tmux set-option -t "$session_name" status-interval 2
    tmux set-option -t "$session_name" pane-border-status top
    tmux set-option -t "$session_name" pane-border-format '#[fg=#565f89] #{pane_title} '
    tmux set-option -t "$session_name" pane-active-border-style 'fg=#7aa2f7'
    tmux set-window-option -t "$session_name:wall" main-pane-width '55%'
    tmux set-window-option -t "$session_name:wall" main-pane-height '55%'
    tmux set-option -t "$session_name" status-style 'bg=#1f2335,fg=#c0caf5'
    tmux set-option -t "$session_name" window-status-current-style 'bg=#7aa2f7,fg=#1f2335,bold'
    tmux set-option -t "$session_name" status-left '#[bold] IIS #[default]'
    tmux set-option -t "$session_name" status-right '#[fg=#9ece6a]%F %T #[default] F1 wall | F7-F9 layouts | Alt-z zoom '
    tmux bind-key -n F1 select-window -t "$session_name:wall"
    tmux bind-key -n F2 select-window -t "$session_name:overview"
    tmux bind-key -n F3 select-window -t "$session_name:web-log"
    tmux bind-key -n F4 select-window -t "$session_name:crawler-log"
    tmux bind-key -n F5 select-window -t "$session_name:vector-log"
    tmux bind-key -n F6 select-window -t "$session_name:btop"
    tmux bind-key -n M-1 select-window -t "$session_name:wall"
    tmux bind-key -n M-2 select-window -t "$session_name:overview"
    tmux bind-key -n M-3 select-window -t "$session_name:web-log"
    tmux bind-key -n M-4 select-window -t "$session_name:crawler-log"
    tmux bind-key -n M-5 select-window -t "$session_name:vector-log"
    tmux bind-key -n M-6 select-window -t "$session_name:btop"
    tmux bind-key -n F7 select-layout -t "$session_name:wall" tiled
    tmux bind-key -n F8 select-layout -t "$session_name:wall" main-vertical
    tmux bind-key -n F9 select-layout -t "$session_name:wall" main-horizontal
    tmux bind-key -n M-z resize-pane -Z
    tmux select-window -t "$session_name:wall"
}

attach_dashboard() {
    start_dashboard
    if [[ -n "${TMUX:-}" ]]; then
        exec tmux switch-client -t "$session_name"
    fi
    exec tmux attach-session -t "$session_name"
}

open_dashboard() {
    start_dashboard
    if command -v gnome-terminal >/dev/null 2>&1; then
        exec gnome-terminal --maximize --title='IIS Operations Dashboard' -- "$self" attach
    fi
    if command -v x-terminal-emulator >/dev/null 2>&1; then
        exec x-terminal-emulator -T 'IIS Operations Dashboard' -e "$self" attach
    fi
    attach_dashboard
}

follow_log() {
    local unit="${1:?systemd unit is required}"
    local label="${2:-$unit}"
    local load_state=""
    local waiting_message_shown=0

    while true; do
        # `systemctl show` exits successfully even for a missing unit, so its
        # LoadState must be inspected explicitly. Otherwise journalctl replays
        # stale history for services that were intentionally uninstalled.
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
}

fallback_monitor() {
    while true; do
        clear
        top -b -n 1 | head -n 30
        sleep 2
    done
}

usage() {
    cat <<EOF
Usage: $(basename "$self") {start|attach|open|restart|stop|status}

  start    Create the detached dashboard session if needed
  attach   Open the dashboard in the current terminal
  open     Open the dashboard in a graphical terminal
  restart  Rebuild every dashboard view
  stop     Stop the background dashboard session
  status   Show whether the dashboard session exists
EOF
}

action="${1:-open}"
case "$action" in
    start)
        start_dashboard
        ;;
    attach)
        attach_dashboard
        ;;
    open)
        open_dashboard
        ;;
    restart)
        if session_exists; then
            tmux kill-session -t "$session_name"
        fi
        start_dashboard
        ;;
    stop)
        if session_exists; then
            tmux kill-session -t "$session_name"
        fi
        ;;
    status)
        if session_exists; then
            printf "IIS dashboard session '%s' is running.\n" "$session_name"
            tmux list-windows -t "$session_name"
        else
            printf "IIS dashboard session '%s' is stopped.\n" "$session_name"
            exit 1
        fi
        ;;
    log)
        shift
        follow_log "$@"
        ;;
    fallback-monitor)
        fallback_monitor
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
