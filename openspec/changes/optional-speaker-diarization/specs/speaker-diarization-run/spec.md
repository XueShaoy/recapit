## Purpose

定义可选的本机说话人分离如何启用、授权、限制人数、写入检查点，以及在缺依赖或未授权时如何失败，而不改变默认转写路径。

## ADDED Requirements

### Requirement: 说话人分离默认关闭
系统 SHALL 在用户未显式启用说话人分离时走现有转写路径，不得下载分离模型、不得读取 Hugging Face Token，也不得把说话人标签写入转写产物。

#### Scenario: 默认转写
- **WHEN** 用户运行转写且未启用说话人分离
- **THEN** 系统完成现有 Whisper 转写，最终分段不含说话人标签，并且不创建说话人检查点

### Requirement: 必须显式启用
系统 SHALL 仅在配置或命令行显式启用说话人分离时执行分离；Skill 仅在用户明确要求区分说话人、人物 A/B 或类似表述时传入该选项。

#### Scenario: 命令行启用
- **WHEN** 用户在转写中启用说话人分离
- **THEN** 系统在转写流水线中执行本机分离，并在成功后把说话人标签写入最终转写

#### Scenario: Skill 收到说话人请求
- **WHEN** 用户通过 `recording-recap` Skill 明确要求区分说话人、标注人物 A/B 或「谁在说话」
- **THEN** Skill 启用说话人分离选项，并在成功后返回带说话人标签的文稿路径

### Requirement: 分离在本机完成且不上传音频
启用说话人分离时，系统 SHALL 在本机对源录音做推理；MUST NOT 把音频、转写文本或 Token 发送到第三方总结或商业分离 API。

#### Scenario: 无外网的二次运行
- **WHEN** 分离模型权重已缓存在本机，用户在无外网环境对同一配置重新启用分离
- **THEN** 系统完成本机分离且不要求访问远程推理服务

### Requirement: Hugging Face Token 按约定顺序解析
下载或刷新 gated 分离权重时，系统 SHALL 按以下顺序使用第一个非空值：`HF_TOKEN`，`HUGGING_FACE_HUB_TOKEN`，`HUGGINGFACE_TOKEN`。系统 MUST NOT 把 Token 写入项目配置、运行 manifest、日志或 git 跟踪文件。

#### Scenario: 仅存在 HUGGINGFACE_TOKEN
- **WHEN** 环境只有 `HUGGINGFACE_TOKEN` 有值，且需要下载 gated 权重
- **THEN** 系统使用该 Token 完成授权下载

#### Scenario: 标准 HF_TOKEN 优先
- **WHEN** `HF_TOKEN` 与 `HUGGINGFACE_TOKEN` 同时存在且值不同
- **THEN** 系统使用 `HF_TOKEN`

### Requirement: 缺少授权或依赖时显式失败
启用说话人分离但无法加载本机 pipeline 时，系统 SHALL 以非零状态失败，并说明是缺少可选依赖、未找到 Token，还是 gated 模型未被当前账号授权。系统 MUST NOT 在启用分离后静默生成无说话人标签的成功文稿。

#### Scenario: 未安装可选依赖
- **WHEN** 用户启用说话人分离但未安装分离可选依赖
- **THEN** 系统在开始分离前失败，并提示需要安装该可选依赖

#### Scenario: 需要下载但没有 Token
- **WHEN** 本机没有可用分离权重，且三个约定 Token 环境变量均为空
- **THEN** 系统失败并列出它所识别的环境变量名称

#### Scenario: gated 模型未同意条款
- **WHEN** Token 存在但对应账号未接受当前分离模型的使用条件
- **THEN** 系统失败并指出需要在模型页同意条款，不得假装转写成功

### Requirement: 可配置说话人人数上限
系统 SHALL 提供说话人人数上限，默认值为 4；上限 MUST 为大于等于 2 的整数。用户可提供确切人数或仅提供上限。

#### Scenario: 使用默认上限
- **WHEN** 用户启用说话人分离且未覆盖人数设置
- **THEN** 系统按最多 4 名说话人进行分离

#### Scenario: 拒绝非法上限
- **WHEN** 用户将人数上限设为 1 或非正整数
- **THEN** 系统在推理前以配置错误失败

### Requirement: 说话人身份在整条录音内一致
系统 SHALL 为一次运行输出全局一致的说话人标签，不得在分块边界把同一人重新编号为另一个标签。

#### Scenario: 跨 15 分钟边界的同一说话人
- **WHEN** 同一说话人出现在相邻转写 chunk 的核心区间两侧
- **THEN** 最终转写对该人使用同一个标签

### Requirement: 说话人步骤可与 Whisper 检查点解耦恢复
在源身份与 Whisper 运行参数仍然兼容时，系统 SHALL 复用已提交的 Whisper chunk 检查点。仅当说话人配置或说话人检查点不兼容时，系统才 MUST 重新运行分离，且 MUST NOT 仅因启用说话人而强制重跑全部 Whisper 推理。

#### Scenario: Whisper 已完成后再启用兼容的分离
- **WHEN** 已存在匹配的 Whisper chunk 检查点，用户以相同转写参数启用说话人分离且尚无有效说话人检查点
- **THEN** 系统复用 Whisper 检查点，只执行分离及相关对齐

#### Scenario: 只改人数上限
- **WHEN** Whisper 检查点匹配，但说话人人数上限与已提交说话人检查点不一致
- **THEN** 系统保留 Whisper 检查点并重新分离，不得混用旧说话人标签

### Requirement: 启用分离时运行身份包含分离参数
启用说话人分离时，系统 SHALL 把影响分离结果的模型标识与人数约束纳入运行身份；Markdown 时间码粒度和 Word 开关 MUST NOT 进入该身份。未启用分离时，运行身份 MUST NOT 因本机是否安装分离依赖而改变。

#### Scenario: 显示参数变化不使说话人检查点失效
- **WHEN** 用户仅将时间码模式从 `paragraph` 改为 `segment` 后重新渲染
- **THEN** 系统不重新运行 Whisper 或说话人分离
