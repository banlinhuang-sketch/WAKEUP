"""项目启动入口。"""

from __future__ import annotations

from pathlib import Path
import ctypes
import sys
import traceback


def _startup_log_path() -> Path:
    """返回启动异常日志路径。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("startup_error.log")
    return Path(__file__).resolve().parent / "startup_error.log"


def _write_startup_error_log(traceback_text: str) -> Path:
    """把启动阶段的异常信息写入日志文件。"""
    log_path = _startup_log_path()
    log_path.write_text(traceback_text, encoding="utf-8")
    return log_path


def _show_startup_error(message: str) -> None:
    """在 Windows 上弹出启动失败提示，避免双击后直接闪退。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "voice_wakeup_tester 启动失败", 0x10)
    except Exception:
        pass


def _run() -> int:
    """延迟导入 CLI 入口，让导入期异常也能被统一捕获。"""
    from voice_wakeup_tester.cli import main

    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(_run())
    except SystemExit:
        raise
    except Exception:
        traceback_text = traceback.format_exc()
        log_path = _write_startup_error_log(traceback_text)
        print(traceback_text, file=sys.stderr)
        _show_startup_error(
            "程序启动失败，错误详情已写入：\n"
            f"{log_path}\n\n"
            "请把这个日志文件发给开发人员排查。"
        )
        raise SystemExit(1)
