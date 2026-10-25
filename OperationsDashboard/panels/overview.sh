#!/usr/bin/env bash
# dashboard:title=IIS 系统总览

# 保持零额外 Python 依赖，使 Web 服务故障时总览仍可运行。
set -u

refresh_seconds="${IIS_DASHBOARD_REFRESH_SECONDS:-2}"

if [[ -t 1 ]]; then
    color_green=$'\033[32m'; color_yellow=$'\033[33m'; color_red=$'\033[31m'
    color_cyan=$'\033[36m'; color_bold=$'\033[1m'; color_reset=$'\033[0m'
else
    color_green=""; color_yellow=""; color_red=""
    color_cyan=""; color_bold=""; color_reset=""
fi

service_value() {
    local unit="$1" load_state active_state main_pid state_color
    load_state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null)"
    if [[ -z "$load_state" || "$load_state" == "not-found" ]]; then
        printf '%snot-installed%s' "$color_yellow" "$color_reset"
        return
    fi
    active_state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null)"
    main_pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null)"
    case "$active_state" in
        active) state_color="$color_green" ;;
        activating|reloading) state_color="$color_yellow" ;;
        *) state_color="$color_red" ;;
    esac
    if [[ "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
        printf '%s%s%s(%s)' "$state_color" "$active_state" "$color_reset" "$main_pid"
    else
        printf '%s%s%s' "$state_color" "$active_state" "$color_reset"
    fi
}

temperature_summary() {
    if ! command -v sensors >/dev/null 2>&1; then
        printf 'unavailable'
        return
    fi
    sensors 2>/dev/null | awk '
        /^k10temp-/ {label="CPU"; next}
        /^acpitz-/ {label="ACPI"; next}
        /^amdgpu-/ {label="GPU"; next}
        /^nvme-/ {label="NVMe"; next}
        /^(Tctl|Tdie|edge|Composite|temp1):/ && !seen[label]++ {
            value=$2; gsub(/^\+/, "", value); values[label]=value
        }
        END {
            order[1]="CPU"; order[2]="GPU"; order[3]="NVMe"; order[4]="ACPI"
            for (i=1; i<=4; i++) {
                key=order[i]
                if (values[key] != "") printf "%s%s=%s", separator, key, values[key]
                separator="  "
            }
        }'
}

restore_terminal() {
    [[ -t 1 ]] && printf '\033[?25h'
}

trap restore_terminal EXIT INT TERM
[[ -t 1 ]] && printf '\033[?25l'

while true; do
    frame="$(
        printf '%s%sIIS OVERVIEW%s  %s\n' "$color_bold" "$color_cyan" "$color_reset" "$(date '+%T')"
        printf 'Web %s  Crawl %s\n' \
            "$(service_value iis-web.service)" "$(service_value iis-crawler.service)"
        health="$(curl --silent --max-time 1 http://127.0.0.1:5000/maintenance/ping 2>/dev/null)"
        if [[ "$health" == *'"status":"ok"'* ]]; then
            api_state="${color_green}healthy${color_reset}"
        else
            api_state="${color_red}unreachable${color_reset}"
        fi
        printf 'Vector %s  API %s\n' "$(service_value iis-vectordb.service)" "$api_state"
        printf 'Temp     '; temperature_summary; printf '\n'
        read -r mem_total mem_used mem_available < <(free -h | awk '/^Mem:/ {print $2, $3, $7}')
        read -r disk_size disk_used _ disk_percent < <(df -h / | awk 'NR == 2 {print $2, $3, $4, $5}')
        printf 'Load     %s\n' "$(cut -d ' ' -f 1-3 /proc/loadavg)"
        printf 'Memory   %s/%s (avail %s)\n' "$mem_used" "$mem_total" "$mem_available"
        printf 'Disk     %s/%s (%s)\n' "$disk_used" "$disk_size" "$disk_percent"
    )"
    if [[ -t 1 ]]; then
        printf '\033[H\033[2J%s\n' "$frame"
    else
        printf '%s\n' "$frame"
    fi
    sleep "$refresh_seconds"
done
