## Why

当前 GUI 中的“自定义次数”只支持“应用到当前选中场景”和“应用到全部场景”两种操作，面对部分场景批量调参时效率偏低，也容易因为选中行切换和次数回填造成误覆盖。随着场景数量增加，这个功能已经成为一处高频但不够顺手的操作点，适合优先做一次面向测试同学的易用性优化。

## What Changes

- 将自定义次数的批量应用范围从“单行 / 全部”扩展为更明确的作用域编辑，至少覆盖“选中场景”“启用场景”“全部场景”。
- 优化场景表与自定义次数输入框的联动方式，避免在选中多条且次数不一致时静默回填单个值。
- 在执行批量设置后提供更明确的反馈，包括作用范围和受影响场景数量。
- 保持现有 `scenarios[].trials` 配置模型不变，现有 YAML、报告和执行引擎行为继续兼容。
- 保留“新增场景继承当前自定义次数”这一已有高效行为，不引入新的独立默认次数配置。

## Capabilities

### New Capabilities

- `custom-trial-batch-editing`: 为场景表提供更细粒度的自定义次数批量编辑和选择状态反馈。

### Modified Capabilities

None.

## Impact

- 主要影响 [gui.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/gui.py) 中场景表、自定义次数控件和状态提示逻辑。
- 需要补充或调整面向 GUI 交互的测试，覆盖范围计算、混合次数反馈和空选择保护。
- 需要更新 [README.md](C:/Users/AORUS/Desktop/voice_wakeup_tester/README.md) 与 [使用文档.md](C:/Users/AORUS/Desktop/voice_wakeup_tester/使用文档.md) 中关于“自定义次数”的说明。
- 不引入新依赖，不修改 YAML 配置结构，不影响执行引擎、报告格式和打包方式。
