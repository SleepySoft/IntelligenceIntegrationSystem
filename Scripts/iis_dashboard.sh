#!/usr/bin/env bash

# 稳定的外部入口；实现、配置示例和面板脚本均在 OperationsDashboard/。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"

exec python3 "$project_dir/OperationsDashboard/dashboard.py" "$@"
