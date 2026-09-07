## Purpose

定义用户和 Codex Skill 如何通过两阶段命令行运行录音转写与总结，并规定配置优先级、Agent 委派、执行阶段、状态反馈及可重复执行所需的项目环境。

## ADDED Requirements

### Requirement: 通过 uv 管理和运行工作流
项目 SHALL 使用 `uv` 管理 Python 版本、依赖和锁文件，并提供 `transcribe`、`prepare-summary` 与 `render` 命令行入口。

#### Scenario: 从锁定环境运行
- **WHEN** 用户在依赖同步完成的项目中执行任一 `recapit` 子命令
- **THEN** 系统使用项目锁定的依赖运行对应的确定性阶段

### Requirement: 提供稳定的命令行参数
`transcribe` 命令 SHALL 支持录音路径、输出目录、覆盖策略、Whisper 模型和语言提示；`prepare-summary` SHALL 从既有检查点重建 Agent 输入与模板；`render` 命令 SHALL 支持转写检查点、总结文件、覆盖策略和时间码模式。命令行不得包含总结服务或总结模型参数。

#### Scenario: 查看帮助
- **WHEN** 用户执行 `uv run recapit --help` 或任一子命令的 `--help`
- **THEN** 系统展示命令、参数、允许值和用途且不启动模型推理

#### Scenario: 最小调用
- **WHEN** 用户向 `transcribe` 提供有效录音路径
- **THEN** 系统用解析后的默认配置生成转写 JSON 和供 Agent 阅读的纯文本文件

### Requirement: 配置具有明确优先级
系统 SHALL 按“命令行参数高于项目配置，项目配置高于内置默认值”的顺序解析配置；项目配置 SHALL 仅包含 Whisper、格式化和输出设置。

#### Scenario: 命令行覆盖项目配置
- **WHEN** 项目配置的时间码模式为 `paragraph` 且命令行指定 `--timestamps none`
- **THEN** 本次 Markdown 不显示时间码，项目配置文件保持不变

### Requirement: Skill 复用命令行契约
项目 SHALL 提供一个录音整理 Skill，用于把用户自然语言中的选项映射到命令行，编排本地转写、一个总结子 Agent和最终渲染；Skill 不得复制转写或格式化实现。

#### Scenario: 用户请求整理指定录音
- **WHEN** Skill 收到包含唯一录音路径且未指定其他选项的请求
- **THEN** Skill 依次运行 `transcribe`、委派一个总结子 Agent、运行 `render`，并向用户返回生成的产物路径

#### Scenario: 用户指定不显示时间码
- **WHEN** 用户明确要求文稿不包含时间码
- **THEN** Skill 调用工作流时传入 `--timestamps none`

### Requirement: 报告阶段和最终状态
CLI SHALL 报告输入校验、转写、检查点和渲染阶段；Skill SHALL 报告子 Agent总结阶段，并以命令状态和总结文件校验结果区分成功与失败。

#### Scenario: 工作流成功
- **WHEN** 所有必需阶段完成且产物成功写入
- **THEN** 系统以零状态结束并输出 Markdown 和 JSON 的绝对或项目相对路径

#### Scenario: 工作流失败
- **WHEN** 任一必需阶段无法完成
- **THEN** 系统以非零状态结束，指出失败阶段并保留此前已经安全落盘的可恢复产物
