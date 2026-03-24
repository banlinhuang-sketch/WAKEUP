"""项目内共用的数据模型与状态常量。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any


SUPPORTED_PLATFORMS = {"rtos", "qualcomm"}
TRIAL_STATUS_PASS = "PASS"
TRIAL_STATUS_FAIL = "FAIL"
TRIAL_STATUS_ERROR = "ERROR"
TRIAL_STATUS_SKIPPED = "SKIPPED"
TRIAL_STATUS_STOPPED = "STOPPED"


def local_now_iso() -> str:
    """生成带毫秒的本地时区时间戳，便于日志与报告落盘。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass(slots=True)
class MatchRule:
    """描述一条关键字或正则匹配规则。"""

    type: str
    pattern: str
    case_sensitive: bool = False
    description: str = ""

    def matches(self, text: str) -> bool:
        """判断当前规则是否命中输入文本。"""
        if not self.pattern:
            return False
        if self.type == "regex":
            flags = 0 if self.case_sensitive else re.IGNORECASE
            return re.search(self.pattern, text, flags) is not None
        if self.case_sensitive:
            return self.pattern in text
        return self.pattern.lower() in text.lower()

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典，供 YAML/JSON 输出使用。"""
        payload: dict[str, Any] = {
            "type": self.type,
            "pattern": self.pattern,
            "case_sensitive": self.case_sensitive,
        }
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(slots=True)
class TimingConfig:
    """测试时序参数。"""

    pre_noise_roll_ms: int = 2000
    trial_interval_ms: int = 5000
    success_window_ms: int = 3000

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "pre_noise_roll_ms": self.pre_noise_roll_ms,
            "trial_interval_ms": self.trial_interval_ms,
            "success_window_ms": self.success_window_ms,
        }


@dataclass(slots=True)
class ScenarioConfig:
    """单条测试场景配置。"""

    name: str
    noise_file: str
    noise_gain_db: float = 0.0
    noise_playback_duration_ms: int = 0
    wakeup_file: str = ""
    wakeup_gain_db: float = 0.0
    trials: int = 10
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "name": self.name,
            "noise_file": self.noise_file,
            "noise_gain_db": self.noise_gain_db,
            "noise_playback_duration_ms": self.noise_playback_duration_ms,
            "wakeup_file": self.wakeup_file,
            "wakeup_gain_db": self.wakeup_gain_db,
            "trials": self.trials,
            "enabled": self.enabled,
        }


@dataclass(slots=True)
class DutConfig:
    """被测设备连接参数。"""

    serial_port: str = ""
    baudrate: int = 115200
    adb_serial: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "serial_port": self.serial_port,
            "baudrate": self.baudrate,
            "adb_serial": self.adb_serial,
        }


@dataclass(slots=True)
class AudioDeviceConfig:
    """双声卡输出选择。"""

    mouth_output: str = ""
    noise_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "mouth_output": self.mouth_output,
            "noise_output": self.noise_output,
        }


@dataclass(slots=True)
class AppConfig:
    """完整的应用配置对象。"""

    platform: str
    dut: DutConfig = field(default_factory=DutConfig)
    audio_devices: AudioDeviceConfig = field(default_factory=AudioDeviceConfig)
    match_rules: list[MatchRule] = field(default_factory=list)
    timing: TimingConfig = field(default_factory=TimingConfig)
    scenarios: list[ScenarioConfig] = field(default_factory=list)
    allow_same_device: bool = False
    output_root: str = ""
    base_dir: str = ""

    def normalized_platform(self) -> str:
        """统一平台字符串格式，避免大小写差异。"""
        return self.platform.strip().lower()

    def validate(self) -> None:
        """校验配置是否满足最基本的运行要求。"""
        platform = self.normalized_platform()
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform: {self.platform}")
        if not self.scenarios:
            raise ValueError("At least one scenario is required.")

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "platform": self.normalized_platform(),
            "dut": self.dut.to_dict(),
            "audio_devices": self.audio_devices.to_dict(),
            "match_rules": [rule.to_dict() for rule in self.match_rules],
            "timing": self.timing.to_dict(),
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
            "allow_same_device": self.allow_same_device,
            "output_root": self.output_root,
        }


@dataclass(slots=True)
class LogEvent:
    """统一后的日志事件对象。"""

    timestamp_monotonic: float
    timestamp_iso: str
    source: str
    raw_line: str
    matched: bool = False
    trial_label: str = ""
    matched_window: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "timestamp_monotonic": round(self.timestamp_monotonic, 6),
            "timestamp_iso": self.timestamp_iso,
            "source": self.source,
            "matched": self.matched,
            "trial_label": self.trial_label,
            "matched_window": self.matched_window,
            "raw_line": self.raw_line,
        }


@dataclass(slots=True)
class TrialResult:
    """单轮唤醒测试的结果对象。"""

    platform: str
    scenario_name: str
    trial_index: int
    trial_label: str
    wakeup_started_monotonic: float
    wakeup_started_iso: str
    status: str
    matched: bool
    latency_ms: float | None = None
    matched_line: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。"""
        return {
            "platform": self.platform,
            "scenario_name": self.scenario_name,
            "trial_index": self.trial_index,
            "trial_label": self.trial_label,
            "wakeup_started_monotonic": round(self.wakeup_started_monotonic, 6),
            "wakeup_started_iso": self.wakeup_started_iso,
            "status": self.status,
            "matched": self.matched,
            "latency_ms": round(self.latency_ms, 3) if self.latency_ms is not None else "",
            "matched_line": self.matched_line,
            "failure_reason": self.failure_reason,
        }
