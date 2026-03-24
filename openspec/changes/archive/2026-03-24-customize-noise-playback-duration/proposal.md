## Why

当前工具里，噪声播放在每个场景开始时启动，并默认贯穿整个场景直到所有唤醒试次结束。对一些测试场景来说，操作者希望噪声只播放一个自定义时长窗口，而不是持续覆盖每一次唤醒词播放，因此需要把噪声播放时长从“固定整场景”扩展成可配置能力。

## What Changes

- 为场景配置增加“噪声播放时长”能力，使噪声文件可以按自定义时长播放，而不是默认持续到场景结束。
- 保留当前默认行为：如果未设置自定义时长，噪声仍按现状从场景开始循环播放到场景结束。
- 让 GUI、YAML 配置、预检快照和运行时行为都能表达并显示这一时长设置，避免“界面看起来配了，但实际没有生效”。
- 允许唤醒词试次继续按现有 `trials` 和 `trial_interval_ms` 运行，即使噪声已经提前停止，也不强制噪声与每一次唤醒词播放绑定。
- 为这一能力补充自动化测试和使用文档，覆盖默认兼容、自定义时长生效和预检展示。

## Capabilities

### New Capabilities
- `noise-playback-duration`: 支持为场景定义噪声播放时长窗口，使噪声播放与场景总时长解耦。

### Modified Capabilities

None.

## Impact

- 主要影响 [models.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/models.py)、[config.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/config.py)、[gui.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/gui.py) 和 [engine.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/engine.py) 中的场景配置、运行时序与预检展示。
- 需要补充场景配置序列化/反序列化、运行流程和预检消息的自动化测试。
- 需要更新 [sample_config.yaml](C:/Users/AORUS/Desktop/voice_wakeup_tester/sample_config.yaml)、[README.md](C:/Users/AORUS/Desktop/voice_wakeup_tester/README.md) 和 [使用文档.md](C:/Users/AORUS/Desktop/voice_wakeup_tester/使用文档.md)。
- 不引入新依赖，不改变现有日志匹配、报告格式和打包方式。
