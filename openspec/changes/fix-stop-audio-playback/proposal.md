## Why

当前工具虽然提供了“停止”按钮，但停止请求主要只是设置引擎标记并终止日志监听，正在播放的唤醒词或试听音频仍可能继续播完，导致操作者误以为停止失效，也会干扰后续测试操作。这个问题已经直接影响测试体验和结果可信度，需要把“停止”提升为真正能中断活动音频的控制能力。

## What Changes

- 让活动中的音频播放支持可中断停止，而不是只能等待素材自然播完。
- 让正式运行中的“停止”同时终止当前唤醒词播放、循环噪声播放和后续未开始的试次。
- 让试听模式下的“停止”也能立即中断当前试听音频，而不是继续阻塞到播放结束。
- 统一停止后的状态反馈和收尾行为，避免界面已显示停止但底层仍在播声。
- 为停止链路补充回归测试，覆盖运行模式和试听模式。

## Capabilities

### New Capabilities
- `audio-stop-control`: 用户发出停止请求后，系统能够立即中断当前活动音频并完成一致的任务收尾。

### Modified Capabilities

## Impact

- 受影响代码主要在 [audio.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/audio.py)、[engine.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/engine.py)、[gui.py](C:/Users/AORUS/Desktop/voice_wakeup_tester/voice_wakeup_tester/gui.py)
- 需要调整音频播放句柄接口与停止时序
- 需要补充运行中停止、试听停止和重复停止的自动化测试
