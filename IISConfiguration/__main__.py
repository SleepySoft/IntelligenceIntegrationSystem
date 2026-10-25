"""在不启动 IIS 业务服务的情况下运行配置页面。"""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path

from flask import Flask

from GlobalConfig import DEFAULT_ALTER_CONFIG_FILE, DEFAULT_CONFIG_FILE

from .service import IISConfigurationService
from .web import IISConfigurationWeb


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动 IIS 独立配置页面。")
    parser.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG_FILE))
    parser.add_argument("--fallback", type=Path, default=Path(DEFAULT_ALTER_CONFIG_FILE))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5070)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("独立配置页只允许监听本机地址；远程使用请通过带认证的 IIS 嵌入页。")
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1..65535")
    app = Flask(__name__)
    app.secret_key = secrets.token_bytes(32)
    IISConfigurationWeb(IISConfigurationService(
        args.config, fallback_path=args.fallback)).mount_to_app(app, url_prefix="")
    print(f"IIS 配置页面: http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
