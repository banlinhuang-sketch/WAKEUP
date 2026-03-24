"""GUI 自定义次数交互测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if os.environ.get("SKIP_GUI_TESTS") == "1":
    pytest.skip("GUI tests are disabled in this environment.", allow_module_level=True)

try:
    from PySide6 import QtCore, QtWidgets
except ImportError as exc:  # pragma: no cover - depends on CI/runtime Qt availability
    pytest.skip(f"PySide6 runtime unavailable: {exc}", allow_module_level=True)

from voice_wakeup_tester.config import config_from_dict
from voice_wakeup_tester.gui import (
    MainWindow,
    SCENARIO_COL_NOISE_GAIN,
    SCENARIO_COL_NOISE_DURATION,
    SCENARIO_COL_TRIALS,
    SCENARIO_COL_WAKEUP_GAIN,
)
from voice_wakeup_tester.models import ScenarioConfig


def build_config(base_dir: Path, scenarios: list[dict]) -> object:
    """生成适合 GUI 测试的最小配置对象。"""
    config = config_from_dict(
        {
            "platform": "rtos",
            "audio_devices": {"mouth_output": "", "noise_output": ""},
            "scenarios": scenarios,
        },
        base_dir=base_dir,
    )
    config.scenarios = [
        ScenarioConfig(
            name=str(item.get("name", "")).strip() or f"scene_{index + 1}",
            noise_file=str(item.get("noise_file", "")).strip(),
            noise_gain_db=float(item.get("noise_gain_db", 0.0)),
            noise_playback_duration_ms=int(item.get("noise_playback_duration_ms", 0)),
            wakeup_file=str(item.get("wakeup_file", "")).strip(),
            wakeup_gain_db=float(item.get("wakeup_gain_db", 0.0)),
            trials=int(item.get("trials", 10)),
            enabled=bool(item.get("enabled", True)),
        )
        for index, item in enumerate(scenarios)
    ]
    return config


class CustomTrialsGuiTests(unittest.TestCase):
    """验证自定义次数批量编辑的 GUI 行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        """保证整组测试共享一个离屏 QApplication。"""
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        """为每个测试构建独立窗口与临时目录。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_root = Path(self.temp_dir.name)

    def _create_window(self, scenarios: list[dict]) -> MainWindow:
        """创建主窗口并跳过真实设备枚举。"""
        config = build_config(self.project_root, scenarios)
        patcher = mock.patch.object(MainWindow, "_refresh_device_lists", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        window = MainWindow(project_root=self.project_root, initial_config=config, dry_run=True)
        self.addCleanup(window.close)
        return window

    def _select_rows(self, window: MainWindow, rows: list[int]) -> None:
        """在场景表中选择一个或多个整行。"""
        window.scenario_table.clearSelection()
        selection_model = window.scenario_table.selectionModel()
        flags = (
            QtCore.QItemSelectionModel.SelectionFlag.Select
            | QtCore.QItemSelectionModel.SelectionFlag.Rows
        )
        for row in rows:
            index = window.scenario_table.model().index(row, 0)
            selection_model.select(index, flags)
        QtWidgets.QApplication.processEvents()

    def test_scope_helpers_return_selected_enabled_and_all_rows(self) -> None:
        """作用域取行逻辑应稳定覆盖选中、启用与全部场景。"""
        window = self._create_window(
            [
                {"name": "scene_1", "noise_file": "", "wakeup_file": "", "trials": 3, "enabled": True},
                {"name": "scene_2", "noise_file": "", "wakeup_file": "", "trials": 5, "enabled": False},
                {"name": "scene_3", "noise_file": "", "wakeup_file": "", "trials": 7, "enabled": True},
            ]
        )

        self._select_rows(window, [0, 2])

        self.assertEqual(window._scenario_rows_for_scope("selected"), [0, 2])
        self.assertEqual(window._scenario_rows_for_scope("enabled"), [0, 2])
        self.assertEqual(window._scenario_rows_for_scope("all"), [0, 1, 2])

    def test_mixed_selection_keeps_spin_value_and_shows_hint(self) -> None:
        """混合次数选择时不应静默覆盖输入框，而应给出明确提示。"""
        window = self._create_window(
            [
                {"name": "scene_1", "noise_file": "", "wakeup_file": "", "trials": 3, "enabled": True},
                {"name": "scene_2", "noise_file": "", "wakeup_file": "", "trials": 7, "enabled": True},
            ]
        )
        window.custom_trials_spin.setValue(10)

        self._select_rows(window, [0, 1])

        self.assertEqual(window.custom_trials_spin.value(), 10)
        self.assertIn("试次不一致", window.custom_trials_hint_label.text())
        self.assertIn("当前输入 10 次", window.custom_trials_hint_label.text())

        self._select_rows(window, [0])

        self.assertEqual(window.custom_trials_spin.value(), 3)
        self.assertIn("当前试次为 3 次", window.custom_trials_hint_label.text())

    def test_apply_enabled_scope_only_updates_enabled_rows(self) -> None:
        """“应用到启用场景”应只改已启用行，并回写明确状态文案。"""
        window = self._create_window(
            [
                {"name": "scene_1", "noise_file": "", "wakeup_file": "", "trials": 1, "enabled": True},
                {"name": "scene_2", "noise_file": "", "wakeup_file": "", "trials": 2, "enabled": False},
                {"name": "scene_3", "noise_file": "", "wakeup_file": "", "trials": 3, "enabled": True},
            ]
        )
        window.custom_trials_spin.setValue(9)

        window._apply_custom_trials("enabled")

        self.assertEqual(window.scenario_table.item(0, SCENARIO_COL_TRIALS).text(), "9")
        self.assertEqual(window.scenario_table.item(1, SCENARIO_COL_TRIALS).text(), "2")
        self.assertEqual(window.scenario_table.item(2, SCENARIO_COL_TRIALS).text(), "9")
        self.assertEqual(window.status_label.text(), "已将 2 条启用场景的试次设置为 9 次")

    def test_apply_selected_scope_requires_selection(self) -> None:
        """未选中场景时，应阻止“应用到选中场景”并给出提示。"""
        window = self._create_window(
            [
                {"name": "scene_1", "noise_file": "", "wakeup_file": "", "trials": 4, "enabled": True},
                {"name": "scene_2", "noise_file": "", "wakeup_file": "", "trials": 6, "enabled": True},
            ]
        )
        window.scenario_table.clearSelection()
        QtWidgets.QApplication.processEvents()

        with mock.patch.object(QtWidgets.QMessageBox, "information") as message_box:
            window._apply_custom_trials("selected")

        message_box.assert_called_once()
        self.assertIn("请先选中至少一条场景。", message_box.call_args.args[2])

    def test_volume_details_show_single_selected_scenario_values(self) -> None:
        """单选场景时应显示该场景的具体噪声和唤醒词音量。"""
        window = self._create_window(
            [
                {
                    "name": "scene_1",
                    "noise_file": "",
                    "noise_gain_db": -3.0,
                    "wakeup_file": "",
                    "wakeup_gain_db": 2.5,
                    "trials": 4,
                    "enabled": True,
                }
            ]
        )

        self._select_rows(window, [0])

        self.assertEqual(window.volume_details_scope_label.text(), "当前场景：scene_1")
        self.assertEqual(window.noise_volume_details_label.text(), "-3.0 dB (0.708x)")
        self.assertEqual(window.wakeup_volume_details_label.text(), "2.5 dB (1.334x)")

    def test_volume_details_show_same_value_for_multi_select(self) -> None:
        """多选且增益一致时，音量详情应显示共享的具体数值。"""
        window = self._create_window(
            [
                {
                    "name": "scene_1",
                    "noise_file": "",
                    "noise_gain_db": 1.0,
                    "wakeup_file": "",
                    "wakeup_gain_db": -2.0,
                    "trials": 4,
                    "enabled": True,
                },
                {
                    "name": "scene_2",
                    "noise_file": "",
                    "noise_gain_db": 1.0,
                    "wakeup_file": "",
                    "wakeup_gain_db": -2.0,
                    "trials": 6,
                    "enabled": True,
                },
            ]
        )

        self._select_rows(window, [0, 1])

        self.assertEqual(window.volume_details_scope_label.text(), "已选 2 条场景")
        self.assertEqual(window.noise_volume_details_label.text(), "1.0 dB (1.122x)")
        self.assertEqual(window.wakeup_volume_details_label.text(), "-2.0 dB (0.794x)")

    def test_volume_details_show_mixed_value_hint_for_multi_select(self) -> None:
        """多选且增益不一致时，应显示混合值提示而不是误导性的单值。"""
        window = self._create_window(
            [
                {
                    "name": "scene_1",
                    "noise_file": "",
                    "noise_gain_db": 0.0,
                    "wakeup_file": "",
                    "wakeup_gain_db": -1.0,
                    "trials": 4,
                    "enabled": True,
                },
                {
                    "name": "scene_2",
                    "noise_file": "",
                    "noise_gain_db": -6.0,
                    "wakeup_file": "",
                    "wakeup_gain_db": -1.0,
                    "trials": 6,
                    "enabled": True,
                },
            ]
        )

        self._select_rows(window, [0, 1])

        self.assertEqual(window.volume_details_scope_label.text(), "已选 2 条场景")
        self.assertEqual(window.noise_volume_details_label.text(), "混合值")
        self.assertEqual(window.wakeup_volume_details_label.text(), "-1.0 dB (0.891x)")

    def test_volume_details_refresh_after_gain_edit(self) -> None:
        """编辑增益列后，音量详情应立即刷新。"""
        window = self._create_window(
            [
                {
                    "name": "scene_1",
                    "noise_file": "",
                    "noise_gain_db": 0.0,
                    "wakeup_file": "",
                    "wakeup_gain_db": 0.0,
                    "trials": 4,
                    "enabled": True,
                }
            ]
        )

        self._select_rows(window, [0])
        window.scenario_table.item(0, SCENARIO_COL_NOISE_GAIN).setText("-6.0")
        window.scenario_table.item(0, SCENARIO_COL_WAKEUP_GAIN).setText("3.0")
        QtWidgets.QApplication.processEvents()

        self.assertEqual(window.noise_volume_details_label.text(), "-6.0 dB (0.501x)")
        self.assertEqual(window.wakeup_volume_details_label.text(), "3.0 dB (1.413x)")

    def test_new_scenario_inherits_current_custom_trials_value(self) -> None:
        """新增场景应继续沿用当前自定义次数输入值。"""
        window = self._create_window([])
        window.custom_trials_spin.setValue(12)

        window._append_empty_scenario()

        self.assertEqual(window.scenario_table.rowCount(), 1)
        self.assertEqual(window.scenario_table.item(0, SCENARIO_COL_TRIALS).text(), "12")

    def test_noise_playback_duration_round_trips_through_table_and_ui(self) -> None:
        """场景表应能显示并保存每个场景的噪声播放时长。"""
        window = self._create_window(
            [
                {
                    "name": "scene_1",
                    "noise_file": "",
                    "noise_playback_duration_ms": 1500,
                    "wakeup_file": "",
                    "trials": 4,
                    "enabled": True,
                }
            ]
        )

        self.assertEqual(window.scenario_table.item(0, SCENARIO_COL_NOISE_DURATION).text(), "1500")
        window.scenario_table.item(0, SCENARIO_COL_NOISE_DURATION).setText("2300")

        config = window._config_from_ui()

        self.assertEqual(config.scenarios[0].noise_playback_duration_ms, 2300)

    def test_recording_guard_round_trips_through_ui(self) -> None:
        """Qualcomm 录像态守护开关和等待时间应能正确回写配置。"""
        config = config_from_dict(
            {
                "platform": "qualcomm",
                "dut": {"adb_serial": "ABC123"},
                "recording_guard": {"enabled": True, "settle_ms": 1800},
                "audio_devices": {"mouth_output": "", "noise_output": ""},
                "scenarios": [
                    {
                        "name": "scene_1",
                        "noise_file": "",
                        "wakeup_file": "",
                        "trials": 4,
                        "enabled": True,
                    }
                ],
            },
            base_dir=self.project_root,
        )
        patcher = mock.patch.object(MainWindow, "_refresh_device_lists", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        window = MainWindow(project_root=self.project_root, initial_config=config, dry_run=True)
        self.addCleanup(window.close)

        self.assertTrue(window.recording_guard_checkbox.isChecked())
        self.assertEqual(window.recording_guard_settle_spin.value(), 1800)

        window.recording_guard_checkbox.setChecked(False)
        window.recording_guard_settle_spin.setValue(900)

        updated = window._config_from_ui()

        self.assertFalse(updated.recording_guard.enabled)
        self.assertEqual(updated.recording_guard.settle_ms, 900)

    def test_recording_guard_controls_follow_platform_visibility(self) -> None:
        """录像态守护控件应只在 Qualcomm 平台下可编辑。"""
        window = self._create_window(
            [
                {"name": "scene_1", "noise_file": "", "wakeup_file": "", "trials": 4, "enabled": True},
            ]
        )

        window.platform_combo.setCurrentText("rtos")
        QtWidgets.QApplication.processEvents()
        self.assertFalse(window.recording_guard_checkbox.isEnabled())
        self.assertFalse(window.recording_guard_settle_spin.isEnabled())

        window.platform_combo.setCurrentText("qualcomm")
        QtWidgets.QApplication.processEvents()
        self.assertTrue(window.recording_guard_checkbox.isEnabled())
        self.assertTrue(window.recording_guard_settle_spin.isEnabled())

    def test_stop_current_task_calls_worker_immediately_and_updates_status(self) -> None:
        """点击停止应直接调用当前 worker 的停止方法，并显示停止中文案。"""
        window = self._create_window([])

        class FakeWorker:
            def __init__(self) -> None:
                self.stop_calls = 0

            def request_stop(self) -> None:
                self.stop_calls += 1

        worker = FakeWorker()
        window._task_worker = worker

        window._stop_current_task()

        self.assertEqual(worker.stop_calls, 1)
        self.assertEqual(window.status_label.text(), "停止中，等待当前音频收尾")

    def test_handle_worker_done_shows_stopped_state_for_run_and_preview(self) -> None:
        """运行和试听被用户停止后，界面应显示已停止而不是已完成。"""
        window = self._create_window([])

        window._handle_worker_done(
            {
                "mode": "run",
                "summary": {"run_dir": "C:/temp/run_001"},
                "stopped": True,
            }
        )
        self.assertEqual(window.status_label.text(), "测试已停止")
        self.assertEqual(window.run_dir_label.text(), "C:/temp/run_001")
        self.assertIn("[stopped] 用户手动停止", window.log_output.toPlainText())

        window._handle_worker_done(
            {
                "mode": "preview",
                "asset": "demo.wav",
                "stopped": True,
            }
        )
        self.assertEqual(window.status_label.text(), "试听已停止")
        self.assertIn("已停止试听: demo.wav", window.log_output.toPlainText())


if __name__ == "__main__":
    unittest.main()
