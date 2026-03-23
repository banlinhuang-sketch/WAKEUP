"""测试执行引擎，负责把播放、日志监听、判定和报表串起来。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Callable

import yaml

from .audio import (
    AudioAsset,
    AudioValidationError,
    classify_output_device_name,
    create_audio_backend,
    load_wav_asset,
    resolve_output_device,
)
from .dut import SyntheticLogSource, create_log_source
from .matching import match_any
from .models import (
    AppConfig,
    LogEvent,
    MatchRule,
    TRIAL_STATUS_ERROR,
    TRIAL_STATUS_FAIL,
    TRIAL_STATUS_PASS,
    TRIAL_STATUS_SKIPPED,
    TRIAL_STATUS_STOPPED,
    TrialResult,
    local_now_iso,
)
from .reporting import build_summary, write_reports


NO_MATCH_IN_WINDOW_REASON = (
    "\u5728\u6210\u529f\u7a97\u53e3\u5185\u672a\u6355\u83b7\u5230\u5339\u914d\u65e5\u5fd7"
)
LATE_MATCH_OUTSIDE_WINDOW_REASON = (
    "\u5339\u914d\u65e5\u5fd7\u51fa\u73b0\u5728\u6210\u529f\u7a97\u53e3\u5916\uff0c\u5ef6\u8fdf {latency_ms:.1f} ms"
)


@dataclass(slots=True)
class EngineCallbacks:
    """引擎向外部汇报状态的回调集合。"""

    on_status: Callable[[str], None] | None = None
    on_log_event: Callable[[LogEvent], None] | None = None
    on_trial_result: Callable[[TrialResult, dict], None] | None = None
    on_progress: Callable[[dict], None] | None = None
    on_finished: Callable[[dict], None] | None = None

    def status(self, message: str) -> None:
        """转发状态文本。"""
        if self.on_status:
            self.on_status(message)

    def log_event(self, event: LogEvent) -> None:
        """转发日志事件。"""
        if self.on_log_event:
            self.on_log_event(event)

    def trial_result(self, result: TrialResult, summary: dict) -> None:
        """转发单轮结果和当前汇总。"""
        if self.on_trial_result:
            self.on_trial_result(result, summary)

    def progress(self, payload: dict) -> None:
        """转发进度信息。"""
        if self.on_progress:
            self.on_progress(payload)

    def finished(self, payload: dict) -> None:
        """转发收尾信息。"""
        if self.on_finished:
            self.on_finished(payload)


@dataclass(slots=True)
class ActiveTrialWindow:
    """当前正在等待命中的试次时间窗。"""

    trial_label: str
    start_monotonic: float
    deadline_monotonic: float
    match_event: threading.Event = field(default_factory=threading.Event)
    matched_log_event: LogEvent | None = None


class TestEngine:
    """统一的测试执行引擎，GUI 和 CLI 都通过它跑测试。"""

    __test__ = False

    def __init__(
        self,
        config: AppConfig,
        dry_run: bool = False,
        audio_backend=None,
        log_source_factory=None,
    ):
        self.config = config
        self.dry_run = dry_run
        self.audio_backend = audio_backend or create_audio_backend(dry_run=dry_run)
        self.log_source_factory = log_source_factory or create_log_source
        self._asset_cache: dict[str, AudioAsset] = {}
        self._events: list[LogEvent] = []
        self._trial_results: list[TrialResult] = []
        self._active_trial: ActiveTrialWindow | None = None
        self._active_lock = threading.Lock()
        self._fatal_error: Exception | None = None
        self._stop_requested = threading.Event()
        self._log_source = None

    @property
    def events(self) -> list[LogEvent]:
        """返回日志事件快照，避免外部直接改内部列表。"""
        return list(self._events)

    @property
    def trial_results(self) -> list[TrialResult]:
        """返回试次结果快照。"""
        return list(self._trial_results)

    def _resolve_asset_path(self, raw_path: str, scenario_name: str, asset_label: str) -> Path:
        """按配置目录和当前工作目录解析音频文件路径。"""
        path_text = raw_path.strip()
        if not path_text:
            raise AudioValidationError(f"场景 {scenario_name} 的{asset_label}文件路径为空，请先选择 WAV 文件。")

        original = Path(path_text)
        candidates: list[Path] = []
        if original.is_absolute():
            candidates.append(original)
        else:
            if self.config.base_dir:
                candidates.append(Path(self.config.base_dir) / original)
            candidates.append(Path.cwd() / original)

        checked: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            resolved_key = str(candidate.resolve(strict=False))
            if resolved_key in seen:
                continue
            seen.add(resolved_key)
            checked.append(candidate)
            if candidate.exists():
                return candidate

        checked_paths = ", ".join(str(candidate) for candidate in checked) or path_text
        raise AudioValidationError(
            f"场景 {scenario_name} 的{asset_label}文件不存在: {path_text}。已尝试路径: {checked_paths}"
        )

    def _load_asset(self, path: str, gain_db: float, scenario_name: str, asset_label: str) -> AudioAsset:
        """按“解析后的绝对路径 + 增益”缓存音频资产。"""
        resolved_path = self._resolve_asset_path(path, scenario_name, asset_label)
        cache_key = f"{resolved_path.resolve()}::{gain_db:.3f}"
        if cache_key not in self._asset_cache:
            self._asset_cache[cache_key] = load_wav_asset(resolved_path).with_gain(gain_db)
        return self._asset_cache[cache_key]

    def _log_line(self, source: str, line: str, callbacks: EngineCallbacks) -> None:
        """处理来自底层日志源的一行文本。"""
        event = LogEvent(
            timestamp_monotonic=time.monotonic(),
            timestamp_iso=local_now_iso(),
            source=source,
            raw_line=line,
            matched=match_any(line, self.config.match_rules),
        )
        with self._active_lock:
            if (
                event.matched
                and self._active_trial is not None
                and self._active_trial.start_monotonic <= event.timestamp_monotonic <= self._active_trial.deadline_monotonic
                and self._active_trial.matched_log_event is None
            ):
                # 只认当前成功窗口内的第一条命中日志，后续重复命中仅作为背景事件保留。
                event.trial_label = self._active_trial.trial_label
                event.matched_window = True
                self._active_trial.matched_log_event = event
                self._active_trial.match_event.set()
        self._events.append(event)
        callbacks.log_event(event)

    def _log_error(self, exc: Exception, callbacks: EngineCallbacks) -> None:
        """记录日志链路异常，并唤醒正在等待命中的试次。"""
        self._fatal_error = exc
        callbacks.status(f"监听错误: {exc}")
        with self._active_lock:
            if self._active_trial is not None:
                self._active_trial.match_event.set()

    def request_stop(self) -> None:
        """供 GUI/CLI 主动请求停止测试。"""
        self._stop_requested.set()
        if self._log_source is not None:
            try:
                self._log_source.stop()
            except Exception:
                pass

    def preview_asset(self, path: str, device: str, gain_db: float = 0.0) -> None:
        """用于 GUI 试听按钮的单次播放。"""
        asset = self._load_asset(path, gain_db, scenario_name="preview", asset_label="试听")
        if not self.dry_run:
            self.audio_backend.validate_output(device, asset)
        handle = self.audio_backend.play_once(device, asset)
        handle.wait(timeout=max(asset.duration_seconds + 5.0, 5.0))

    def _enabled_scenarios(self):
        """返回启用状态的场景列表。"""
        return [scenario for scenario in self.config.scenarios if scenario.enabled]

    def _check_same_device(self) -> None:
        """按默认策略禁止人工嘴与噪声共用同一输出设备。"""
        if self.dry_run or self.config.allow_same_device:
            return
        mouth = self.config.audio_devices.mouth_output
        noise = self.config.audio_devices.noise_output
        if not mouth or not noise:
            return
        mouth_index, _ = resolve_output_device(mouth)
        noise_index, _ = resolve_output_device(noise)
        if mouth_index == noise_index:
            raise AudioValidationError("人工嘴与噪声音箱默认要求选择不同的输出设备。")

    def _build_audio_device_messages(self) -> list[str]:
        """根据已选音频设备补充蓝牙与通话模式提示。"""
        if self.dry_run:
            return []

        messages: list[str] = []
        for role, selection in [
            ("人工嘴", self.config.audio_devices.mouth_output),
            ("噪声音箱", self.config.audio_devices.noise_output),
        ]:
            if not selection:
                continue
            _index, device = resolve_output_device(selection)
            device_name = str(device.get("name", "")).strip()
            flags = classify_output_device_name(device_name)
            if flags["is_handsfree"]:
                messages.append(
                    f"警告: {role} 当前选择的是 Hands-Free/AG Audio 通话模式“{device_name}”，"
                    "不建议用于音频测试，请改选 Stereo/A2DP 输出。"
                )
                continue
            if role == "噪声音箱" and flags["is_bluetooth"]:
                messages.append(
                    f"提示: 噪声音箱当前使用蓝牙输出“{device_name}”，可以用于噪声播放；"
                    "如蓝牙重连后设备变化，请刷新音频设备列表后重新确认。"
                )
            if role == "人工嘴" and flags["is_bluetooth"]:
                messages.append(
                    f"提示: 人工嘴当前使用蓝牙输出“{device_name}”，蓝牙额外时延和抖动可能影响响应时延统计，"
                    "正式测试建议改用有线输出。"
                )
        return messages

    def _describe_match_rule(self, rule: MatchRule, index: int) -> str:
        """Format one active log matching rule for precheck output."""
        rule_type = "regex" if rule.type == "regex" else "keyword"
        case_suffix = ", case-sensitive" if rule.case_sensitive else ""
        return f"监控规则 {index}: [{rule_type}{case_suffix}] {rule.pattern}"

    def _build_match_rule_messages(self) -> list[str]:
        """Build human-readable status lines for the active log rules."""
        messages = ["当前监控的日志关键词/规则:"]
        for index, rule in enumerate(self.config.match_rules, start=1):
            messages.append(self._describe_match_rule(rule, index))
        return messages

    def _build_config_snapshot_messages(self) -> list[str]:
        """Build a full precheck snapshot of the active configuration."""
        snapshot = yaml.safe_dump(self.config.to_dict(), sort_keys=False, allow_unicode=True).strip()
        if not snapshot:
            return []
        return ["\u5f53\u524d\u9884\u8bbe\u53c2\u6570\u5feb\u7167:"] + snapshot.splitlines()

    def precheck(self) -> list[str]:
        """执行运行前预检，并返回展示给用户的提示信息。"""
        self.config.validate()
        enabled = self._enabled_scenarios()
        if not enabled:
            raise ValueError("At least one enabled scenario is required.")
        messages = ["开始执行预检"]
        if not self.dry_run:
            self._check_same_device()
            messages.extend(self._build_audio_device_messages())
        for scenario in enabled:
            noise_asset = self._load_asset(
                scenario.noise_file,
                scenario.noise_gain_db,
                scenario_name=scenario.name,
                asset_label="噪声",
            )
            wake_asset = self._load_asset(
                scenario.wakeup_file,
                scenario.wakeup_gain_db,
                scenario_name=scenario.name,
                asset_label="唤醒词",
            )
            if len(noise_asset.samples) == 0 or len(wake_asset.samples) == 0:
                raise AudioValidationError(f"Audio asset is empty in scenario: {scenario.name}")
            if not self.dry_run:
                self.audio_backend.validate_output(self.config.audio_devices.noise_output, noise_asset)
                self.audio_backend.validate_output(self.config.audio_devices.mouth_output, wake_asset)
            messages.append(
                f"场景 {scenario.name}: 噪声 {noise_asset.sample_rate}Hz/{noise_asset.channels}ch, "
                f"唤醒词 {wake_asset.sample_rate}Hz/{wake_asset.channels}ch"
            )
        # 这里单独实例化一次日志源，只做可用性探测，不进入正式 run。
        log_source = self.log_source_factory(
            platform=self.config.platform,
            serial_port=self.config.dut.serial_port,
            baudrate=self.config.dut.baudrate,
            adb_serial=self.config.dut.adb_serial,
            dry_run=self.dry_run,
        )
        log_source.precheck()
        messages.append("日志监听链路检查通过")
        messages.extend(self._build_match_rule_messages())
        messages.extend(self._build_config_snapshot_messages())
        return messages

    def _annotate_late_matches(self) -> None:
        """Backfill failed trials that actually matched outside the success window."""
        late_events = [event for event in self._events if event.matched and not event.matched_window]
        if not late_events:
            return

        used_event_ids: set[int] = set()
        started_trials = [trial for trial in self._trial_results if trial.wakeup_started_monotonic > 0.0]
        for index, trial in enumerate(started_trials):
            if trial.status != TRIAL_STATUS_FAIL:
                continue
            if trial.failure_reason != "鍦ㄦ垚鍔熺獥鍙ｅ唴鏈崟鑾峰埌鍖归厤鏃ュ織":
                continue

            deadline = trial.wakeup_started_monotonic + self.config.timing.success_window_ms / 1000.0
            next_trial_start = float("inf")
            if index + 1 < len(started_trials):
                next_trial_start = started_trials[index + 1].wakeup_started_monotonic
            elif self.config.timing.trial_interval_ms > 0:
                next_trial_start = (
                    trial.wakeup_started_monotonic + self.config.timing.trial_interval_ms / 1000.0
                )

            late_event = next(
                (
                    event
                    for event in late_events
                    if id(event) not in used_event_ids
                    and deadline < event.timestamp_monotonic < next_trial_start
                ),
                None,
            )
            if late_event is None:
                continue

            used_event_ids.add(id(late_event))
            late_latency_ms = (late_event.timestamp_monotonic - trial.wakeup_started_monotonic) * 1000.0
            trial.matched_line = late_event.raw_line
            trial.failure_reason = f"鍖归厤鏃ュ織鍑虹幇鍦ㄦ垚鍔熺獥鍙ｅ锛屽欢杩?{late_latency_ms:.1f} ms"

    def _annotate_late_matches_for_reports(self) -> None:
        """Backfill failed trials that matched after the success window."""
        late_events = [event for event in self._events if event.matched and not event.matched_window]
        if not late_events:
            return

        used_event_ids: set[int] = set()
        started_trials = [trial for trial in self._trial_results if trial.wakeup_started_monotonic > 0.0]
        for index, trial in enumerate(started_trials):
            if trial.status != TRIAL_STATUS_FAIL:
                continue
            if trial.matched or trial.matched_line or trial.latency_ms is not None:
                continue

            deadline = trial.wakeup_started_monotonic + self.config.timing.success_window_ms / 1000.0
            next_trial_start = (
                started_trials[index + 1].wakeup_started_monotonic
                if index + 1 < len(started_trials)
                else float("inf")
            )

            late_event = next(
                (
                    event
                    for event in late_events
                    if id(event) not in used_event_ids
                    and deadline < event.timestamp_monotonic < next_trial_start
                ),
                None,
            )
            if late_event is None:
                continue

            used_event_ids.add(id(late_event))
            late_latency_ms = (late_event.timestamp_monotonic - trial.wakeup_started_monotonic) * 1000.0
            late_event.trial_label = trial.trial_label
            trial.matched = True
            trial.matched_line = late_event.raw_line
            trial.latency_ms = late_latency_ms
            trial.failure_reason = LATE_MATCH_OUTSIDE_WINDOW_REASON.format(latency_ms=late_latency_ms)

    def _sleep_interruptible(self, seconds: float) -> bool:
        """可中断睡眠，用于试次间隔与预热等待。"""
        deadline = time.monotonic() + max(seconds, 0.0)
        while time.monotonic() < deadline:
            if self._stop_requested.is_set() or self._fatal_error is not None:
                return False
            time.sleep(min(0.1, deadline - time.monotonic()))
        return True

    def _build_partial_summary(self) -> dict:
        """构建当前已完成试次的临时汇总。"""
        return build_summary(self.config, self._trial_results, output_dir=".")

    def _append_skipped_trials(
        self,
        scenarios,
        start_scenario_index: int,
        start_trial_index: int,
        reason: str,
    ) -> None:
        """在中断后补齐剩余试次的 SKIPPED 结果。"""
        for scenario_index in range(start_scenario_index, len(scenarios)):
            scenario = scenarios[scenario_index]
            first_trial = start_trial_index if scenario_index == start_scenario_index else 1
            for trial_index in range(first_trial, scenario.trials + 1):
                result = TrialResult(
                    platform=self.config.normalized_platform(),
                    scenario_name=scenario.name,
                    trial_index=trial_index,
                    trial_label=f"{scenario.name}#{trial_index}",
                    wakeup_started_monotonic=0.0,
                    wakeup_started_iso="",
                    status=TRIAL_STATUS_SKIPPED,
                    matched=False,
                    failure_reason=reason,
                )
                self._trial_results.append(result)

    def _create_run_directory(self) -> Path:
        """为每轮执行创建独立输出目录。"""
        root = Path(self.config.output_root) if self.config.output_root else Path.cwd() / "runs"
        root.mkdir(parents=True, exist_ok=True)
        run_dir = root / time.strftime("run_%Y%m%d_%H%M%S")
        suffix = 1
        while run_dir.exists():
            suffix += 1
            run_dir = root / f"{time.strftime('run_%Y%m%d_%H%M%S')}_{suffix}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def run(self, callbacks: EngineCallbacks | None = None) -> dict:
        """执行完整测试流程并写出报告。"""
        callbacks = callbacks or EngineCallbacks()
        self._events.clear()
        self._trial_results.clear()
        self._fatal_error = None
        self._stop_requested.clear()
        self._active_trial = None

        for message in self.precheck():
            callbacks.status(message)

        run_dir = self._create_run_directory()
        scenarios = self._enabled_scenarios()
        total_trials = sum(scenario.trials for scenario in scenarios)
        started_trials = 0
        abort_cursor: tuple[int, int, str] | None = None

        self._log_source = self.log_source_factory(
            platform=self.config.platform,
            serial_port=self.config.dut.serial_port,
            baudrate=self.config.dut.baudrate,
            adb_serial=self.config.dut.adb_serial,
            dry_run=self.dry_run,
        )
        callbacks.status("启动日志监听")
        self._log_source.start(
            line_callback=lambda source, line: self._log_line(source, line, callbacks),
            error_callback=lambda exc: self._log_error(exc, callbacks),
        )

        try:
            for scenario_index, scenario in enumerate(scenarios):
                callbacks.status(f"开始场景: {scenario.name}")
                noise_asset = self._load_asset(
                    scenario.noise_file,
                    scenario.noise_gain_db,
                    scenario_name=scenario.name,
                    asset_label="噪声",
                )
                wake_asset = self._load_asset(
                    scenario.wakeup_file,
                    scenario.wakeup_gain_db,
                    scenario_name=scenario.name,
                    asset_label="唤醒词",
                )
                try:
                    # 每个场景先独立开启噪声循环，场景结束时再停止。
                    noise_handle = self.audio_backend.start_noise_loop(
                        self.config.audio_devices.noise_output,
                        noise_asset,
                    )
                except Exception as exc:
                    result = TrialResult(
                        platform=self.config.normalized_platform(),
                        scenario_name=scenario.name,
                        trial_index=1,
                        trial_label=f"{scenario.name}#1",
                        wakeup_started_monotonic=0.0,
                        wakeup_started_iso=local_now_iso(),
                        status=TRIAL_STATUS_ERROR,
                        matched=False,
                        failure_reason=f"噪声播放异常: {exc}",
                    )
                    self._trial_results.append(result)
                    started_trials += 1
                    callbacks.trial_result(result, self._build_partial_summary())
                    abort_cursor = (scenario_index, 2, f"噪声播放异常: {exc}")
                    break
                try:
                    if not self._sleep_interruptible(self.config.timing.pre_noise_roll_ms / 1000.0):
                        reason = "测试在噪声预热阶段被中断"
                        abort_cursor = (scenario_index, 1, reason)
                        break

                    for trial_index in range(1, scenario.trials + 1):
                        if self._fatal_error is not None:
                            reason = f"日志监听异常: {self._fatal_error}"
                            abort_cursor = (scenario_index, trial_index, reason)
                            break
                        if self._stop_requested.is_set():
                            abort_cursor = (scenario_index, trial_index, "用户手动停止")
                            break

                        trial_label = f"{scenario.name}#{trial_index}"
                        callbacks.status(f"执行试次 {trial_label}")
                        try:
                            playback_handle = self.audio_backend.play_once(
                                self.config.audio_devices.mouth_output,
                                wake_asset,
                            )
                        except Exception as exc:
                            result = TrialResult(
                                platform=self.config.normalized_platform(),
                                scenario_name=scenario.name,
                                trial_index=trial_index,
                                trial_label=trial_label,
                                wakeup_started_monotonic=0.0,
                                wakeup_started_iso=local_now_iso(),
                                status=TRIAL_STATUS_ERROR,
                                matched=False,
                                failure_reason=f"唤醒词播放异常: {exc}",
                            )
                            self._trial_results.append(result)
                            started_trials += 1
                            callbacks.trial_result(result, self._build_partial_summary())
                            callbacks.progress(
                                {
                                    "completed_trials": started_trials,
                                    "total_trials": total_trials,
                                    "scenario_name": scenario.name,
                                    "trial_label": trial_label,
                                }
                            )
                            abort_cursor = (scenario_index, trial_index + 1, f"唤醒词播放异常: {exc}")
                            break
                        start_monotonic = playback_handle.started_at_monotonic
                        deadline = start_monotonic + self.config.timing.success_window_ms / 1000.0
                        active = ActiveTrialWindow(
                            trial_label=trial_label,
                            start_monotonic=start_monotonic,
                            deadline_monotonic=deadline,
                        )
                        with self._active_lock:
                            self._active_trial = active

                        if isinstance(self._log_source, SyntheticLogSource):
                            # dry-run 模式下自动注入一条命中日志，便于走通整条流程。
                            self._log_source.inject_line_after(
                                delay_seconds=min(self.config.timing.success_window_ms / 2000.0, 0.4),
                                source="synthetic",
                                line=self.config.match_rules[0].pattern,
                            )

                        while True:
                            # 等待直到命中、超时、用户停止或底层日志链路出错。
                            if active.match_event.wait(timeout=0.05):
                                break
                            if self._fatal_error is not None or self._stop_requested.is_set():
                                break
                            if time.monotonic() >= deadline:
                                break

                        playback_error: Exception | None = None
                        try:
                            playback_handle.wait(timeout=max(wake_asset.duration_seconds + 5.0, 5.0))
                        except Exception as exc:
                            playback_error = exc
                        with self._active_lock:
                            matched_event = active.matched_log_event
                            self._active_trial = None

                        if playback_error is not None:
                            status = TRIAL_STATUS_ERROR
                            failure_reason = f"唤醒词播放异常: {playback_error}"
                            latency_ms = None
                            matched = False
                            matched_line = ""
                            abort_cursor = (scenario_index, trial_index + 1, f"唤醒词播放异常: {playback_error}")
                        elif self._fatal_error is not None:
                            status = TRIAL_STATUS_ERROR
                            failure_reason = str(self._fatal_error)
                            latency_ms = None
                            matched = False
                            matched_line = ""
                            abort_cursor = (scenario_index, trial_index + 1, f"日志监听异常: {self._fatal_error}")
                        elif self._stop_requested.is_set():
                            status = TRIAL_STATUS_STOPPED
                            failure_reason = "用户手动停止"
                            latency_ms = None
                            matched = False
                            matched_line = ""
                            abort_cursor = (scenario_index, trial_index + 1, "用户手动停止")
                        elif matched_event is not None:
                            status = TRIAL_STATUS_PASS
                            failure_reason = ""
                            matched = True
                            matched_line = matched_event.raw_line
                            # 这里的时延定义是“主机观测时延”，不是设备内部纯算法时延。
                            latency_ms = (matched_event.timestamp_monotonic - start_monotonic) * 1000.0
                        else:
                            status = TRIAL_STATUS_FAIL
                            failure_reason = "在成功窗口内未捕获到匹配日志"
                            matched = False
                            matched_line = ""
                            latency_ms = None

                        result = TrialResult(
                            platform=self.config.normalized_platform(),
                            scenario_name=scenario.name,
                            trial_index=trial_index,
                            trial_label=trial_label,
                            wakeup_started_monotonic=start_monotonic,
                            wakeup_started_iso=local_now_iso(),
                            status=status,
                            matched=matched,
                            latency_ms=latency_ms,
                            matched_line=matched_line,
                            failure_reason=failure_reason,
                        )
                        self._trial_results.append(result)
                        started_trials += 1
                        partial_summary = self._build_partial_summary()
                        callbacks.trial_result(result, partial_summary)
                        callbacks.progress(
                            {
                                "completed_trials": started_trials,
                                "total_trials": total_trials,
                                "scenario_name": scenario.name,
                                "trial_label": trial_label,
                            }
                        )

                        if abort_cursor is not None:
                            break

                        # 试次间隔按“本轮播放开始时刻”对齐，而不是按上一轮结束时刻累加。
                        elapsed_since_start = time.monotonic() - start_monotonic
                        remaining_interval = (self.config.timing.trial_interval_ms / 1000.0) - elapsed_since_start
                        if remaining_interval > 0 and not self._sleep_interruptible(remaining_interval):
                            abort_cursor = (scenario_index, trial_index + 1, "测试在试次间隔阶段被中断")
                            break
                    if abort_cursor is not None:
                        break
                finally:
                    try:
                        noise_handle.stop()
                    except Exception as exc:
                        callbacks.status(f"停止噪声播放失败: {exc}")

            if abort_cursor is not None:
                scenario_index, next_trial_index, reason = abort_cursor
                if next_trial_index <= scenarios[scenario_index].trials:
                    self._append_skipped_trials(scenarios, scenario_index, next_trial_index, reason)
                elif scenario_index + 1 < len(scenarios):
                    self._append_skipped_trials(scenarios, scenario_index + 1, 1, reason)
        finally:
            if self._log_source is not None:
                self._log_source.stop()
                self._log_source = None

        # 不论成功、失败还是中断，都尽量输出完整报告，方便复盘。
        summary = write_reports(run_dir, self.config, self._trial_results, self._events)
        self._annotate_late_matches_for_reports()
        summary = write_reports(run_dir, self.config, self._trial_results, self._events)
        summary["run_dir"] = str(run_dir)
        if self._fatal_error is not None:
            summary["fatal_error"] = str(self._fatal_error)
        callbacks.finished(summary)
        return summary
