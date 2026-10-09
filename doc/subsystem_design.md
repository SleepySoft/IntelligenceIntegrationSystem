# Event V4 子系统设计

所有启用的子系统共享 Event V4 数据契约和 Prompt 机制，但拥有独立的数据集合、URL 前缀、
AI 客户端分组、评分配置和可选 UI 插件。

## 配置

子系统入口位于 `_config/config.json`：

```json
{
  "intelligence_hub": {
    "subsystems": {
      "default": "news",
      "list": [
        {"name": "news", "display_name": "国际新闻", "enabled": true},
        {
          "name": "finance",
          "display_name": "财经情报",
          "enabled": true,
          "config_file": "_config/subsystems/finance.json"
        }
      ]
    }
  }
}
```

详细配置支持：`collection_prefix`、`url_prefix`、`ai_client_group`、`scoring`、`ui_plugin`
和 `detail_page`。Prompt 不再由各子系统加载旧式 `prompt_v*.md`；所有子系统从
`prompts_event_v4.EVENT_ANALYSIS_PROMPT_TABLE` 选择版本，人工调试可在单次请求中覆盖 Prompt。

## 资源隔离

`SubsystemRegistry` 为每个子系统构造独立的 cache、V4 intelligence、low-value、event、entity
和 outbox 集合，并注册 `EventV4QueryEngine`、统计引擎、实体解析器和归档仓库。

默认子系统挂载根 URL；其他子系统通常挂载 `/{name}`。UI 插件入口为
`_config/subsystems/{name}/ui_plugin.js`，插件接收的文档只使用 V4 字段。

## 新增子系统

1. 在主配置 `subsystems.list` 中登记名称、显示名和配置文件。
2. 设置唯一 `collection_prefix` 与 `url_prefix`。
3. 如有需要，设置 AI 客户端分组和 V4 评分权重。
4. 如需定制展示，新增读取 V4 DTO 的 `ui_plugin.js`。
5. 使用人工情报调试页验证正式 Prompt 拼接、覆盖 Prompt 和 V4 校验结果。
