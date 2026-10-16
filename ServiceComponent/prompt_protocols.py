"""Prompt 协议族机制：prompt 文本与前处理、后处理、校验的集中注册。

两个核心概念：

- **协议（PromptProtocol）**：一族 prompt 共用的处理链——前处理（通常是注入，
  如把发布时间锚点写进 prompt）、后处理（把 LLM 原始输出桥接为规范契约）、
  校验。多个 prompt 版本可共用同一协议（文案小迭代），结构大改时才新建协议。
- **PromptCatalog**：协议与 prompt 版本的注册表，回答三个问题：生产抽样用哪些
  版本（sampling）、显式选择（调试/影子/回滚）可用哪些版本、每个版本走哪条协议。

机制本身不认识 subsystem；按子系统差异化绑定时，在组合根（启动装配）为不同
pipeline 实例传入不同 PromptCatalog 即可，协议与目录代码无需改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


PrepareHook = Callable[[str, dict], str]
"""前处理/注入钩子：(prompt 文本, 采集数据) -> 实际发送的 prompt 文本。"""

ValidateHook = Callable[[Any], Any]
"""后处理 + 校验钩子：LLM 原始输出 -> 校验通过的规范结果（如 AnalysisResultV4）。
桥接（如核心子集补齐）属于该钩子的内部职责。校验失败应抛出 ValueError/
ValidationError，由 pipeline 的重试循环捕获并回拼反馈。"""


def identity_prepare(prompt: str, data: dict) -> str:
    return prompt


class PromptCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class PromptProtocol:
    """一族 prompt 共用的处理链。"""

    name: str
    validate_result: ValidateHook
    prepare_prompt: PrepareHook = identity_prepare
    description: str = ""


@dataclass(frozen=True)
class PromptSpec:
    """一个 prompt 版本：文本 + 绑定的协议 + 是否参与生产抽样。"""

    version: int
    text: str
    protocol: str
    sampling: bool = False


class PromptCatalog:
    """协议与 prompt 版本的注册表。

    - 版本号全局唯一，注册后不可变；
    - 每个版本必须绑定已注册协议，杜绝"prompt 与校验不匹配"的静默错配；
    - sampling 版本参与生产随机抽样，非 sampling 版本仅供显式选择
      （人工调试、影子链路、回滚通道）。
    """

    def __init__(self):
        self._protocols: dict[str, PromptProtocol] = {}
        self._prompts: dict[int, PromptSpec] = {}

    def register_protocol(self, protocol: PromptProtocol) -> None:
        if protocol.name in self._protocols:
            raise PromptCatalogError(f"duplicate prompt protocol: {protocol.name}")
        if not callable(protocol.validate_result):
            raise PromptCatalogError(f"protocol {protocol.name} 缺少 validate_result")
        self._protocols[protocol.name] = protocol

    def register_prompt(self, version: int, text: str, *,
                        protocol: str, sampling: bool = False) -> None:
        version = int(version)
        if version in self._prompts:
            raise PromptCatalogError(f"duplicate prompt version: {version}")
        if protocol not in self._protocols:
            raise PromptCatalogError(
                f"prompt v{version} 绑定了未注册协议: {protocol!r}")
        if not text or not str(text).strip():
            raise PromptCatalogError(f"prompt v{version} 文本不能为空")
        self._prompts[version] = PromptSpec(
            version=version, text=str(text), protocol=protocol, sampling=bool(sampling))

    def spec_for(self, version: int) -> PromptSpec:
        spec = self._prompts.get(int(version))
        if spec is None:
            raise PromptCatalogError(f"prompt_version_not_configured:{version}")
        return spec

    def protocol_for(self, version: int) -> PromptProtocol:
        return self._protocols[self.spec_for(version).protocol]

    def text_for(self, version: int) -> str:
        return self.spec_for(version).text

    def has(self, version: int) -> bool:
        return int(version) in self._prompts

    def sampling_versions(self) -> list[int]:
        return sorted(spec.version for spec in self._prompts.values() if spec.sampling)

    def selectable_versions(self) -> list[int]:
        return sorted(self._prompts)

    def sampling_table(self) -> dict[int, str]:
        return {v: self._prompts[v].text for v in self.sampling_versions()}

    def selectable_table(self) -> dict[int, str]:
        return {v: spec.text for v, spec in sorted(self._prompts.items())}

    def extra_table(self) -> dict[int, str]:
        """不参与生产抽样、仅供显式选择的版本表（兼容旧导出）。"""
        return {v: spec.text for v, spec in sorted(self._prompts.items())
                if not spec.sampling}

    def modes_table(self) -> dict[int, str]:
        """版本 → 协议名（兼容旧导出）。"""
        return {v: spec.protocol for v, spec in sorted(self._prompts.items())}
