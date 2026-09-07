## Purpose

定义分块转写运行的受支持配置、稳定身份和操作语义，防止未知引擎、同名录音冲突或参数变化导致不兼容检查点被静默混用。

## ADDED Requirements

### Requirement: 严格校验转写引擎和参数
系统 SHALL 仅接受当前实现支持的 `faster-whisper` 引擎，并在读取音频或加载模型前校验 chunk 时长、上下文重叠、模型、语言、设备、计算精度以及开放的解码参数。

#### Scenario: 配置未知引擎
- **WHEN** 用户配置 `engine` 为 `faster-whisper` 以外的值
- **THEN** 系统在开始媒体处理前拒绝执行并列出受支持引擎

#### Scenario: 配置非法分块参数
- **WHEN** chunk 秒数不是正数，或上下文重叠为负数或达到 chunk 核心时长
- **THEN** 系统拒绝执行并指出非法配置项

#### Scenario: 使用默认分块配置
- **WHEN** 用户未显式提供分块参数
- **THEN** 系统使用 900 秒核心区间和 10 秒边界上下文并将实际值记录到运行 manifest

### Requirement: 使用源内容标识输出目录
系统 SHALL 以清理后的源文件 stem 和完整源内容 SHA-256 的至少前 12 位生成默认录音目录身份，并在 manifest 中保存完整摘要以校验短标识碰撞。

#### Scenario: 两个目录存在同名但内容不同的录音
- **WHEN** 用户依次转写两个 stem 相同而内容摘要不同的文件
- **THEN** 系统为它们选择不同的默认输出目录且互不覆盖

#### Scenario: 同一内容移动到不同路径
- **WHEN** 源录音内容不变但路径或文件名发生变化
- **THEN** 系统可通过完整内容摘要识别已有运行，同时保留当前源路径作为展示元数据

### Requirement: 使用运行签名约束检查点复用
系统 SHALL 根据所有影响转写结果或 chunk 计划的有效参数生成确定性运行签名；只有源内容摘要和运行签名均匹配时才自动恢复既有 chunk。

#### Scenario: 修改 Whisper 模型
- **WHEN** 已有检查点由 `turbo` 生成而用户改用另一个模型
- **THEN** 系统不得把旧 chunk 与新模型结果合并，并提示开始新运行或恢复原配置

#### Scenario: 仅改变显示参数
- **WHEN** 用户只改变 Markdown 时间码模式或是否导出 Word
- **THEN** 转写运行签名保持不变，系统可复用原始转写检查点

### Requirement: 区分恢复、覆盖和重启
系统 SHALL 默认恢复兼容的未完成转写；`render --overwrite` SHALL 只允许替换用户选定的派生最终产物；重新处理所有 chunk SHALL 要求显式 `transcribe --restart`。

#### Scenario: 默认重新运行 transcribe
- **WHEN** 输出目录存在兼容且未完成的运行，用户未提供重启选项
- **THEN** 系统恢复未完成 chunk，不覆盖已提交 chunk

#### Scenario: 显式从头转写
- **WHEN** 用户提供 `transcribe --restart`
- **THEN** 系统忽略已有 chunk 结果并建立新的转写执行，且在开始前明确提示从头处理

#### Scenario: 覆盖派生文档
- **WHEN** 用户提供 `render --overwrite`
- **THEN** 系统可替换当前运行的 `recap.json`、Markdown 和所选可选文档，但不得修改 `transcript.json` 或 chunk 检查点

### Requirement: 性能历史匹配有效运行配置
系统 SHALL 使用实际解析设备和影响推理吞吐的运行参数匹配性能历史，并以去除重叠后的源录音核心时长计算整体实时率。

#### Scenario: 从 auto 解析为 CPU
- **WHEN** `device=auto` 最终使用 CPU 完成转写
- **THEN** 性能样本记录实际 CPU 设备，而不是无法比较的 `auto` 标签

#### Scenario: 推理参数不同
- **WHEN** 历史样本的模型、计算精度、chunk 或解码参数与当前运行不同
- **THEN** 系统不得把该样本作为当前配置的精确历史估算依据

