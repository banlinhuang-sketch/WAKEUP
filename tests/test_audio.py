"""音频资产加载相关测试。"""

from __future__ import annotations

import math
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from voice_wakeup_tester import audio
from voice_wakeup_tester.audio import (
    AudioValidationError,
    list_output_devices,
    load_wav_asset,
    normalize_output_device_selection,
    resolve_output_device,
)


def write_test_wav(path: Path, sample_rate: int = 16000, duration_seconds: float = 0.1) -> None:
    """生成一段简单的测试 WAV，供单测复用。"""
    sample_count = int(sample_rate * duration_seconds)
    values = [
        int(32767 * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
        for index in range(sample_count)
    ]
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(np.array(values, dtype=np.int16).tobytes())


class AudioTests(unittest.TestCase):
    """验证音频文件读取与基础异常处理。"""

    def test_load_wav_asset_returns_expected_shape(self) -> None:
        """应能正确读取单声道 WAV 并转成二维数组。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tone.wav"
            write_test_wav(path)

            asset = load_wav_asset(path)

            self.assertEqual(asset.sample_rate, 16000)
            self.assertEqual(asset.channels, 1)
            self.assertGreater(len(asset.samples), 0)
            self.assertEqual(asset.samples.ndim, 2)

    def test_missing_asset_raises(self) -> None:
        """不存在的文件应抛出校验异常。"""
        with self.assertRaises(AudioValidationError):
            load_wav_asset("missing.wav")

    def test_resolve_output_device_accepts_gui_label(self) -> None:
        """GUI 下拉框里的“索引: 名称”格式也应能正确解析。"""
        fake_backend = mock.Mock()
        fake_backend.query_devices.return_value = [
            {"name": "Microsoft 声音映射器 - Output", "max_output_channels": 2},
            {"name": "Realtek HD Audio 2nd output", "max_output_channels": 2},
        ]

        with mock.patch.object(audio, "sd", fake_backend):
            index, device = resolve_output_device("1: Realtek HD Audio 2nd output")

        self.assertEqual(index, 1)
        self.assertEqual(device["name"], "Realtek HD Audio 2nd output")

    def test_resolve_output_device_falls_back_to_name_when_index_changes(self) -> None:
        """蓝牙重连后如果索引漂移，旧标签也应优先按设备名匹配。"""
        fake_backend = mock.Mock()
        fake_backend.query_devices.return_value = [
            {"name": "Speakers", "max_output_channels": 2},
            {"name": "BT Speaker", "max_output_channels": 2},
        ]

        with mock.patch.object(audio, "sd", fake_backend):
            index, device = resolve_output_device("0: BT Speaker")

        self.assertEqual(index, 1)
        self.assertEqual(device["name"], "BT Speaker")

    def test_normalize_output_device_selection_returns_stable_device_name(self) -> None:
        """保存配置时应尽量把 GUI 标签归一化为稳定的设备名。"""
        fake_backend = mock.Mock()
        fake_backend.query_devices.return_value = [
            {"name": "Speakers", "max_output_channels": 2},
            {"name": "BT Speaker", "max_output_channels": 2},
        ]

        with mock.patch.object(audio, "sd", fake_backend):
            normalized = normalize_output_device_selection("1: BT Speaker [Bluetooth]")

        self.assertEqual(normalized, "BT Speaker")

    def test_list_output_devices_marks_bluetooth_and_handsfree(self) -> None:
        """设备列表应标记蓝牙输出与 Hands-Free 通话模式。"""
        fake_backend = mock.Mock()
        fake_backend.query_devices.return_value = [
            {"name": "USB Speaker", "max_output_channels": 2, "hostapi": 0, "default_samplerate": 48000},
            {"name": "JBL Flip 6 (Bluetooth)", "max_output_channels": 2, "hostapi": 0, "default_samplerate": 48000},
            {"name": "WH-1000XM5 Hands-Free AG Audio", "max_output_channels": 1, "hostapi": 0, "default_samplerate": 16000},
        ]

        with mock.patch.object(audio, "sd", fake_backend):
            devices = list_output_devices()

        self.assertFalse(devices[0]["is_bluetooth"])
        self.assertTrue(devices[1]["is_bluetooth"])
        self.assertFalse(devices[1]["is_handsfree"])
        self.assertTrue(devices[2]["is_handsfree"])


if __name__ == "__main__":
    unittest.main()
