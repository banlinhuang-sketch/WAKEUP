"""报告输出测试。"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from voice_wakeup_tester.config import default_config
from voice_wakeup_tester.models import (
    LogEvent,
    TRIAL_STATUS_FAIL,
    TRIAL_STATUS_PASS,
    TRIAL_STATUS_SKIPPED,
    TrialResult,
)
from voice_wakeup_tester.reporting import write_reports


class ReportingTests(unittest.TestCase):
    """验证报表落盘行为。"""

    def test_write_reports_creates_expected_files(self) -> None:
        """报告输出后应产生计划中的四个核心文件。"""
        config = default_config()
        trials = [
            TrialResult(
                platform="rtos",
                scenario_name="scene",
                trial_index=1,
                trial_label="scene#1",
                wakeup_started_monotonic=1.0,
                wakeup_started_iso="2026-01-01T00:00:00+08:00",
                status=TRIAL_STATUS_PASS,
                matched=True,
                latency_ms=321.0,
            ),
            TrialResult(
                platform="rtos",
                scenario_name="scene",
                trial_index=2,
                trial_label="scene#2",
                wakeup_started_monotonic=2.0,
                wakeup_started_iso="2026-01-01T00:00:02+08:00",
                status=TRIAL_STATUS_FAIL,
                matched=False,
                failure_reason="timeout",
            ),
        ]
        events = [
            LogEvent(
                timestamp_monotonic=1.321,
                timestamp_iso="2026-01-01T00:00:01+08:00",
                source="serial",
                raw_line="WAKEUP_SUCCESS",
                matched=True,
                trial_label="scene#1",
                matched_window=True,
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = write_reports(Path(temp_dir), config, trials, events)

            self.assertEqual(summary["overall"]["total_trials"], 2)
            self.assertTrue((Path(temp_dir) / "summary.json").exists())
            self.assertTrue((Path(temp_dir) / "trial_results.csv").exists())
            self.assertTrue((Path(temp_dir) / "event_log.csv").exists())
            self.assertTrue((Path(temp_dir) / "run_config_snapshot.yaml").exists())

    def test_write_reports_includes_recording_guard_fields(self) -> None:
        """录像态守护的试次字段和汇总统计应写入报告。"""
        config = default_config(platform="qualcomm")
        trials = [
            TrialResult(
                platform="qualcomm",
                scenario_name="scene",
                trial_index=1,
                trial_label="scene#1",
                wakeup_started_monotonic=1.0,
                wakeup_started_iso="2026-01-01T00:00:00+08:00",
                status=TRIAL_STATUS_SKIPPED,
                matched=False,
                failure_reason="检测到录像态，已执行 BACK 并跳过本轮",
                recording_guard_triggered=True,
                recording_guard_state="ON",
                recording_guard_recovery_action="BACK",
                recording_guard_recovery_result="RECOVERED",
            ),
            TrialResult(
                platform="qualcomm",
                scenario_name="scene",
                trial_index=2,
                trial_label="scene#2",
                wakeup_started_monotonic=2.0,
                wakeup_started_iso="2026-01-01T00:00:02+08:00",
                status=TRIAL_STATUS_PASS,
                matched=True,
                latency_ms=210.0,
                recording_guard_state="OFF",
            ),
            TrialResult(
                platform="qualcomm",
                scenario_name="scene",
                trial_index=3,
                trial_label="scene#3",
                wakeup_started_monotonic=3.0,
                wakeup_started_iso="2026-01-01T00:00:03+08:00",
                status=TRIAL_STATUS_FAIL,
                matched=False,
                failure_reason="timeout",
                recording_guard_state="OFF",
            ),
        ]
        events = [
            LogEvent(
                timestamp_monotonic=1.1,
                timestamp_iso="2026-01-01T00:00:01+08:00",
                source="recording_guard",
                raw_line="recording_state=ON",
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            summary = write_reports(Path(temp_dir), config, trials, events)
            summary_payload = json.loads((Path(temp_dir) / "summary.json").read_text(encoding="utf-8"))
            with (Path(temp_dir) / "trial_results.csv").open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

            self.assertEqual(summary["overall"]["recording_guard_triggered"], 1)
            self.assertEqual(summary["overall"]["recording_guard_recovered"], 1)
            self.assertEqual(summary_payload["scenarios"]["scene"]["recording_guard_triggered"], 1)
            self.assertEqual(rows[0]["recording_guard_triggered"], "True")
            self.assertEqual(rows[0]["recording_guard_state"], "ON")
            self.assertEqual(rows[0]["recording_guard_recovery_action"], "BACK")
            self.assertEqual(rows[0]["recording_guard_recovery_result"], "RECOVERED")


if __name__ == "__main__":
    unittest.main()
