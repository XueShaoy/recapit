## Purpose

定义转写、总结和派生文档之间可验证且可恢复的产物生命周期，使多文件工作流在进程中断或单项写入失败时不会把半成品误报为完整结果。

## ADDED Requirements

### Requirement: manifest 是运行状态的权威记录
系统 SHALL 为每次录音运行维护版本化 manifest，记录源身份、运行签名、chunk 状态、工作流阶段及已提交产物的路径与摘要，并在对应文件完成原子写入和校验后才提交新的 manifest 状态。

#### Scenario: 文件写入成功但 manifest 更新失败
- **WHEN** 一个新产物已写入但提交 manifest 时失败
- **THEN** 下次运行不得仅因该文件存在就把对应阶段视为已完成

#### Scenario: manifest 引用被修改的产物
- **WHEN** manifest 中的已提交产物摘要与磁盘文件不一致
- **THEN** 系统拒绝把该产物作为可信输入并报告具体路径

### Requirement: 保持原始转写检查点不可变
系统 SHALL 在所有 chunk 合并成功后生成只包含源信息、实际转写配置和原始分段的 `transcript.json`；总结和渲染阶段不得把总结内容写回该文件。

#### Scenario: 成功渲染总结
- **WHEN** 有效 `summary.json` 与 `transcript.json` 哈希匹配并完成渲染
- **THEN** `transcript.json` 的字节内容保持不变，组合后的结构化结果写入独立 `recap.json`

#### Scenario: 使用不同总结重新渲染
- **WHEN** 用户以另一个匹配同一转写哈希的有效总结执行渲染
- **THEN** 系统无需重新转写即可生成新的 `recap.json` 和派生文档

### Requirement: 明确提交阶段和部分成功
系统 SHALL 区分 `planned`、`transcribing`、`transcribed`、`summary-ready` 和 `rendered` 阶段；任何失败 SHALL 保留最近一次已提交阶段及其可信产物，并不得宣告后续阶段完成。

#### Scenario: Markdown 写入失败
- **WHEN** `recap.json` 尚未提交且 Markdown 无法完成写入
- **THEN** manifest 不得进入 `rendered`，原始转写与有效总结仍可用于重试

#### Scenario: 可选 Word 导出失败
- **WHEN** `recap.json` 和 Markdown 已成功提交但用户请求的 Word 导出失败
- **THEN** 系统保留并报告基础产物，将 Word 标记为未完成，并以非零状态结束该请求

#### Scenario: 全部选定产物完成
- **WHEN** `recap.json`、Markdown 以及用户请求的可选产物均通过写入和校验
- **THEN** 系统最后提交 `rendered` 状态及所有文件摘要

### Requirement: 支持既有检查点读取
系统 SHALL 继续接受 change 生效前生成且通过现有 schema 与转写哈希校验的 `transcript.json` 作为 `prepare-summary` 和 `render` 输入，不要求重新执行 Whisper。

#### Scenario: 渲染旧版转写检查点
- **WHEN** 用户提供不带运行 manifest 但格式有效的既有 `transcript.json`
- **THEN** 系统允许生成新的总结输入和最终产物，同时不得依赖不存在的 chunk 检查点

