#!/usr/bin/env bash
# dashboard:title=IIS VectorDB 日志

set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$script_dir/_follow-journal.sh" iis-vectordb.service 'IIS VectorDB'
