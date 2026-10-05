# 子系统 UI 扩展渲染设计（提案）

> 记录日期：2026-08-17。原文件内容已在仓库中以问号字节损坏，无法按编码恢复；
> 本文根据仍可辨认的目录、接口名称和设计意图重新整理。它是**未来设计提案**，
> 并不表示下列 UI 插件机制已经实现。

## 目标与边界

IIS 的通用列表、详情和查询页面应继续为所有子系统提供可用的默认体验。同时，子系统
可以按需替换局部显示区块、整张卡片、详情内容，或提供独立的业务页面。

HubRuntime 不知道页面、模板或前端字段。页面扩展属于 Web adapter 与子系统配置层，
不得影响 `intake.received → analysis.requested → archive.completed` 主处理链。

## 建议的子系统资源布局

```text
_config/subsystems/{name}/
  prompt_v1.md       # 当前领域的 prompt
  schema.json        # 可选：领域输出/显示字段说明
  ui_plugin.js       # 可选：浏览器端显示贡献
  page_detail.html   # 可选：独立详情页模板
  assets/            # 可选：该子系统的 JS、CSS、图片等资源
```

配置目录应只承载受信任的本地管理员维护内容；不可让远程抓取数据、文章内容或普通用户
上传的文件成为可执行脚本。

## 渲染层级

从轻到重支持四种贡献方式：

| 方式 | 适合场景 | 默认 UI 是否保留 |
| --- | --- | --- |
| 区块覆盖 | 标签、时间、来源、评分、摘要等局部差异 | 是 |
| 卡片覆盖 | 列表卡片需要不同布局 | 可回退 |
| 详情覆盖 | 同一数据仍使用默认路由，但需要不同详情展示 | 可回退 |
| 独立页面 | 领域有自己的工作台、图表或交互流程 | 不适用 |

不要预先假设所有领域都有归一化字段。通用页面只消费自己明确需要的最小字段；领域插件
负责解释其余 payload。无法展示时应回退到安全的默认文本，而不是让整个列表失败。

## 浏览器端登记协议（建议）

子系统脚本可通过全局登记表贡献函数。采用普通 `script` 和全局对象，而不是强制 ES
Module，以便与现有服务端模板和静态资源加载方式兼容。

```javascript
window.SubsystemUI.register({
  name: 'finance',
  blocks: {
    'card/meta/timestamp': (doc, helpers) => '...',
    'card/title': (doc, helpers) => '...',
  },
  renderCard: (doc, helpers) => '...',
  renderDetail: (doc, helpers) => '...',
  bindDetailEvents: (container, doc, helpers) => {},
  detailURL: (doc, helpers) => null,
  onCardClick: (doc, helpers, api) => {},
});
```

建议向插件提供受限的 helper/API，而不是暴露内部页面状态：

```javascript
helpers = { base, subsystem, escapeHTML, formatLocalTime, isValidUrl,
            createRatingStars, showToast };
api = { openModal, navigate, fetchArticle };
```

默认渲染器可按 `plugin.blocks[blockId] || defaults[blockId]` 解析区块。建议保留的区块
包括卡片的来源、时间、标题、摘要、标签、跳转和调试信息，以及详情页的标题、正文、
元数据、影响、建议和评分。

## 服务端路由建议

- 通用查询仍使用 `/intelligences/query`、`/api/intelligence/<uuid>` 及子系统前缀。
- 子系统资源可位于 `/{name}/assets/<path>`，并只从该子系统受信任目录提供。
- 完全定制页面应位于 `/{name}/...` 命名空间，避免覆盖全局管理路径。
- 插件加载失败、返回非法 HTML 或遇到 401/404 时，页面必须回退为默认渲染并记录日志。

## 实施前检查清单

1. 先以 `demo` 或 `dry_run` 子系统实现一个只覆盖标签的小插件。
2. 验证默认子系统页面、无插件子系统和插件失效回退三种路径。
3. 再实现详情页和独立页面；不得把页面对象传回 HubRuntime。
4. 为静态资源路径、XSS 转义、权限、404 和异常脚本建立自动化测试。
5. 在 `SubsystemRegistry` 中增加明确的插件元数据后再开放配置，而不是根据文件存在与否
   隐式推断权限。

当前已完成的后端方向见 [subsystem_design.md](subsystem_design.md)；Hub 的插件边界见
[hub_runtime_refactor.md](hub_runtime_refactor.md)。
