## Why

当前 GUI 里的场景音量调节主要通过 `noise_gain_db` 和 `wakeup_gain_db` 两列完成，但界面只展示可编辑值，缺少更直观的“当前到底是多大音量”的反馈。测试同学在调节不同场景时，容易出现只改了增益、却无法快速确认具体数值和前后差异的问题，尤其在做多轮对比和复现实验时不够直观。

## What Changes

- 在 GUI 中新增“具体音量数值”显示能力，让用户在编辑噪声增益和唤醒词增益时，能同步看到更明确的当前音量展示。
- 为场景表中的音量相关字段补充实时反馈，避免只依赖手工阅读单元格内容判断当前设置。
- 在预检或运行前快照中保留最终生效的音量展示信息，方便核对本轮实际配置。
- 保持现有 `noise_gain_db` / `wakeup_gain_db` 作为配置源，不改变现有 YAML 结构和执行逻辑。

## Capabilities

### New Capabilities
- `volume-value-display`: 在 GUI 和运行前信息中展示场景当前生效的具体音量数值，帮助用户直观确认噪声与唤醒词的音量设置。

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `voice_wakeup_tester/gui.py`
  - `voice_wakeup_tester/engine.py`
  - `voice_wakeup_tester/models.py` or supporting formatting helpers if display metadata needs to be carried
  - GUI / engine related tests
- APIs / config:
  - No breaking change to existing YAML structure
  - No change to batch execution semantics or report success-rate logic
- Systems:
  - GUI display and precheck snapshot output only
