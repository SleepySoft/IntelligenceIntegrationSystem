"""源码检出目录的包路径桥接。

正式安装时使用内层 ``event_engine`` 包；在 IIS 仓库根目录直接运行时，将内层目录
加入包搜索路径，保证组合代码与测试使用相同的 ``event_engine.*`` 导入路径。
"""

from pathlib import Path

_INNER_PACKAGE = Path(__file__).resolve().parent / "event_engine"
if str(_INNER_PACKAGE) not in __path__:
    __path__.append(str(_INNER_PACKAGE))
