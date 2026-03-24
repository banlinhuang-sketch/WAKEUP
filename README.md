# Voice Wakeup Tester V2.0

Windows Python tool for smart-glasses voice wakeup testing.

Current desktop release: `V2.0`

中文使用文档见 [使用文档](使用文档.md)。

## Features

- PySide6 GUI + shared headless CLI engine
- Independent mouth/noise output devices
- RTOS UART log monitoring and Qualcomm ADB logcat monitoring
- Batch scenario execution
- GUI custom trial-count controls for selected, enabled, or all scenarios
- Per-scenario noise playback duration with full-scene default compatibility
- Qualcomm recording-state guard that skips rounds after accidental video entry
- Exact per-scenario volume details derived from `noise_gain_db` / `wakeup_gain_db`
- Success-rate and latency reports in `CSV + JSON + YAML`
- `--dry-run` mode for pipeline validation without hardware

## Quick Start

```powershell
cd C:\Users\AORUS\Desktop\voice_wakeup_tester
python -m pip install -r requirements.txt
python main.py
```

## GitHub Actions Build

The repository includes [`.github/workflows/build-windows-exe.yml`](.github/workflows/build-windows-exe.yml) for automatic Windows packaging.

- Push to `main`, open a pull request, or run `workflow_dispatch`: run tests and build the EXE, then upload a GitHub Actions artifact.
- Push a tag like `v1.0.0`: build the EXE and also upload `voice_wakeup_tester-windows-x64.zip` to the GitHub Release.
- The packaged file keeps the full `dist/voice_wakeup_tester` directory so the EXE and its `_internal` dependencies stay together.

## Headless Mode

```powershell
python main.py --config sample_config.yaml --headless
python main.py --config sample_config.yaml --headless --dry-run
```

## Utility Commands

```powershell
python main.py --version
python main.py --list-audio-devices
python main.py --list-serial-ports
python main.py --list-adb-devices
```

## GUI Batch Trials

- `自定义次数` keeps working as the value used for newly added scenarios.
- `应用到选中场景` now supports multi-row selection.
- `应用到启用场景` only updates rows whose `启用` checkbox is checked.
- `应用到全部场景` updates the entire table.
- When selected rows have different `轮数`, the GUI keeps the current input value and shows a mixed-value hint instead of silently overwriting it.

## Scenario Noise Duration

- Each scenario now supports `noise_playback_duration_ms`.
- Set it to a positive value to stop the noise loop early while the remaining wakeup trials continue.
- Leave it unset or set it to `0` to keep the previous full-scene noise playback behavior.
- Precheck output now shows the effective noise playback duration for every enabled scenario.

## Exact Volume Details

- The GUI now shows read-only per-scenario volume details for the currently selected rows.
- The displayed value is derived only from `noise_gain_db` and `wakeup_gain_db`, for example `-3.0 dB (0.708x)`.
- When multiple selected rows share the same gain, the GUI shows that shared exact value; otherwise it shows `混合值`.
- This display reflects relative playback gain only and does not include the Windows system master volume.
- Precheck output now prints the exact effective noise/wakeup gain for every enabled scenario.

## Qualcomm Recording Guard

- Enable `recording_guard.enabled` to query `adb shell getprop emdoor.video.state` after each wakeup playback.
- When the property returns `ON`, the tool sends one `ADB BACK`, marks the current round as `SKIPPED`, and continues with the next round.
- `recording_guard.settle_ms` controls how long the tool waits after `BACK` before continuing.
- Precheck output now shows whether this guard is enabled and which property/action it will use.

## Bluetooth Notes

- Bluetooth speakers can be used as the noise output device.
- Prefer the `Stereo` / `A2DP` output profile and avoid `Hands-Free` / `AG Audio`.
- For reproducible latency measurements, keep the mouth output on a wired device when possible.
- If a Bluetooth device reconnects and Windows reorders audio indexes, click `刷新音频/蓝牙设备` in the GUI or re-run `--list-audio-devices`.

## Outputs

Each run writes a timestamped directory under `runs/` by default:

- `summary.json`
- `trial_results.csv`
- `event_log.csv`
- `run_config_snapshot.yaml`
