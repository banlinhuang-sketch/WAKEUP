"""配置加载与默认值回填测试。"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

from voice_wakeup_tester.config import config_from_dict, load_config, save_config


class ConfigTests(unittest.TestCase):
    """验证 YAML 配置加载逻辑。"""

    def test_load_config_applies_defaults(self) -> None:
        """缺省字段应被默认值正确补齐。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    platform: qualcomm
                    dut:
                      adb_serial: ABC123
                    audio_devices:
                      mouth_output: '0: Mouth'
                      noise_output: '1: Noise'
                    scenarios:
                      - name: office
                        noise_file: office.wav
                        wakeup_file: wakeup.wav
                        trials: 3
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(path)

            self.assertEqual(config.platform, "qualcomm")
            self.assertEqual(config.dut.adb_serial, "ABC123")
            self.assertEqual(config.timing.success_window_ms, 3000)
            self.assertEqual(config.match_rules[0].pattern, "AudioHAL: Voice wake up triggered")
            self.assertEqual(config.scenarios[0].trials, 3)
            self.assertEqual(config.scenarios[0].noise_playback_duration_ms, 0)
            self.assertEqual(Path(config.base_dir), path.parent)

    def test_load_config_migrates_legacy_qualcomm_success_rule(self) -> None:
        """已知旧版 Qualcomm 单规则配置应自动补上更早的 DMIC 命中规则。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    platform: qualcomm
                    match_rules:
                      - type: keyword
                        pattern: '-----------------------------------------M33_WAKEUP_AR1 success!! ----------------------------------------------------------'
                    timing:
                      success_window_ms: 3000
                    scenarios:
                      - name: office
                        noise_file: office.wav
                        wakeup_file: wakeup.wav
                        trials: 3
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(path)

            self.assertEqual(
                [rule.pattern for rule in config.match_rules],
                [
                    "DMIC wake up",
                    "-----------------------------------------M33_WAKEUP_AR1 success!! ----------------------------------------------------------",
                ],
            )

    def test_load_config_supports_noise_playback_duration(self) -> None:
        """场景级噪声播放时长应从 YAML 正确读入。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    platform: rtos
                    scenarios:
                      - name: office
                        noise_file: office.wav
                        noise_playback_duration_ms: 1800
                        wakeup_file: wakeup.wav
                        trials: 3
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(path)

            self.assertEqual(config.scenarios[0].noise_playback_duration_ms, 1800)

    def test_load_config_supports_recording_guard(self) -> None:
        """Qualcomm 录像态守护配置应从 YAML 正确读入。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    platform: qualcomm
                    dut:
                      adb_serial: ABC123
                    recording_guard:
                      enabled: true
                      settle_ms: 1500
                    scenarios:
                      - name: office
                        noise_file: office.wav
                        wakeup_file: wakeup.wav
                        trials: 3
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(path)

            self.assertTrue(config.recording_guard.enabled)
            self.assertEqual(config.recording_guard.settle_ms, 1500)

    def test_save_config_persists_recording_guard(self) -> None:
        """录像态守护配置应能稳定回写到 YAML 快照。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            config = config_from_dict(
                {
                    "platform": "qualcomm",
                    "dut": {"adb_serial": "ABC123"},
                    "recording_guard": {"enabled": True, "settle_ms": 1800},
                    "scenarios": [
                        {
                            "name": "office",
                            "noise_file": "office.wav",
                            "wakeup_file": "wakeup.wav",
                            "trials": 3,
                        }
                    ],
                },
                base_dir=temp_dir,
            )

            save_config(path, config)

            payload = yaml.safe_load(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["recording_guard"]["enabled"], True)
            self.assertEqual(payload["recording_guard"]["settle_ms"], 1800)


if __name__ == "__main__":
    unittest.main()
