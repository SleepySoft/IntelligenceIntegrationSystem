"""独立启动实验性态势视图，不修改或依赖 IIS Web 路由。"""

from __future__ import annotations

import argparse

from flask import Flask, redirect

from GlobalConfig import DEFAULT_ALTER_CONFIG_FILE, DEFAULT_CONFIG_FILE
from MyPythonUtility.easy_config import EasyConfig
from ServiceComponent.SubsystemRegistry import SubsystemRegistry
from SituationViews.web import SituationViewsService


def create_app(config=None, *, subsystem_registry=None) -> Flask:
    """创建只读视图服务；正式入口始终从配置连接真实 MongoDB。"""
    config = config or EasyConfig(
        config_file=DEFAULT_CONFIG_FILE,
        config_file_alter=DEFAULT_ALTER_CONFIG_FILE,
    )
    registry = subsystem_registry or SubsystemRegistry.build_from_config(
        config,
        mongodb_params={
            "host": config.get("mongodb.host", "localhost"),
            "port": config.get("mongodb.port", 27017),
            "username": config.get("mongodb.user", ""),
            "password": config.get("mongodb.password", ""),
        },
        db_name="IntelligenceIntegrationSystem",
    )
    app = Flask(__name__)
    SituationViewsService(registry).mount_to_app(app, url_prefix="/situation")

    @app.get("/")
    def root():
        return redirect("/situation/")

    return app


def main():
    parser = argparse.ArgumentParser(description="IIS experimental situation views")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5010)
    args = parser.parse_args()
    create_app().run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
