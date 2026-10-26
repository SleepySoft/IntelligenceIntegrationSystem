#!/usr/bin/env bash

# IIS Linux Python bootstrap。
# 本文件由 iisctl source；不要在这里承载业务部署逻辑。

IIS_STANDALONE_PYTHON_VERSION="3.12.14"
IIS_STANDALONE_PYTHON_RELEASE="20260901"
IIS_STANDALONE_PYTHON_ARCHIVE_NAME="cpython-${IIS_STANDALONE_PYTHON_VERSION}+${IIS_STANDALONE_PYTHON_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
IIS_STANDALONE_PYTHON_DEFAULT_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${IIS_STANDALONE_PYTHON_RELEASE}/${IIS_STANDALONE_PYTHON_ARCHIVE_NAME}"
IIS_STANDALONE_PYTHON_DEFAULT_SHA256="72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c"

iis_bootstrap_error() {
    printf 'IIS Python bootstrap: %s\n' "$*" >&2
}

iis_controller_python_usable() {
    local candidate="$1"
    [[ -x "$candidate" ]] || return 1
    "$candidate" -c '
import ssl, sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
' >/dev/null 2>&1
}

iis_runtime_python_usable() {
    local candidate="$1"
    local probe_dir
    [[ -x "$candidate" ]] || return 1
    "$candidate" -c '
import platform, ssl, sys
from pathlib import Path
supported = sys.version_info[:2] in {(3, 11), (3, 12)}
is_cpython = platform.python_implementation() == "CPython"
not_nested = sys.prefix == sys.base_prefix
not_conda = not (Path(sys.prefix) / "conda-meta").exists()
raise SystemExit(0 if supported and is_cpython and not_nested and not_conda else 1)
' >/dev/null 2>&1 || return 1

    probe_dir="$(mktemp -d)" || return 1
    if ! "$candidate" -m venv "$probe_dir/venv" >/dev/null 2>&1 \
            || ! "$probe_dir/venv/bin/python" -c 'import pip, ssl' >/dev/null 2>&1; then
        rm -rf -- "$probe_dir"
        return 1
    fi
    rm -rf -- "$probe_dir"
}

