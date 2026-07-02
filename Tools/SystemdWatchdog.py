#!/usr/bin/env python3
"""
systemd watchdog 通知封装。

- 仅在 Linux + systemd 管理 + 设置了 WATCHDOG_USEC 时实际发送通知。
- Windows 或非 systemd 环境下自动变为 no-op，不影响程序运行。
- 提供 READY / ALIVE / STOPPING 三种通知。
"""

import os
import socket
import logging

logger = logging.getLogger(__name__)


def is_watchdog_enabled() -> bool:
    """判断是否处于 systemd watchdog 监管之下。"""
    # NOTIFY_SOCKET 在 Windows 上不存在，直接返回 False
    if os.name == 'nt':
        return False
    return bool(os.environ.get('WATCHDOG_USEC')) and bool(os.environ.get('NOTIFY_SOCKET'))


def notify(message: str) -> bool:
    """
    向 systemd 发送一条 sd_notify 消息。

    Args:
        message: 如 "READY=1", "WATCHDOG=1", "STOPPING=1"

    Returns:
        bool: 是否发送成功（未启用 watchdog 时返回 True）
    """
    if not is_watchdog_enabled():
        return True

    sock_path = os.environ.get('NOTIFY_SOCKET')
    if not sock_path:
        return False

    try:
        # abstract namespace socket 以 '@' 开头
        if sock_path.startswith('@'):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.connect('\0' + sock_path[1:])
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.connect(sock_path)

        sock.sendall(message.encode('utf-8'))
        sock.close()
        return True
    except Exception as e:
        logger.warning(f"systemd notify failed: {e}")
        return False


def notify_ready() -> bool:
    """通知 systemd 服务已就绪。"""
    return notify("READY=1")


def notify_alive() -> bool:
    """通知 systemd 服务仍然存活。"""
    return notify("WATCHDOG=1")


def notify_stopping() -> bool:
    """通知 systemd 服务正在停止。"""
    return notify("STOPPING=1")
