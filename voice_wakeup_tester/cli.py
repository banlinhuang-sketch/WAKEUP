"""命令行入口，负责模式分发和简单控制台输出。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import APP_TITLE, DISPLAY_VERSION, __version__
from .audio import (
    AudioDependencyError,
    AudioValidationError,
    create_audio_backend,
    format_output_device_label,
    list_output_devices,
)
from .config import apply_platform_override, default_config, load_config
from .dut import LogSourceError, list_adb_devices, list_serial_port_names
from .engine import EngineCallbacks, TestEngine


def build_parser() -> argparse.ArgumentParser:
    """定义 CLI 参数接口。"""
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--platform", choices=["rtos", "qualcomm"], help="Override platform from YAML.")
    parser.add_argument("--headless", action="store_true", help="Run without launching the GUI.")
    parser.add_argument("--list-audio-devices", action="store_true", help="List output audio devices.")
    parser.add_argument("--list-serial-ports", action="store_true", help="List available serial ports.")
    parser.add_argument("--list-adb-devices", action="store_true", help="List available adb devices.")
    parser.add_argument("--dry-run", action="store_true", help="Use synthetic log/audio backends.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {DISPLAY_VERSION} ({__version__})")
    return parser


def _load_requested_config(args: argparse.Namespace):
    """优先读取用户配置文件，否则回退到默认配置。"""
    if args.config:
        config = load_config(args.config)
    else:
        config = default_config(platform=args.platform or "rtos", base_dir=Path.cwd())
    return apply_platform_override(config, args.platform)


def _print_audio_devices() -> int:
    """列出可用于人工嘴和噪声音箱的输出设备。"""
    try:
        devices = list_output_devices()
    except AudioDependencyError as exc:
        print(exc, file=sys.stderr)
        return 1
    for device in devices:
        print(
            f"{format_output_device_label(device)} "
            f"(channels={device['max_output_channels']}, rate={device['default_samplerate']})"
        )
    return 0


def _print_serial_ports() -> int:
    """列出可选串口。"""
    try:
        ports = list_serial_port_names()
    except LogSourceError as exc:
        print(exc, file=sys.stderr)
        return 1
    for port in ports:
        print(port)
    return 0


def _print_adb_devices() -> int:
    """列出当前 adb 可见设备。"""
    try:
        devices = list_adb_devices()
    except LogSourceError as exc:
        print(exc, file=sys.stderr)
        return 1
    for device in devices:
        print(f"{device.serial}\t{device.state}")
    return 0


def _run_headless(args: argparse.Namespace) -> int:
    """运行无界面模式，适合自动化或远程执行。"""
    config = _load_requested_config(args)
    engine = TestEngine(config=config, dry_run=args.dry_run, audio_backend=create_audio_backend(args.dry_run))
    callbacks = EngineCallbacks(
        on_status=lambda message: print(f"[status] {message}"),
        on_log_event=lambda event: print(f"[log] {event.source}: {event.raw_line}") if event.matched else None,
        on_trial_result=lambda result, _summary: print(
            f"[trial] {result.trial_label} {result.status}"
            + (f" latency={result.latency_ms:.1f}ms" if result.latency_ms is not None else "")
        ),
        on_finished=lambda summary: print(f"[done] reports saved to {summary['run_dir']}"),
    )
    try:
        engine.run(callbacks=callbacks)
    except (AudioValidationError, AudioDependencyError, LogSourceError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_audio_devices:
        return _print_audio_devices()
    if args.list_serial_ports:
        return _print_serial_ports()
    if args.list_adb_devices:
        return _print_adb_devices()

    if args.headless:
        return _run_headless(args)

    # GUI 模式依赖 PySide6，因此只在真正需要时再导入。
    from .gui import launch_gui

    config = _load_requested_config(args) if args.config or args.platform else None
    return launch_gui(initial_config=config, dry_run=args.dry_run, project_root=Path(__file__).resolve().parent.parent)
