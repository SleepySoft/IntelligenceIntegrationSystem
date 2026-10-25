import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
project_text = str(PROJECT_DIR)
# 某些环境在 Python bin 目录中安装了同名 deploy.py；确保本仓库包始终优先。
if project_text in sys.path:
    sys.path.remove(project_text)
sys.path.insert(0, project_text)
sys.modules.pop("deploy", None)
