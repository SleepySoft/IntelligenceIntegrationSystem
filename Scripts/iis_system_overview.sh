#!/usr/bin/env bash

# Lightweight IIS/system overview used by the tmux operations dashboard.
# Keep this dependency-free so the dashboard remains useful during failures.

set -u

refresh_seconds="${IIS_DASHBOARD_REFRESH_SECONDS:-2}"

if [[ -t 1 ]]; then
    color_green=$'\033[32m'
    color_yellow=$'\033[33m'
    color_red=$'\033[31m'
    color_cyan=$'\033[36m'
    color_bold=$'\033[1m'
    color_reset=$'\033[0m'
else
    color_green=""
    color_yellow=""
    color_red=""
    color_cyan=""
    color_bold=""
    color_reset=""
fi

service_line() {
    local unit="$1"
    local label="$2"
    local load_state active_state sub_state main_pid state_color process_stats

    load_state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null)"
    if [[ -z "$load_state" || "$load_state" == "not-found" ]]; then
        printf '  %-10s %snot installed%s\n' "$label" "$color_yellow" "$color_reset"
        return
    fi

    active_state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null)"
    sub_state="$(systemctl show "$unit" --property=SubState --value 2>/dev/null)"
    main_pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null)"
    case "$active_state" in
        active) state_color="$color_green" ;;
        activating|reloading) state_color="$color_yellow" ;;
        *) state_color="$color_red" ;;
    esac

    process_stats=""
    if [[ "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
        process_stats="$(ps -p "$main_pid" -o %cpu=,%mem=,rss= 2>/dev/null | awk '{printf " CPU=%s%% MEM=%s%% RSS=%.0fMiB", $1, $2, $3/1024}')"
    fi
    printf '  %-10s %s%-10s%s %-12s PID=%-7s%s\n' \
        "$label" "$state_color" "$active_state" "$color_reset" "$sub_state" "${main_pid:-0}" "$process_stats"
}

compact_service_value() {
    local unit="$1"
    local load_state active_state main_pid state_color

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
            value=$2; gsub(/^\+/, "", value)
            values[label]=value
        }
        END {
            order[1]="CPU"; order[2]="GPU"; order[3]="NVMe"; order[4]="ACPI"
            for (i=1; i<=4; i++) {
                key=order[i]
                if (values[key] != "") printf "%s%s=%s", separator, key, values[key]
                separator="  "
            }
        }
    '
}

network_summary() {
    if command -v ip >/dev/null 2>&1; then
        ip -brief address show up 2>/dev/null | awk '$1 != "lo" && $2 == "UP" {printf "%s%s=%s", separator, $1, $3; separator="  "} END {print ""}'
    fi
}

restore_terminal() {
    if [[ -t 1 ]]; then
        printf '\033[?25h'
    fi
}

trap restore_terminal EXIT INT TERM
if [[ -t 1 ]]; then
    # Keep the old frame visible while the next one is rendered. Using `clear`
    # here leaves a perceptible blank frame in graphical terminals.
    printf '\033[?25l'
fi

while true; do
    if [[ "${IIS_DASHBOARD_COMPACT:-0}" == "1" ]]; then
        frame="$(
            printf '%s%sIIS OVERVIEW%s  %s\n' "$color_bold" "$color_cyan" "$color_reset" "$(date '+%T')"
            printf 'Web %s  Crawl %s\n' \
                "$(compact_service_value iis-web.service)" \
                "$(compact_service_value iis-crawler.service)"
            health="$(curl --silent --max-time 1 http://127.0.0.1:5000/maintenance/ping 2>/dev/null)"
            if [[ "$health" == *'"status":"ok"'* ]]; then
                api_state="${color_green}healthy${color_reset}"
            else
                api_state="${color_red}unreachable${color_reset}"
            fi
            printf 'Vector %s  API %s\n' "$(compact_service_value iis-vectordb.service)" "$api_state"
            printf 'Temp     '; temperature_summary; printf '\n'
            read -r mem_total mem_used mem_available < <(free -h | awk '/^Mem:/ {print $2, $3, $7}')
            read -r disk_size disk_used disk_available disk_percent < <(df -h / | awk 'NR == 2 {print $2, $3, $4, $5}')
            printf 'Load     %s\n' "$(cut -d ' ' -f 1-3 /proc/loadavg)"
            printf 'Memory   %s/%s (avail %s)\n' "$mem_used" "$mem_total" "$mem_available"
            printf 'Disk     %s/%s (%s)\n' "$disk_used" "$disk_size" "$disk_percent"
        )"
    else
        frame="$(
        printf '%s%sIIS OPERATIONS OVERVIEW%s  %s\n' "$color_bold" "$color_cyan" "$color_reset" "$(date '+%F %T %Z')"
        printf 'Host=%s  Uptime=%s\n\n' \
            "$(hostname)" "$(uptime -p 2>/dev/null | sed 's/^up //')"

        printf '%sServices%s\n' "$color_bold" "$color_reset"
        service_line iis-web.service Web
        service_line iis-crawler.service Crawler
        service_line iis-vectordb.service VectorDB

        health="$(curl --silent --show-error --max-time 1 http://127.0.0.1:5000/maintenance/ping 2>/dev/null)"
        if [[ "$health" == *'"status":"ok"'* ]]; then
            printf '  %-10s %shealthy%s\n' API "$color_green" "$color_reset"
        else
            printf '  %-10s %sunreachable%s\n' API "$color_red" "$color_reset"
        fi

        printf '\n%sTemperature%s  ' "$color_bold" "$color_reset"
        temperature_summary
        printf '\n'

        read -r mem_total mem_used mem_available < <(free -h | awk '/^Mem:/ {print $2, $3, $7}')
        read -r disk_size disk_used disk_available disk_percent < <(df -h / | awk 'NR == 2 {print $2, $3, $4, $5}')
        printf '%sSystem%s       Load=%s  Memory=%s/%s (avail %s)\n' \
            "$color_bold" "$color_reset" "$(cut -d ' ' -f 1-3 /proc/loadavg)" \
            "$mem_used" "$mem_total" "$mem_available"
        printf '%sStorage/net%s  Disk=%s/%s (%s)  %s\n' "$color_bold" "$color_reset" \
            "$disk_used" "$disk_size" "$disk_percent" "$(network_summary)"

        printf '\n%sTop processes%s\n' "$color_bold" "$color_reset"
        ps -eo pid,comm,%cpu,%mem,rss --sort=-%cpu | awk 'NR <= 6 {if (NR == 1) printf "  %-8s %-22s %7s %7s %10s\n", $1,$2,$3,$4,"RSS(KiB)"; else printf "  %-8s %-22s %7s %7s %10s\n", $1,$2,$3,$4,$5}'

        printf '\nF1 Wall  F2 Overview  F3 Web  F4 Crawler  F5 Vector  F6 btop\n'
        )"
    fi
    if [[ -t 1 ]]; then
        printf '\033[H\033[2J%s\n' "$frame"
    else
        printf '%s\n' "$frame"
    fi
    sleep "$refresh_seconds"
done
