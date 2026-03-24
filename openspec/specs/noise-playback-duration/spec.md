## ADDED Requirements

### Requirement: 场景可以配置独立的噪声播放时长
系统 SHALL 允许每个场景定义独立的 `noise_playback_duration_ms`，用于控制该场景噪声从开始播放起持续多久；该时长与唤醒词试次次数解耦。

#### Scenario: 未设置自定义时长时保持整场景噪声
- **WHEN** 场景未设置 `noise_playback_duration_ms` 或该值为 `0`
- **THEN** 系统 SHALL 保持当前行为，在场景开始后持续播放噪声直到该场景全部试次结束

#### Scenario: 设置固定时长后噪声提前停止
- **WHEN** 场景设置了大于 `0` 的 `noise_playback_duration_ms`
- **THEN** 系统 SHALL 在噪声开始播放后达到该时长时自动停止噪声播放

#### Scenario: 噪声停止后试次继续执行
- **WHEN** 场景的噪声已因达到 `noise_playback_duration_ms` 而停止，但该场景仍有剩余唤醒试次
- **THEN** 系统 SHALL 继续按现有 `trials` 和 `trial_interval_ms` 执行剩余唤醒词试次

### Requirement: GUI 与 YAML 必须一致表达噪声播放时长
系统 SHALL 在 GUI 场景配置和 YAML 配置中一致支持 `noise_playback_duration_ms`，避免同一场景在不同入口下出现不同语义。

#### Scenario: 从 YAML 加载场景噪声时长
- **WHEN** YAML 配置中的某个场景包含 `noise_playback_duration_ms`
- **THEN** 系统 SHALL 在 GUI 中显示该场景的噪声播放时长值

#### Scenario: 保存 GUI 配置时保留噪声时长
- **WHEN** 操作者在 GUI 中修改某个场景的噪声播放时长并保存配置
- **THEN** 系统 SHALL 把该值写回 YAML 中对应场景的 `noise_playback_duration_ms`

#### Scenario: 旧配置缺少该字段时自动兼容
- **WHEN** 旧 YAML 配置中不包含 `noise_playback_duration_ms`
- **THEN** 系统 SHALL 将该场景视为 `noise_playback_duration_ms = 0`，并保持整场景噪声播放行为

### Requirement: 预检必须展示最终生效的噪声播放时长
系统 SHALL 在预检输出中逐场景展示噪声播放时长设置，使操作者能在运行前确认该值是否已经正确加载。

#### Scenario: 自定义噪声时长出现在预检快照中
- **WHEN** 场景设置了大于 `0` 的 `noise_playback_duration_ms`
- **THEN** 预检结果 SHALL 显示该场景的噪声播放时长值

#### Scenario: 默认整场景噪声出现在预检快照中
- **WHEN** 场景未设置 `noise_playback_duration_ms` 或该值为 `0`
- **THEN** 预检结果 SHALL 明确显示该场景仍按整场景播放噪声，而不是隐藏该字段
