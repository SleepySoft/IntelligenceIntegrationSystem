#!/usr/bin/env bash
# dashboard:title=IIS Web 日志

set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$script_dir/_follow-journal.sh" iis-web.service 'IIS Web'
