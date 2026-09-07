## Purpose

定义 Skill 如何把完整转写委派给运行在同一 Agent 环境中的子 Agent，获得稳定、结构化且可验证的摘要，同时避免额外模型 API、凭据和远程客户端依赖。

## ADDED Requirements

### Requirement: 生成结构化录音总结
总结子 Agent SHALL 基于完整转写生成结构化结果，至少包含总体摘要、关键结论和待办事项；不得把无法从转写中确认的信息表述为事实。

#### Scenario: 总结成功
- **WHEN** 总结器返回符合约定结构的结果
- **THEN** 系统将总体摘要、关键结论和待办事项写入 Markdown 与 JSON

#### Scenario: 转写中没有明确待办
- **WHEN** 总结器无法从转写中识别明确的行动项
- **THEN** 待办事项为空且系统不臆造负责人或截止时间

### Requirement: 使用 Agent 子任务完成总结
Skill SHALL 启动一个独立子 Agent读取本地转写并完成总结，不得要求用户提供额外的模型 API Key，也不得由 Python CLI 调用远程总结 API。

#### Scenario: 委派总结任务
- **WHEN** 本地 Whisper 转写成功并生成检查点
- **THEN** Skill 启动一个子 Agent，向其提供转写文件路径、总结契约和目标总结文件路径

#### Scenario: 无需额外模型凭据
- **WHEN** 用户通过 Skill 运行录音总结工作流
- **THEN** 工作流不检查 `OPENAI_API_KEY` 或其他总结服务凭据

#### Scenario: Agent 委派能力不可用
- **WHEN** 当前 Agent 环境无法启动子 Agent
- **THEN** Skill 保留转写检查点、报告无法完成总结，且不得回退到未经用户要求的外部 API

### Requirement: 使用文件化总结契约
子 Agent SHALL 将总结写入 UTF-8 `summary.json`，该文件 SHALL 满足项目定义的 schema；确定性渲染命令 SHALL 在生成最终文稿前校验该文件。

#### Scenario: 总结文件有效
- **WHEN** 子 Agent 写入包含 `summary`、`key_points` 和 `action_items` 的有效 JSON
- **THEN** 渲染命令接受该文件并把总结合并到 Markdown 和最终结构化产物

#### Scenario: 总结文件无效
- **WHEN** `summary.json` 缺少必需字段、包含非法字段或不是有效 JSON
- **THEN** 渲染命令以非零状态结束、报告校验问题且不生成不完整的最终 Markdown

#### Scenario: 总结与转写哈希匹配
- **WHEN** `summary.json` 的 `transcript_sha256` 与当前转写规范化文本的 SHA-256 一致
- **THEN** 渲染命令允许合并总结并将来源记录为 `agent`

#### Scenario: 总结与转写哈希不匹配
- **WHEN** `summary.json` 的 `transcript_sha256` 与当前转写不一致
- **THEN** 渲染命令拒绝合并，且不修改检查点或已有最终 Markdown

### Requirement: 保留总结失败时的转写结果
系统 SHALL 在本地转写成功后持久化可恢复的结构化转写；若随后子 Agent总结或最终渲染失败，系统 SHALL 保留该结果。

#### Scenario: 子 Agent总结失败
- **WHEN** 转写已经成功而子 Agent未能生成有效总结文件
- **THEN** Skill 保留带完整分段的 JSON 和纯文本转写，并允许以后不重新运行 Whisper 即可重试总结
