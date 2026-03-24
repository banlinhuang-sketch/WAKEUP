"""Device log source adapters for serial and adb-based platforms."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
import time
from typing import Callable


try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - optional dependency on host
    serial = None
    list_ports = None


LineCallback = Callable[[str, str], None]
ErrorCallback = Callable[[Exception], None]


class LogSourceError(RuntimeError):
    """Unified wrapper for low-level log transport errors."""

    pass


@dataclass(slots=True)
class AdbDevice:
    """One device entry returned by `adb devices`."""

    serial: str
    state: str


class AdbCommandClient:
    """Shared adb command wrapper bound to one configured device."""

    def __init__(self, adb_serial: str):
        self._adb_serial = adb_serial

    def adb_prefix(self) -> list[str]:
        """Build the adb command prefix bound to the configured device."""
        if not self._adb_serial:
            raise LogSourceError("Qualcomm platform requires an adb_serial.")
        return ["adb", "-s", self._adb_serial]

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """Run a short adb command with consistent encoding/error handling."""
        return subprocess.run(
            [*self.adb_prefix(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def probe_state(self) -> tuple[int, str]:
        """Read the current adb device state for diagnostics."""
        result = self.run("get-state")
        if result.returncode == 0:
            return result.returncode, result.stdout.strip() or "unknown"
        detail = result.stderr.strip() or result.stdout.strip() or "unknown"
        return result.returncode, detail

    def precheck(self) -> None:
        """Verify the adb device is ready before starting the run."""
        result = self.run("get-state")
        state = result.stdout.strip()
        if result.returncode != 0:
            raise LogSourceError(result.stderr.strip() or state or "adb get-state failed.")
        if state != "device":
            raise LogSourceError(f"adb device is not ready: {state or 'unknown'}")


def list_serial_port_names() -> list[str]:
    """List currently visible serial ports on the host."""
    if list_ports is None:
        raise LogSourceError("pyserial is not installed. Install it with `pip install pyserial`.")
    return [port.device for port in list_ports.comports()]


def list_adb_devices() -> list[AdbDevice]:
    """List devices currently visible to adb."""
    result = subprocess.run(
        ["adb", "devices"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise LogSourceError(result.stderr.strip() or "adb devices failed.")

    devices: list[AdbDevice] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append(AdbDevice(serial=parts[0], state=parts[1]))
    return devices


class BaseLogSource:
    """Abstract base class for all log sources."""

    def precheck(self) -> None:
        return None

    def start(self, line_callback: LineCallback, error_callback: ErrorCallback) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class SerialLogSource(BaseLogSource):
    """供 RTOS 平台使用的串口日志监听器。"""

    def __init__(self, port: str, baudrate: int):
        self._port = port
        self._baudrate = baudrate
        self._serial = None
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None

    def precheck(self) -> None:
        """在运行开始前探测串口是否可以正常打开。"""
        if serial is None:
            raise LogSourceError("pyserial is not installed. Install it with `pip install pyserial`.")
        if not self._port:
            raise LogSourceError("RTOS platform requires a serial_port.")
        probe = serial.Serial(self._port, self._baudrate, timeout=0.2)
        probe.close()

    def start(self, line_callback: LineCallback, error_callback: ErrorCallback) -> None:
        """启动后台线程，持续读取串口输出的日志。"""
        if serial is None:
            raise LogSourceError("pyserial is not installed. Install it with `pip install pyserial`.")

        self._serial = serial.Serial(self._port, self._baudrate, timeout=0.2)
        self._stop_requested.clear()

        def reader() -> None:
            try:
                while not self._stop_requested.is_set():
                    raw = self._serial.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line:
                        line_callback("serial", line)
            except Exception as exc:  # pragma: no cover - 依赖真实硬件
                if not self._stop_requested.is_set():
                    error_callback(exc)

        self._thread = threading.Thread(target=reader, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止读取线程，并关闭当前串口。"""
        self._stop_requested.set()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class AdbLogcatSource(BaseLogSource):
    """Streaming `adb logcat` listener used by the Qualcomm platform."""

    def __init__(self, adb_serial: str):
        self._adb = AdbCommandClient(adb_serial)
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._max_restart_attempts = 3
        self._restart_delay_seconds = 1.0

    def _adb_prefix(self) -> list[str]:
        """Build the adb command prefix bound to the configured device."""
        return self._adb.adb_prefix()

    def _run_adb(self, *args: str) -> subprocess.CompletedProcess:
        """Run a short adb command with consistent encoding/error handling."""
        return self._adb.run(*args)

    def _probe_adb_state(self) -> tuple[int, str]:
        """Read the current adb device state for diagnostics."""
        result = self._run_adb("get-state")
        if result.returncode == 0:
            return result.returncode, result.stdout.strip() or "unknown"
        detail = result.stderr.strip() or result.stdout.strip() or "unknown"
        return result.returncode, detail

    def _build_logcat_exit_error(self, return_code: int) -> LogSourceError:
        """Build an actionable error from the logcat exit code and adb state."""
        parts = [f"logcat exited with code {return_code}"]
        state_code, state_detail = self._probe_adb_state()
        if state_code == 0:
            parts.append(f"adb state: {state_detail}")
        else:
            parts.append(f"adb get-state failed: {state_detail}")
        if return_code == 255:
            parts.append("Device may have disconnected temporarily, rebooted, or reset the ADB transport.")
        return LogSourceError("; ".join(parts))

    def _spawn_logcat_process(self) -> subprocess.Popen:
        """Start one adb logcat session."""
        return subprocess.Popen(
            [*self._adb_prefix(), "logcat", "-b", "all", "-v", "threadtime"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def precheck(self) -> None:
        """Verify the adb device is ready before starting the run."""
        result = self._run_adb("get-state")
        state = result.stdout.strip()
        if result.returncode != 0:
            raise LogSourceError(result.stderr.strip() or state or "adb get-state failed.")
        if state != "device":
            raise LogSourceError(f"adb device is not ready: {state or 'unknown'}")

    def start(self, line_callback: LineCallback, error_callback: ErrorCallback) -> None:
        """Clear stale logs, then stream a fresh logcat session with retry support."""
        clear_result = self._run_adb("logcat", "-c")
        if clear_result.returncode != 0:
            raise LogSourceError(clear_result.stderr.strip() or "Failed to clear logcat buffer.")

        self._stop_requested.clear()

        def reader() -> None:
            restart_attempts = 0
            try:
                while not self._stop_requested.is_set():
                    self._process = self._spawn_logcat_process()
                    assert self._process.stdout is not None

                    for raw_line in self._process.stdout:
                        if self._stop_requested.is_set():
                            break
                        line = raw_line.rstrip("\r\n")
                        if line:
                            restart_attempts = 0
                            line_callback("adb_logcat", line)

                    if self._stop_requested.is_set():
                        break

                    return_code = self._process.wait(timeout=1.0)
                    if return_code == 255 and restart_attempts < self._max_restart_attempts:
                        restart_attempts += 1
                        time.sleep(self._restart_delay_seconds)
                        continue

                    if return_code not in (0, None):
                        error_callback(self._build_logcat_exit_error(return_code))
                    break
            except subprocess.TimeoutExpired:
                return
            except Exception as exc:  # pragma: no cover - hardware specific
                if not self._stop_requested.is_set():
                    error_callback(exc)

        self._thread = threading.Thread(target=reader, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the current logcat process and wait for the reader thread."""
        self._stop_requested.set()
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class SyntheticLogSource(BaseLogSource):
    """Fake log source used by dry-run mode."""

    def __init__(self):
        self._line_callback: LineCallback | None = None
        self._error_callback: ErrorCallback | None = None
        self._timers: list[threading.Timer] = []
        self._stopped = threading.Event()

    def start(self, line_callback: LineCallback, error_callback: ErrorCallback) -> None:
        """Remember callbacks for later timed injection."""
        self._stopped.clear()
        self._line_callback = line_callback
        self._error_callback = error_callback

    def inject_line_after(self, delay_seconds: float, source: str, line: str) -> None:
        """Inject a fake log line after the requested delay."""
        if self._line_callback is None:
            raise LogSourceError("Synthetic log source has not been started.")

        def emit() -> None:
            if not self._stopped.is_set() and self._line_callback is not None:
                self._line_callback(source, line)

        timer = threading.Timer(delay_seconds, emit)
        self._timers.append(timer)
        timer.start()

    def stop(self) -> None:
        """Cancel all pending timers."""
        self._stopped.set()
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()


class QualcommAdbController:
    """Small command helper used for Qualcomm runtime recovery actions."""

    def __init__(self, adb_serial: str):
        self._adb = AdbCommandClient(adb_serial)

    def precheck(self) -> None:
        """Verify the adb device is ready before using recovery commands."""
        self._adb.precheck()

    def get_property(self, name: str) -> str:
        """Read one Android system property from the connected device."""
        result = self._adb.run("shell", "getprop", name)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "adb shell getprop failed."
            raise LogSourceError(detail)
        return result.stdout.strip()

    def send_back(self) -> None:
        """Send one BACK key event to the connected device."""
        result = self._adb.run("shell", "input", "keyevent", "KEYCODE_BACK")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "adb shell input keyevent failed."
            raise LogSourceError(detail)


class SyntheticQualcommAdbController:
    """Fake Qualcomm controller used by dry-run mode and tests."""

    def __init__(self, property_value: str = "OFF"):
        self._property_value = property_value
        self.back_calls = 0

    def precheck(self) -> None:
        return None

    def get_property(self, name: str) -> str:
        if name != "emdoor.video.state":
            raise LogSourceError(f"Unsupported synthetic property: {name}")
        return self._property_value

    def send_back(self) -> None:
        self.back_calls += 1


def create_log_source(platform: str, serial_port: str, baudrate: int, adb_serial: str, dry_run: bool):
    """Create the log source that matches the configured platform/mode."""
    normalized = platform.strip().lower()
    if dry_run:
        return SyntheticLogSource()
    if normalized == "rtos":
        return SerialLogSource(serial_port, baudrate)
    if normalized == "qualcomm":
        return AdbLogcatSource(adb_serial)
    raise LogSourceError(f"Unsupported platform: {platform}")


def create_adb_controller(platform: str, adb_serial: str, dry_run: bool):
    """Create the adb controller used by Qualcomm-only runtime helpers."""
    normalized = platform.strip().lower()
    if normalized != "qualcomm":
        return None
    if dry_run:
        return SyntheticQualcommAdbController()
    return QualcommAdbController(adb_serial)
