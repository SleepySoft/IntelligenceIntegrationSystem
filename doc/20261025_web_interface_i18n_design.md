# Web 界面国际化设计

日期：2026-10-25

## 1. 范围与目标

本期只国际化 IIS Web 的界面文案、日期数字格式和可访问性文本，不翻译情报内容字段。
`EVENT_TITLE`、`EVENT_BRIEF`、`EVENT_TEXT`、实体名、事件 surface 等继续按权威归档原样展示；
情报内容多语言化属于后续非破坏性旁路，不与本期耦合。

设计必须支持任意 BCP 47 语言标签，不把系统写成中英二选一。首批目录只提供
`zh-CN` 和 `en`，新增语言只需增加 gettext 目录和配置，不改路由、模板或 JavaScript。

## 2. 技术选择

采用 Flask-Babel 4.x + GNU gettext：

- Jinja 使用 `_()` / `ngettext()` 标记界面消息；
- Python 请求期消息使用 `gettext()`，模块加载期消息使用 `lazy_gettext()`；
- `.po` 是可审阅事实源，部署使用编译后的 `.mo`；
- JavaScript 所需文案由服务端从同一 gettext 域注入，不维护第二套 JSON 翻译；
- Babel 负责 locale 解析、复数规则及日期/数字格式，不自行实现语言规则。

## 3. Locale 契约

外部统一使用 BCP 47，例如 `zh-CN`、`en`、`pt-BR`；gettext 目录采用 Babel/POSIX
目录名，例如 `zh_Hans`、`en`、`pt_BR`。配置显式维护二者映射，避免用字符串替换猜测。

选择优先级：

1. 合法的 `?lang=<BCP47>`；
2. Flask session 中用户上次明确选择；
3. HTTP `Accept-Language` 对受支持语言做 best-match；
4. `web.i18n.default_locale`。

非法或未启用语言不得进入 session，也不得成为翻译目录路径。响应增加
`Content-Language`，模板根节点使用当前 BCP 47 标签。显式 query 选择写入 session，
同源 iframe 因而自然继承；生成站内链接时逐步保留 `lang`，但 cookie/session 是可靠回退。

## 4. 配置

```yaml
web:
  i18n:
    enabled: true
    default_locale: zh-CN
    supported_locales:
      zh-CN: zh_Hans
      en: en
```

默认配置必须无需用户修改即可保持现有中文界面。增加语言时只增加映射和消息目录。

## 5. 消息边界

可翻译：导航、按钮、表头、空状态、错误提示、ARIA、日期数字周边文案。

不可翻译：UUID、URL、模型名、服务名、Prompt 版本、谓词/角色内部 ID、JSON/代码块。
这些节点继续使用标准 `translate="no"`，gettext 也不收录。

情报内容不得作为 gettext msgid；分类、谓词等协议值未来应由语言资源映射显示，但本期不
改写权威值。缺少翻译时必须回退 msgid，不允许因单条消息缺失造成页面失败。

## 6. 前端消息

页面按需注入 `window.IIS_I18N_MESSAGES` 和当前 locale，公共帮助函数 `IIS_I18N.t()` 负责
查找与 `{name}` 参数替换。注入值必须由 Jinja `tojson` 编码，禁止拼接脚本字符串。
每个页面只请求自身词条集合，避免把管理后台全部消息发送到公开页面。

长期可增加 `/api/ui-messages?scope=x` 供独立前端复用；当前服务端渲染页面直接注入，减少
一次请求并保证首屏语言一致。

## 7. 分步实施与提交边界

1. 基础设施：配置 schema、Flask-Babel 扩展、locale 选择、响应头、公共模板上下文和测试。
2. 公共壳与列表：`/views`、情报列表及通用卡片/分页消息。
3. 详情与弹窗：详情模板、详情渲染器、文章弹窗和 Prompt 弹窗。
4. 查询与聚合：查询表单、搜索状态、聚合列表。
5. 态势视图：地域图、实体图、时间线的静态与动态界面消息。
6. 其余独立子系统及登录后台页面，按页面族逐批迁移。
7. 文档和提取校验：`babel.cfg`、提取/更新/编译命令、缺失翻译与 HTML `lang` 回归测试。

每一步独立提交、可独立回滚；迁移期间未标记页面继续显示现有中文，不影响功能。

### 7.1 实施状态

截至 2026-10-25，已完成：

- locale 配置、请求协商、session 保持、响应头和独立子系统安全安装；
- `/views` 公共外壳、语言选择器、情报列表、通用卡片和分页；
- 情报详情页外壳、跨页面情报弹窗、Prompt 弹窗；
- 聚合情报页；
- 卡片/弹窗词条公共片段，查询、关系图、地域图、时间线和 Vector 页面已接入，
  尚未迁移的页面继续使用中文回退，不会因词典缺失中断。

仍待按独立提交迁移：详情正文协议标签、查询页、关系图、地域图、实体图、实体时间线、
Vector 页面，以及登录后的管理页面。情报标题、摘要和事件原文不属于这些迁移项。

### 7.2 Catalog 维护命令

```bash
# 提取模板与 Python 消息；iis_msg:1 用于公共 JS 消息模板宏的第一个参数。
pybabel extract -F babel.cfg \
  -k _ -k gettext -k ngettext:1,2 -k lazy_gettext -k iis_msg:1 \
  -o /tmp/iis_messages.pot .

# 用提取结果更新中文目录；新增语言首次使用 pybabel init。
pybabel update -i /tmp/iis_messages.pot \
  -d WebI18n/translations -l zh_Hans
pybabel compile -d WebI18n/translations
```

`.po` 与编译后的 `.mo` 均提交；`.pot` 是临时提取产物，不提交。更新后必须检查
placeholder（如 `{message}`、`{count}`）未在译文中丢失，并运行 Web i18n 回归测试。
提取配置只扫描模板和 Web 路由模块，避免把业务代码里作为普通变量使用的 `_` 误收为文案。

## 8. 测试要求

- locale 优先级、非法值、地区回退、session 持久化；
- `Content-Language` 与 `<html lang>` 一致；
- `zh-CN`、`en` 至少各跑一次核心页面；
- 缺少 msgid 时安全回退；
- JS 消息使用 JSON 注入并正确替换参数；
- 技术标识保留 `translate="no"`；
- 页面切换、iframe、弹窗和 URL 参数不丢 locale；
- 不读取或改写任何情报内容字段。

## 9. 非目标

- 不建立英文情报子系统；
- 不翻译或复制 Event V4 权威归档；
- 不改变实体解析、向量索引、评分和 Prompt；
- 不在本期设计情报内容本地化旁表的最终 schema。
