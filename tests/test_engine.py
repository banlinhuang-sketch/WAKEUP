"""Dry-run integration tests for the test engine."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from voice_wakeup_tester.audio import AudioValidationError
from voice_wakeup_tester.config import config_from_dict
from voice_wakeup_tester.dut import SyntheticLogSource
from voice_wakeup_tester.engine import TestEngine
from voice_wakeup_tester.models import LogEvent, TRIAL_STATUS_FAIL, TRIAL_STATUS_PASS, TrialResult


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


if __name__ == "__main__":
    unittest.main()
