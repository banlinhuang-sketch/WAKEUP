"""串口与 adb 日志源行为测试。"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from voice_wakeup_tester.dut import AdbLogcatSource, LogSourceError


class FakeLogcatProcess:
    """用于驱动读取线程的轻量级 `subprocess.Popen` 假对象。"""

    def __init__(self, lines: list[str], return_code: int):
        self.stdout = iter(lines)
        self._return_code = return_code
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return self._return_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class AdbLogcatSourceTests(unittest.TestCase):
    """验证 adb logcat 的重试与诊断行为。"""

    def test_start_restarts_once_after_logcat_exit_255(self) -> None:
        """遇到一次临时性的 255 退出时，应自动重试而不是立刻判整轮失败。"""
        source = AdbLogcatSource("ABC123")
        processes = [
            FakeLogcatProcess([], 255),
            FakeLogcatProcess(["first line\r\n"], 0),
        ]
        lines: list[tuple[str, str]] = []
        errors: list[Exception] = []

        def fake_run_adb(*args: str) -> subprocess.CompletedProcess:
            self.assertEqual(args, ("logcat", "-c"))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch.object(source, "_run_adb", side_effect=fake_run_adb):
            with mock.patch.object(source, "_spawn_logcat_process", side_effect=processes):
                with mock.patch("voice_wakeup_tester.dut.time.sleep", return_value=None):
                    source.start(lambda source_name, line: lines.append((source_name, line)), errors.append)
                    assert source._thread is not None
                    source._thread.join(timeout=2.0)

        self.assertEqual(lines, [("adb_logcat", "first line")])
        self.assertEqual(errors, [])

    def test_start_reports_fatal_error_after_exhausting_255_retries(self) -> None:
        """连续多次 255 退出时，重试耗尽后应抛出带诊断信息的错误。"""
        source = AdbLogcatSource("ABC123")
        source._max_restart_attempts = 2
        processes = [
            FakeLogcatProcess([], 255),
            FakeLogcatProcess([], 255),
            FakeLogcatProcess([], 255),
        ]
        errors: list[Exception] = []

        def fake_run_adb(*args: str) -> subprocess.CompletedProcess:
            if args == ("logcat", "-c"):
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args == ("get-state",):
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="device offline")
            raise AssertionError(f"Unexpected adb invocation: {args}")

        with mock.patch.object(source, "_run_adb", side_effect=fake_run_adb):
            with mock.patch.object(source, "_spawn_logcat_process", side_effect=processes):
                with mock.patch("voice_wakeup_tester.dut.time.sleep", return_value=None):
                    source.start(lambda *_args: None, errors.append)
                    assert source._thread is not None
                    source._thread.join(timeout=2.0)

        self.assertEqual(len(errors), 1)
        self.assertIn("logcat exited with code 255", str(errors[0]))
        self.assertIn("adb get-state failed: device offline", str(errors[0]))

    def test_precheck_requires_ready_device_state(self) -> None:
        """预检阶段应拒绝 `device` 以外的 adb 状态。"""
        source = AdbLogcatSource("ABC123")

        with mock.patch.object(
            source,
            "_run_adb",
            return_value=subprocess.CompletedProcess(args=("get-state",), returncode=0, stdout="offline\n", stderr=""),
        ):
            with self.assertRaises(LogSourceError) as context:
                source.precheck()

        self.assertIn("adb device is not ready: offline", str(context.exception))


if __name__ == "__main__":
    unittest.main()
