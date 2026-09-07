## Purpose

定义系统如何把原始 Whisper 分段整理成适合阅读的录音文稿，同时以结构化数据保留完整时间信息，使内容能够被追溯、重新排版或再次总结。

## ADDED Requirements

### Requirement: 生成整洁的 Markdown 文稿
系统 SHALL 将转写内容整理为自然段；在收到有效的 Agent 总结文件后，生成包含录音标题、处理元数据、AI 总结和完整转写章节的 UTF-8 Markdown 文件。

#### Scenario: 成功生成完整文稿
- **WHEN** 转写和总结均成功完成
- **THEN** Markdown 按“AI 摘要”“关键结论”“待办事项”“完整转写”的顺序呈现内容

#### Scenario: 合并自然段
- **WHEN** 相邻 Whisper 分段在时间和文本长度上满足段落合并规则
- **THEN** 系统将其合并成可读自然段且不改变原始识别文字的语义顺序

### Requirement: 支持可配置的时间码粒度
系统 SHALL 支持 `none`、`paragraph` 和 `segment` 三种 Markdown 时间码模式，默认模式 SHALL 为 `paragraph`。

#### Scenario: 段落级时间码
- **WHEN** 时间码模式为 `paragraph`
- **THEN** 每个自然段仅显示该段第一条原始分段的开始时间

#### Scenario: 分段级时间码
- **WHEN** 时间码模式为 `segment`
- **THEN** 每个 Whisper 原始分段分别显示其开始时间且不因段落合并丢失边界

#### Scenario: 隐藏时间码
- **WHEN** 时间码模式为 `none`
- **THEN** Markdown 的完整转写中不显示时间码

#### Scenario: 时间码格式
- **WHEN** Markdown 需要显示时间码
- **THEN** 系统将开始时间格式化为固定的 `[HH:MM:SS]`，小时位至少为两位

#### Scenario: 非法时间码模式
- **WHEN** 用户提供不属于 `none|paragraph|segment` 的时间码模式
- **THEN** 系统在处理录音前拒绝执行并列出允许的取值

### Requirement: 始终保留结构化转写数据
系统 SHALL 为每次成功转写生成 UTF-8 JSON 文件，无论 Markdown 是否显示时间码，JSON 都 SHALL 保留每个原始分段的开始时间、结束时间和文本。

#### Scenario: Markdown 不显示时间码
- **WHEN** 时间码模式为 `none` 且转写成功
- **THEN** JSON 仍包含所有原始分段的起止秒数

#### Scenario: 记录处理元数据
- **WHEN** 系统写入 JSON
- **THEN** JSON 包含源文件标识、音频时长、实际语言、Whisper 引擎、模型、处理时间和 schema 版本

### Requirement: 提供供 Agent 阅读的转写文本
转写阶段 SHALL 同时生成 UTF-8 纯文本文件和总结模板；`prepare-summary` SHALL 能从既有检查点重建二者。纯文本按原始顺序包含全部识别文字，使子 Agent无需解析音频即可执行总结。

#### Scenario: 转写成功
- **WHEN** Whisper 生成有效分段
- **THEN** 输出目录包含 `transcript.json`、`transcript.txt` 和 `summary.template.json`，纯文本内容与 JSON 分段文字顺序一致，模板携带对应的 `transcript_sha256`

#### Scenario: 从检查点重建 Agent 输入
- **WHEN** 用户对有效的 `transcript.json` 运行 `prepare-summary`
- **THEN** 系统无需读取源音频即可重新生成匹配的纯文本和总结模板

### Requirement: 使用确定且安全的产物路径
系统 SHALL 将 Markdown 和 JSON 写入用户指定或项目默认的输出目录，采用由源文件名派生的稳定名称，并避免留下部分写入的目标文件。

#### Scenario: 使用默认输出目录
- **WHEN** 用户未指定输出目录
- **THEN** 系统将产物写入项目配置定义的 `outputs` 目录

#### Scenario: 目标产物已经存在
- **WHEN** 稳定名称对应的目标产物已经存在且用户未显式允许覆盖
- **THEN** 系统在写入前以非零状态结束并提示用户选择其他输出目录或启用覆盖

#### Scenario: 写入失败
- **WHEN** 目标目录不可写或最终文件无法原子替换
- **THEN** 系统以非零状态结束并报告失败的目标路径