iis_resolve_executable() {
    local candidate="$1"
    if [[ "$candidate" == */* ]]; then
        readlink -f -- "$candidate"
    else
        command -v -- "$candidate"
    fi
}

iis_install_roots() {
    local install_root="/opt/iis"
    local sandbox_root=""
    local previous=""
    local argument
    for argument in "$@"; do
        if [[ "$previous" == "install-root" ]]; then
            install_root="$argument"
            previous=""
            continue
        fi
        if [[ "$previous" == "sandbox-root" ]]; then
            sandbox_root="$argument"
            previous=""
            continue
        fi
        case "$argument" in
            --install-root) previous="install-root" ;;
            --install-root=*) install_root="${argument#*=}" ;;
            --sandbox-root|--root) previous="sandbox-root" ;;
            --sandbox-root=*|--root=*) sandbox_root="${argument#*=}" ;;
        esac
    done
    [[ "$install_root" == /* ]] || {
        iis_bootstrap_error "--install-root 必须是绝对路径。"
        return 1
    }
    if [[ -n "$sandbox_root" ]]; then
        [[ "$sandbox_root" == /* ]] || {
            iis_bootstrap_error "--sandbox-root 必须是绝对路径。"
            return 1
        }
        printf '%s\n%s\n' "${sandbox_root%/}/${install_root#/}" "${sandbox_root%/}"
    else
        printf '%s\n\n' "${install_root%/}"
    fi
}

iis_select_existing_runtime_python() {
    local install_root="$1"
    local candidate resolved
    local selected_link="$install_root/runtime/selected-python"

    if [[ -n "${IIS_BOOTSTRAP_PYTHON:-}" ]]; then
        resolved="$(iis_resolve_executable "$IIS_BOOTSTRAP_PYTHON" 2>/dev/null || true)"
        if [[ -z "$resolved" ]] || ! iis_runtime_python_usable "$resolved"; then
            iis_bootstrap_error "IIS_BOOTSTRAP_PYTHON 不是功能完整的 CPython 3.11/3.12: ${IIS_BOOTSTRAP_PYTHON}"
            return 2
        fi
        printf '%s\n' "$resolved"
        return 0
    fi

    if [[ -x "$selected_link" ]] && iis_runtime_python_usable "$selected_link"; then
        iis_resolve_executable "$selected_link"
        return 0
    fi

    for candidate in python3.12 python3.11 python3; do
        resolved="$(iis_resolve_executable "$candidate" 2>/dev/null || true)"
        if [[ -n "$resolved" ]] && iis_runtime_python_usable "$resolved"; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

iis_select_controller_python() {
    local candidate resolved
    if [[ -n "${IIS_BOOTSTRAP_PYTHON:-}" ]]; then
        resolved="$(iis_resolve_executable "$IIS_BOOTSTRAP_PYTHON" 2>/dev/null || true)"
        if [[ -n "$resolved" ]] && iis_controller_python_usable "$resolved"; then
            printf '%s\n' "$resolved"
            return 0
        fi
        iis_bootstrap_error "IIS_BOOTSTRAP_PYTHON 无法运行部署控制器: ${IIS_BOOTSTRAP_PYTHON}"
        return 1
    fi
    for candidate in python3.12 python3.11 python3; do
        resolved="$(iis_resolve_executable "$candidate" 2>/dev/null || true)"
        if [[ -n "$resolved" ]] && iis_controller_python_usable "$resolved"; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

iis_install_standalone_python() {
    local install_root="$1"
    local runtime_dir="$install_root/runtime"
    local target="$runtime_dir/python-${IIS_STANDALONE_PYTHON_VERSION}"
    local target_python="$target/bin/python3.12"
    local archive_override="${IIS_STANDALONE_PYTHON_ARCHIVE:-}"
    local url="${IIS_STANDALONE_PYTHON_URL:-$IIS_STANDALONE_PYTHON_DEFAULT_URL}"
    local checksum="${IIS_STANDALONE_PYTHON_SHA256:-$IIS_STANDALONE_PYTHON_DEFAULT_SHA256}"
    local work_dir archive

    case "$(uname -s):$(uname -m)" in
        Linux:x86_64|Linux:amd64) ;;
        *)
            iis_bootstrap_error "独立 Python 首版只支持 Linux x86_64。"
            return 1
            ;;
    esac
    for candidate in tar sha256sum mktemp; do
        command -v "$candidate" >/dev/null 2>&1 || {
            iis_bootstrap_error "安装独立 Python 缺少命令: $candidate"
            return 1
        }
    done
    if [[ -z "$archive_override" ]]; then
        command -v curl >/dev/null 2>&1 || {
            iis_bootstrap_error "下载独立 Python 需要 curl。"
            return 1
        }
    elif [[ ! -f "$archive_override" ]]; then
        iis_bootstrap_error "IIS_STANDALONE_PYTHON_ARCHIVE 不存在: $archive_override"
        return 1
    fi

    if [[ -x "$target_python" ]] && iis_runtime_python_usable "$target_python"; then
        printf '%s\n' "$target_python"
        return 0
    fi
    if [[ -e "$target" ]]; then
        iis_bootstrap_error "独立 Python 目标已存在但不可用，请人工检查: $target"
        return 1
    fi
    if ! mkdir -p -- "$runtime_dir" 2>/dev/null; then
        iis_bootstrap_error "无法创建 $runtime_dir；生产安装请使用 sudo。"
        return 1
    fi
    work_dir="$(mktemp -d "$runtime_dir/.python-bootstrap.XXXXXX")"
    archive="$work_dir/$IIS_STANDALONE_PYTHON_ARCHIVE_NAME"
    if [[ -n "$archive_override" ]]; then
        cp -- "$archive_override" "$archive"
    else
        printf '未找到受支持的系统 Python，下载 CPython %s...\n' \
            "$IIS_STANDALONE_PYTHON_VERSION" >&2
        if ! curl --fail --location --proto '=https' --tlsv1.2 \
                --output "$archive" "$url"; then
            rm -rf -- "$work_dir"
            iis_bootstrap_error "独立 Python 下载失败。"
            return 1
        fi
    fi
    if ! printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status; then
        rm -rf -- "$work_dir"
        iis_bootstrap_error "独立 Python SHA-256 校验失败。"
        return 1
    fi
    if ! tar -xzf "$archive" -C "$work_dir" \
            || [[ ! -x "$work_dir/python/bin/python3.12" ]] \
            || ! iis_runtime_python_usable "$work_dir/python/bin/python3.12"; then
        rm -rf -- "$work_dir"
        iis_bootstrap_error "独立 Python 解压后未通过 ssl/venv/pip 自检。"
        return 1
    fi
    mv -- "$work_dir/python" "$target"
    rm -rf -- "$work_dir"
    printf '%s\n' "$target_python"
}

iis_remember_runtime_python() {
    local install_root="$1"
    local python_path="$2"
    local runtime_dir="$install_root/runtime"
    mkdir -p -- "$runtime_dir"
    ln -sfn -- "$python_path" "$runtime_dir/selected-python"
}

iis_bootstrap_exec() {
    local controller="$1"
    shift
    local command_name="${1:-}"
    local roots install_root python_path select_status

    local help_only="false"
    local argument
    for argument in "$@"; do
        if [[ "$argument" == "-h" || "$argument" == "--help" ]]; then
            help_only="true"
            break
        fi
    done
    if [[ "$command_name" != "install" || "$help_only" == "true" ]]; then
        python_path="$(iis_select_controller_python)" || {
            iis_bootstrap_error "需要 Python 3.10+ 才能运行部署控制器。"
            return 1
        }
        exec "$python_path" "$controller" "$@"
    fi

    roots="$(iis_install_roots "$@")" || return 1
    install_root="$(printf '%s\n' "$roots" | sed -n '1p')"
    set +e
    python_path="$(iis_select_existing_runtime_python "$install_root")"
    select_status=$?
    set -e
    if [[ $select_status -eq 2 ]]; then
        return 1
    fi
    if [[ $select_status -ne 0 ]]; then
        python_path="$(iis_install_standalone_python "$install_root")" || return 1
    fi
    iis_remember_runtime_python "$install_root" "$python_path" || {
        iis_bootstrap_error "无法记录基础 Python；生产安装请使用 sudo。"
        return 1
    }
    export IIS_BOOTSTRAP_BASE_PYTHON="$python_path"
    printf 'IIS 使用基础 Python: %s\n' "$python_path" >&2
    exec "$python_path" "$controller" "$@"
}
