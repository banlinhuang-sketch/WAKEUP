# Voice Wakeup Tester

Windows Python tool for smart-glasses voice wakeup testing.

中文使用文档见 [使用文档](使用文档.md)。

## Features

- PySide6 GUI + shared headless CLI engine
- Independent mouth/noise output devices
- RTOS UART log monitoring and Qualcomm ADB logcat monitoring
- Batch scenario execution
- GUI custom trial-count controls for selected or all scenarios
- Success-rate and latency reports in `CSV + JSON + YAML`
- `--dry-run` mode for pipeline validation without hardware

## Quick Start

```powershell
cd C:\Users\AORUS\Desktop\voice_wakeup_tester
python -m pip install -r requirements.txt
python main.py
```

## Headless Mode

```powershell
python main.py --config sample_config.yaml --headless
python main.py --config sample_config.yaml --headless --dry-run
```

## Utility Commands

```powershell
python main.py --list-audio-devices
python main.py --list-serial-ports
python main.py --list-adb-devices
```

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
