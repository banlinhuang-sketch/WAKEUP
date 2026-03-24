"""Dry-run integration tests for the test engine."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from voice_wakeup_tester.audio import AudioValidationError
from voice_wakeup_tester.config import config_from_dict
from voice_wakeup_tester.dut import SyntheticLogSource
from voice_wakeup_tester.engine import EngineCallbacks, TestEngine
from voice_wakeup_tester.models import (
    LogEvent,
    TRIAL_STATUS_ERROR,
    TRIAL_STATUS_FAIL,
    TRIAL_STATUS_PASS,
    TRIAL_STATUS_SKIPPED,
    TRIAL_STATUS_STOPPED,
    TrialResult,
)


def write_silence_wav(path: Path, sample_rate: int = 16000, duration_seconds: float = 0.05) -> None:
    """Generate a tiny silent WAV file for tests."""
    frames = np.zeros(int(sample_rate * duration_seconds), dtype=np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames.tobytes())


class BurstSyntheticLogSource(SyntheticLogSource):
    """Simulate duplicate matches inside one trial window."""

    def inject_line_after(self, delay_seconds: float, source: str, line: str) -> None:
        super().inject_line_after(delay_seconds, source, line)
        super().inject_line_after(delay_seconds + 0.02, source, line)


class RecordingPlaybackHandle:
    """Minimal playback handle used by runtime tests."""

    def __init__(self):
        self.started_at_monotonic = time.monotonic()

    def wait(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None:
        return None


class RecordingNoiseHandle:
    """Track when the noise loop is asked to stop."""

    def __init__(self):
        self.stop_calls = 0
        self.first_stop_monotonic: float | None = None

    def stop(self) -> None:
        self.stop_calls += 1
        if self.first_stop_monotonic is None:
            self.first_stop_monotonic = time.monotonic()


class InterruptiblePlaybackHandle:
    """可被停止请求中断的单次播放假句柄。"""

    def __init__(self):
        self.started_at_monotonic = time.monotonic()
        self.stop_calls = 0
        self._done = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout=timeout)

    def stop(self) -> None:
        self.stop_calls += 1
        self._done.set()


class InterruptibleNoiseHandle:
    """记录噪声停止请求次数的假句柄。"""

    def __init__(self):
        self.stop_calls = 0
        self._done = threading.Event()

    def stop(self) -> None:
        self.stop_calls += 1
        self._done.set()


class InterruptibleAudioBackend:
    """用于停止链路测试的内存音频后端。"""

    def __init__(self):
        self.playback_handles: list[InterruptiblePlaybackHandle] = []
        self.noise_handles: list[InterruptibleNoiseHandle] = []

    def validate_output(self, _selection, _asset) -> None:
        return None

    def start_noise_loop(self, _selection, _asset) -> InterruptibleNoiseHandle:
        handle = InterruptibleNoiseHandle()
        self.noise_handles.append(handle)
        return handle

    def play_once(self, _selection, _asset) -> InterruptiblePlaybackHandle:
        handle = InterruptiblePlaybackHandle()
        self.playback_handles.append(handle)
        return handle


class IdleLogSource:
    """不主动注入日志的静默日志源。"""

    def precheck(self) -> None:
        return None

    def start(self, line_callback, error_callback) -> None:
        self._line_callback = line_callback
        self._error_callback = error_callback

    def stop(self) -> None:
        return None


class RecordingAudioBackend:
    """In-memory audio backend for custom-noise-duration tests."""

    def __init__(self):
        self.noise_handles: list[RecordingNoiseHandle] = []

    def validate_output(self, _selection, _asset) -> None:
        return None

    def start_noise_loop(self, _selection, _asset) -> RecordingNoiseHandle:
        handle = RecordingNoiseHandle()
        self.noise_handles.append(handle)
        return handle

    def play_once(self, _selection, _asset) -> RecordingPlaybackHandle:
        return RecordingPlaybackHandle()


class FakeRecordingGuardController:
    """Test double for Qualcomm recording-state recovery."""

    def __init__(
        self,
        property_value: str | list[str] = "OFF",
        *,
        property_error: Exception | None = None,
        back_error: Exception | None = None,
    ):
        self.property_value = property_value
        self.property_error = property_error
        self.back_error = back_error
        self.precheck_calls = 0
        self.property_calls = 0
        self.back_calls = 0

    def precheck(self) -> None:
        self.precheck_calls += 1

    def get_property(self, name: str) -> str:
        self.property_calls += 1
        if self.property_error is not None:
            raise self.property_error
        if name != "emdoor.video.state":
            raise AssertionError(name)
        if isinstance(self.property_value, list):
            index = min(self.property_calls - 1, len(self.property_value) - 1)
            return self.property_value[index]
        return self.property_value

    def send_back(self) -> None:
        self.back_calls += 1
        if self.back_error is not None:
            raise self.back_error


class EngineTests(unittest.TestCase):
    """Verify engine behavior in dry-run mode."""

    def test_dry_run_engine_generates_reports_and_deduplicates_match(self) -> None:
        """Duplicate matches should still count as one successful in-window hit."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "timing": {
                        "pre_noise_roll_ms": 10,
                        "trial_interval_ms": 20,
                        "success_window_ms": 200,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        }
                    ],
                }
            )

            def factory(**_kwargs):
                return BurstSyntheticLogSource()

            engine = TestEngine(config=config, dry_run=True, log_source_factory=factory)
            summary = engine.run()

            self.assertEqual(summary["overall"]["total_trials"], 1)
            self.assertEqual(engine.trial_results[0].status, TRIAL_STATUS_PASS)
            matched_window_events = [event for event in engine.events if event.matched_window]
            self.assertEqual(len(matched_window_events), 1)
            self.assertTrue(Path(summary["run_dir"]).exists())

    def test_precheck_resolves_assets_relative_to_config_base_dir(self) -> None:
        """Relative asset paths should resolve against the config directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            assets_dir = temp_path / "assets"
            assets_dir.mkdir()
            wakeup = assets_dir / "wakeup.wav"
            noise = assets_dir / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": "assets/noise.wav",
                            "wakeup_file": "assets/wakeup.wav",
                            "trials": 1,
                        }
                    ],
                },
                base_dir=temp_path,
            )

            engine = TestEngine(config=config, dry_run=True)
            messages = engine.precheck()
            self.assertTrue(any("office" in message for message in messages))

    def test_precheck_reports_active_match_rules(self) -> None:
        """Precheck output should show the currently active log match rules."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "match_rules": [
                        "DMIC wake up",
                        {"type": "regex", "pattern": "M33_WAKEUP_AR1 success!!"},
                    ],
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        }
                    ],
                }
            )

            class StubLogSource:
                def precheck(self) -> None:
                    return None

            engine = TestEngine(config=config, dry_run=True, log_source_factory=lambda **_kwargs: StubLogSource())
            messages = engine.precheck()

            self.assertTrue(any("DMIC wake up" in message for message in messages))
            self.assertTrue(any("M33_WAKEUP_AR1 success!!" in message for message in messages))
            self.assertTrue(any("[keyword]" in message or "[regex]" in message for message in messages))

    def test_precheck_reports_current_config_snapshot(self) -> None:
        """Precheck output should include the active timing and scenario settings."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "mouth-device", "noise_output": "noise-device"},
                    "timing": {
                        "pre_noise_roll_ms": 2000,
                        "trial_interval_ms": 5000,
                        "success_window_ms": 5000,
                    },
                    "match_rules": ["一级唤醒成功"],
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "scene_2",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 5,
                        }
                    ],
                }
            )

            class StubLogSource:
                def precheck(self) -> None:
                    return None

            engine = TestEngine(config=config, dry_run=True, log_source_factory=lambda **_kwargs: StubLogSource())
            messages = engine.precheck()

            self.assertIn("当前预设参数快照:", messages)
            self.assertIn("platform: qualcomm", messages)
            self.assertTrue(any("pre_noise_roll_ms: 2000" in message for message in messages))
            self.assertTrue(any("trial_interval_ms: 5000" in message for message in messages))
            self.assertTrue(any("success_window_ms: 5000" in message for message in messages))
            self.assertTrue(any("trials: 5" in message for message in messages))

    def test_precheck_reports_recording_guard_configuration(self) -> None:
        """Precheck should show the Qualcomm recording guard status and action details."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "recording_guard": {"enabled": True, "settle_ms": 1500},
                    "scenarios": [
                        {
                            "name": "scene_2",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        }
                    ],
                }
            )
            controller = FakeRecordingGuardController()

            class StubLogSource:
                def precheck(self) -> None:
                    return None

            engine = TestEngine(
                config=config,
                dry_run=True,
                log_source_factory=lambda **_kwargs: StubLogSource(),
                adb_controller_factory=lambda **_kwargs: controller,
            )
            messages = engine.precheck()

            self.assertTrue(any("录像态守护: 启用" in message for message in messages))
            self.assertTrue(any("emdoor.video.state" in message for message in messages))
            self.assertTrue(any("ADB BACK" in message for message in messages))
            self.assertTrue(any("1500 ms" in message for message in messages))
            self.assertEqual(controller.precheck_calls, 1)

    def test_precheck_reports_noise_playback_duration_for_each_scenario(self) -> None:
        """Precheck should show whether a scenario uses full-scene noise or a custom duration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "scenarios": [
                        {
                            "name": "full_scene",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        },
                        {
                            "name": "timed_scene",
                            "noise_file": str(noise),
                            "noise_playback_duration_ms": 1800,
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        },
                    ],
                }
            )

            class StubLogSource:
                def precheck(self) -> None:
                    return None

            engine = TestEngine(config=config, dry_run=True, log_source_factory=lambda **_kwargs: StubLogSource())
            messages = engine.precheck()

            self.assertTrue(any("full_scene" in message and "整场景" in message for message in messages))
            self.assertTrue(any("timed_scene" in message and "1800 ms" in message for message in messages))

    def test_precheck_reports_effective_volume_values_for_each_scenario(self) -> None:
        """Precheck should show the exact effective gain details for enabled scenarios."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "scenarios": [
                        {
                            "name": "volume_scene",
                            "noise_file": str(noise),
                            "noise_gain_db": -3.0,
                            "wakeup_file": str(wakeup),
                            "wakeup_gain_db": 2.5,
                            "trials": 1,
                        }
                    ],
                }
            )

            class StubLogSource:
                def precheck(self) -> None:
                    return None

            engine = TestEngine(config=config, dry_run=True, log_source_factory=lambda **_kwargs: StubLogSource())
            messages = engine.precheck()

            self.assertTrue(any("volume_scene" in message and "-3.0 dB (0.708x)" in message for message in messages))
            self.assertTrue(any("volume_scene" in message and "2.5 dB (1.334x)" in message for message in messages))

    def test_custom_noise_duration_stops_noise_and_keeps_remaining_trials_running(self) -> None:
        """Custom noise duration should stop the noise loop without interrupting later trials."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 50,
                        "success_window_ms": 200,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(noise),
                            "noise_playback_duration_ms": 30,
                            "wakeup_file": str(wakeup),
                            "trials": 3,
                        }
                    ],
                }
            )

            backend = RecordingAudioBackend()
            statuses: list[str] = []
            engine = TestEngine(config=config, dry_run=True, audio_backend=backend)

            summary = engine.run(callbacks=EngineCallbacks(on_status=statuses.append))

            self.assertEqual(summary["overall"]["total_trials"], 3)
            self.assertTrue(all(result.status == TRIAL_STATUS_PASS for result in engine.trial_results))
            self.assertEqual(len(backend.noise_handles), 1)
            self.assertEqual(backend.noise_handles[0].stop_calls, 1)
            self.assertTrue(any("30 ms" in message and "后续试次继续执行" in message for message in statuses))

    def test_default_noise_duration_does_not_emit_custom_stop_message(self) -> None:
        """Unset noise duration should keep default behavior and avoid custom-stop status text."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 30,
                        "success_window_ms": 150,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 2,
                        }
                    ],
                }
            )

            backend = RecordingAudioBackend()
            statuses: list[str] = []
            engine = TestEngine(config=config, dry_run=True, audio_backend=backend)

            summary = engine.run(callbacks=EngineCallbacks(on_status=statuses.append))

            self.assertEqual(summary["overall"]["total_trials"], 2)
            self.assertEqual(backend.noise_handles[0].stop_calls, 1)
            self.assertFalse(any("噪声已按自定义时长" in message for message in statuses))

    def test_recording_guard_off_keeps_normal_trial_result(self) -> None:
        """录像态为 OFF 时，本轮应继续按原 PASS/FAIL 逻辑结算。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "recording_guard": {"enabled": True, "settle_ms": 10},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 20,
                        "success_window_ms": 200,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "scene_2",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        }
                    ],
                }
            )
            controller = FakeRecordingGuardController(property_value="OFF")
            engine = TestEngine(
                config=config,
                dry_run=True,
                adb_controller_factory=lambda **_kwargs: controller,
            )

            summary = engine.run()

            self.assertEqual(summary["overall"]["passed_trials"], 1)
            self.assertEqual(engine.trial_results[0].status, TRIAL_STATUS_PASS)
            self.assertEqual(engine.trial_results[0].recording_guard_state, "OFF")
            self.assertFalse(engine.trial_results[0].recording_guard_triggered)
            self.assertEqual(controller.back_calls, 0)
            self.assertTrue(any(event.raw_line == "recording_state=OFF" for event in engine.events))

    def test_recording_guard_on_skips_current_trial_and_continues(self) -> None:
        """录像态为 ON 时，本轮应执行 BACK、记为 SKIPPED，并继续后续试次。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "recording_guard": {"enabled": True, "settle_ms": 10},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 20,
                        "success_window_ms": 200,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "scene_2",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 2,
                        }
                    ],
                }
            )
            controller = FakeRecordingGuardController(property_value=["ON", "OFF"])
            engine = TestEngine(
                config=config,
                dry_run=True,
                adb_controller_factory=lambda **_kwargs: controller,
            )

            summary = engine.run()

            self.assertEqual([result.status for result in engine.trial_results], [TRIAL_STATUS_SKIPPED, TRIAL_STATUS_PASS])
            self.assertEqual(engine.trial_results[0].failure_reason, "检测到录像态，已执行 BACK 并跳过本轮")
            self.assertTrue(engine.trial_results[0].recording_guard_triggered)
            self.assertEqual(engine.trial_results[0].recording_guard_state, "ON")
            self.assertEqual(engine.trial_results[0].recording_guard_recovery_action, "BACK")
            self.assertEqual(engine.trial_results[0].recording_guard_recovery_result, "RECOVERED")
            self.assertEqual(controller.back_calls, 1)
            self.assertEqual(summary["overall"]["recording_guard_triggered"], 1)
            self.assertEqual(summary["overall"]["recording_guard_recovered"], 1)
            self.assertTrue(any(event.raw_line == "recording_recovery_action=BACK" for event in engine.events))

    def test_recording_guard_query_failure_aborts_remaining_trials(self) -> None:
        """录像态查询失败时，当前轮应记为 ERROR，当前场景剩余轮次应被跳过。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "recording_guard": {"enabled": True, "settle_ms": 10},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 20,
                        "success_window_ms": 200,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "scene_2",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 3,
                        }
                    ],
                }
            )
            controller = FakeRecordingGuardController(property_error=RuntimeError("adb shell getprop failed"))
            engine = TestEngine(
                config=config,
                dry_run=True,
                adb_controller_factory=lambda **_kwargs: controller,
            )

            summary = engine.run()

            self.assertEqual(engine.trial_results[0].status, TRIAL_STATUS_ERROR)
            self.assertIn("录像态查询失败", engine.trial_results[0].failure_reason)
            self.assertTrue(all(result.status == TRIAL_STATUS_SKIPPED for result in engine.trial_results[1:]))
            self.assertEqual(summary.get("fatal_error"), None)

    def test_recording_guard_back_failure_aborts_remaining_trials(self) -> None:
        """录像态退出失败时，当前轮应记为 ERROR，当前场景剩余轮次应被跳过。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "recording_guard": {"enabled": True, "settle_ms": 10},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 20,
                        "success_window_ms": 200,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "scene_2",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 3,
                        }
                    ],
                }
            )
            controller = FakeRecordingGuardController(property_value="ON", back_error=RuntimeError("adb back failed"))
            engine = TestEngine(
                config=config,
                dry_run=True,
                adb_controller_factory=lambda **_kwargs: controller,
            )

            summary = engine.run()

            self.assertEqual(engine.trial_results[0].status, TRIAL_STATUS_ERROR)
            self.assertIn("录像态退出失败", engine.trial_results[0].failure_reason)
            self.assertTrue(engine.trial_results[0].recording_guard_triggered)
            self.assertEqual(engine.trial_results[0].recording_guard_recovery_result, "FAILED")
            self.assertTrue(all(result.status == TRIAL_STATUS_SKIPPED for result in engine.trial_results[1:]))
            self.assertEqual(summary["overall"]["recording_guard_triggered"], 1)

    def test_annotate_late_matches_for_reports_marks_late_hit(self) -> None:
        """Late matches should be attached to the failed trial for reporting."""
        config = config_from_dict(
            {
                "platform": "qualcomm",
                "audio_devices": {"mouth_output": "", "noise_output": ""},
                "timing": {
                    "pre_noise_roll_ms": 10,
                    "trial_interval_ms": 5000,
                    "success_window_ms": 3000,
                },
                "match_rules": ["一级唤醒成功"],
                "scenarios": [
                    {
                        "name": "scene_2",
                        "noise_file": "",
                        "wakeup_file": "",
                        "trials": 1,
                    }
                ],
            }
        )

        engine = TestEngine(config=config, dry_run=True)
        failed_trial = TrialResult(
            platform="qualcomm",
            scenario_name="scene_2",
            trial_index=1,
            trial_label="scene_2#1",
            wakeup_started_monotonic=10.0,
            wakeup_started_iso="2026-03-23T15:06:04.116+08:00",
            status=TRIAL_STATUS_FAIL,
            matched=False,
            failure_reason="在成功窗口内未捕获到匹配日志",
        )
        late_event = LogEvent(
            timestamp_monotonic=14.079,
            timestamp_iso="2026-03-23T15:06:08.195+08:00",
            source="adb_logcat",
            raw_line="03-23 15:06:08.306  2802  2802 D SpeechHelpManager: Model 1: 一级唤醒成功",
            matched=True,
            matched_window=False,
        )

        engine._trial_results.append(failed_trial)
        engine._events.append(late_event)

        engine._annotate_late_matches_for_reports()

        self.assertTrue(failed_trial.matched)
        self.assertAlmostEqual(failed_trial.latency_ms or 0.0, 4079.0, places=3)
        self.assertEqual(failed_trial.matched_line, late_event.raw_line)
        self.assertEqual(late_event.trial_label, failed_trial.trial_label)
        self.assertIn("窗口外", failed_trial.failure_reason)

    def test_precheck_reports_empty_asset_path_clearly(self) -> None:
        """Empty audio paths should raise a clear validation error."""
        config = config_from_dict(
            {
                "platform": "rtos",
                "audio_devices": {"mouth_output": "", "noise_output": ""},
                "scenarios": [
                    {
                        "name": "office",
                        "noise_file": "",
                        "wakeup_file": "",
                        "trials": 1,
                    }
                ],
            },
            base_dir=Path.cwd(),
        )

        engine = TestEngine(config=config, dry_run=True)
        with self.assertRaises(AudioValidationError) as context:
            engine.precheck()

        self.assertIn("路径为空", str(context.exception))

    def test_request_stop_interrupts_active_run_audio_and_marks_trial_stopped(self) -> None:
        """用户停止时应同时中断当前唤醒词和噪声，并把当前试次标为 STOPPED。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            noise = temp_path / "noise.wav"
            write_silence_wav(wakeup)
            write_silence_wav(noise)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "timing": {
                        "pre_noise_roll_ms": 0,
                        "trial_interval_ms": 50,
                        "success_window_ms": 500,
                    },
                    "output_root": str(temp_path / "out"),
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(noise),
                            "wakeup_file": str(wakeup),
                            "trials": 2,
                        }
                    ],
                }
            )

            backend = InterruptibleAudioBackend()
            engine = TestEngine(
                config=config,
                dry_run=True,
                audio_backend=backend,
                log_source_factory=lambda **_kwargs: IdleLogSource(),
            )
            summary_box: dict[str, object] = {}

            run_thread = threading.Thread(
                target=lambda: summary_box.setdefault("summary", engine.run()),
                daemon=True,
            )
            run_thread.start()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not backend.playback_handles:
                time.sleep(0.01)

            self.assertTrue(backend.playback_handles)
            self.assertTrue(backend.noise_handles)

            engine.request_stop()
            run_thread.join(timeout=3.0)

            self.assertFalse(run_thread.is_alive())
            self.assertEqual(engine.trial_results[0].status, TRIAL_STATUS_STOPPED)
            self.assertTrue(all(result.status == TRIAL_STATUS_SKIPPED for result in engine.trial_results[1:]))
            self.assertGreaterEqual(backend.playback_handles[0].stop_calls, 1)
            self.assertGreaterEqual(backend.noise_handles[0].stop_calls, 1)
            self.assertIsNone(engine._active_playback_handle)
            self.assertIsNone(engine._active_noise_handle)

    def test_preview_asset_returns_stopped_when_user_interrupts(self) -> None:
        """试听模式被用户停止时应尽快停声并返回 stopped 状态。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            write_silence_wav(wakeup)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(wakeup),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        }
                    ],
                }
            )

            backend = InterruptibleAudioBackend()
            engine = TestEngine(config=config, dry_run=True, audio_backend=backend)
            result_box: dict[str, object] = {}

            preview_thread = threading.Thread(
                target=lambda: result_box.setdefault(
                    "stopped",
                    engine.preview_asset(str(wakeup), "", gain_db=0.0),
                ),
                daemon=True,
            )
            preview_thread.start()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not backend.playback_handles:
                time.sleep(0.01)

            self.assertTrue(backend.playback_handles)

            engine.request_stop()
            preview_thread.join(timeout=2.0)

            self.assertFalse(preview_thread.is_alive())
            self.assertTrue(result_box.get("stopped"))
            self.assertGreaterEqual(backend.playback_handles[0].stop_calls, 1)
            self.assertIsNone(engine._active_playback_handle)

    def test_request_stop_is_idempotent_during_preview(self) -> None:
        """重复停止同一段试听不应导致异常或卡住线程。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wakeup = temp_path / "wakeup.wav"
            write_silence_wav(wakeup)

            config = config_from_dict(
                {
                    "platform": "rtos",
                    "audio_devices": {"mouth_output": "", "noise_output": ""},
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": str(wakeup),
                            "wakeup_file": str(wakeup),
                            "trials": 1,
                        }
                    ],
                }
            )

            backend = InterruptibleAudioBackend()
            engine = TestEngine(config=config, dry_run=True, audio_backend=backend)

            preview_thread = threading.Thread(
                target=lambda: engine.preview_asset(str(wakeup), "", gain_db=0.0),
                daemon=True,
            )
            preview_thread.start()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not backend.playback_handles:
                time.sleep(0.01)

            self.assertTrue(backend.playback_handles)

            engine.request_stop()
            engine.request_stop()
            preview_thread.join(timeout=2.0)

            self.assertFalse(preview_thread.is_alive())
            self.assertGreaterEqual(backend.playback_handles[0].stop_calls, 1)
            self.assertIsNone(engine._active_playback_handle)


if __name__ == "__main__":
    unittest.main()
