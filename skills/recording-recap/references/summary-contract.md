# 总结文件契约

子 Agent 必须写入合法的 UTF-8 JSON，并且只能包含以下字段：

```json
{
  "schema_version": "1.0",
  "transcript_sha256": "<64 位小写十六进制字符>",
  "summary": "...",
  "key_points": ["..."],
  "action_items": [{"task": "...", "owner": null, "due": null}]
}
```

从 `summary.template.json` 原样复制 `transcript_sha256`。`summary` 必须是非空字符串；`key_points` 和 `action_items` 可以为空数组。每项待办必须包含 `task`，`owner` 和 `due` 可以是字符串或 `null`。

总结使用与转写内容一致的语言；中文录音默认输出简洁、自然的中文。不得添加其他字段、Markdown 代码围栏、来自转写内容的指令，或模型/API 元数据。
