"""音频资产加载、设备解析与播放后端封装。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import threading
import time
from typing import Any
import wave

import numpy as np


try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - exercised by environment checks instead
    sd = None


class AudioValidationError(RuntimeError):
    """音频文件或设备参数不合法时抛出的异常。"""

    pass


class AudioDependencyError(RuntimeError):
    """缺少底层音频依赖时抛出的异常。"""

    pass


BLUETOOTH_KEYWORDS = ("bluetooth", "蓝牙", "a2dp")
HANDSFREE_KEYWORDS = ("hands-free", "handsfree", "ag audio", "hfp", "免提")


@dataclass(slots=True)
class AudioAsset:
    """内存中的 WAV 资产。"""

    path: Path
    sample_rate: int
    channels: int
    samples: np.ndarray

    @property
    def duration_seconds(self) -> float:
        """按采样率计算音频时长。"""
        if self.sample_rate <= 0:
            return 0.0
        return float(len(self.samples)) / float(self.sample_rate)

    def with_gain(self, gain_db: float) -> "AudioAsset":
        """对样本应用 dB 增益并返回新对象。"""
        if abs(gain_db) < 1e-9:
            return self
        gain = float(10 ** (gain_db / 20.0))
        boosted = np.clip(self.samples * gain, -1.0, 1.0).astype(np.float32, copy=False)
        return AudioAsset(
            path=self.path,
            sample_rate=self.sample_rate,
            channels=self.channels,
            samples=boosted,
        )


def _require_sounddevice():
    """确保运行环境已经安装 sounddevice。"""
    if sd is None:
        raise AudioDependencyError(
            "sounddevice is not installed. Install it with `pip install sounddevice`."
        )
    return sd


def _pcm_to_float32(raw_bytes: bytes, sample_width: int) -> np.ndarray:
    """把不同位深的 PCM 数据统一转换为 float32。"""
    if sample_width == 1:
        return (np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if sample_width == 2:
        return np.frombuffer(raw_bytes, dtype="<i2").astype(np.float32) / 32768.0
    if sample_width == 3:
        raw = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(-1, 3)
        values = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        sign_bit = values & 0x800000
        values = values - (sign_bit << 1)
        return values.astype(np.float32) / 8388608.0
    if sample_width == 4:
        return np.frombuffer(raw_bytes, dtype="<i4").astype(np.float32) / 2147483648.0
    raise AudioValidationError(f"Unsupported PCM sample width: {sample_width} bytes")


def load_wav_asset(path: str | Path) -> AudioAsset:
    """读取 PCM WAV 文件并转换为统一的浮点格式。"""
    asset_path = Path(path)
    if not asset_path.exists():
        raise AudioValidationError(f"Audio asset not found: {asset_path}")
    with wave.open(str(asset_path), "rb") as wav_file:
        if wav_file.getcomptype() != "NONE":
            raise AudioValidationError(f"Only PCM WAV is supported: {asset_path}")
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())
    samples = _pcm_to_float32(frames, sample_width)
    if channels <= 0:
        raise AudioValidationError(f"Invalid channel count in asset: {asset_path}")
    # 声音播放层统一要求二维数组，形状为 [frame, channel]。
    reshaped = samples.reshape(-1, channels).astype(np.float32, copy=False)
    return AudioAsset(path=asset_path, sample_rate=sample_rate, channels=channels, samples=reshaped)


def classify_output_device_name(name: str) -> dict[str, bool]:
    """根据设备名推断是否为蓝牙输出或 Hands-Free 通话模式。"""
    normalized = str(name).strip().lower()
    return {
        "is_bluetooth": any(keyword in normalized for keyword in BLUETOOTH_KEYWORDS),
        "is_handsfree": any(keyword in normalized for keyword in HANDSFREE_KEYWORDS),
    }


def format_output_device_label(device: dict[str, Any]) -> str:
    """把设备信息格式化成 GUI/CLI 共用的可读标签。"""
    name = str(device.get("name", ""))
    tags: list[str] = []
    if bool(device.get("is_bluetooth", False)):
        tags.append("Bluetooth")
    if bool(device.get("is_handsfree", False)):
        tags.append("Hands-Free/通话模式")
    suffix = f" [{' | '.join(tags)}]" if tags else ""
    return f"{device['index']}: {name}{suffix}"


def _extract_device_name(selection: str | int) -> str:
    """从 GUI 标签里提取设备名，兼容末尾追加的能力标签。"""
    text = str(selection).strip()
    if not text:
        return ""
    match = re.match(r"^\d+\s*:\s*(.+?)(?:\s+\[[^\]]+\])?$", text)
    if match:
        return match.group(1).strip()
    return text


def list_output_devices() -> list[dict[str, str | int]]:
    """列出所有支持输出的声卡设备。"""
    backend = _require_sounddevice()
    devices = backend.query_devices()
    results: list[dict[str, str | int]] = []
    for index, device in enumerate(devices):
        if int(device.get("max_output_channels", 0)) <= 0:
            continue
        name = str(device.get("name", f"device_{index}"))
        flags = classify_output_device_name(name)
        results.append(
            {
                "index": index,
                "name": name,
                "hostapi": int(device.get("hostapi", -1)),
                "max_output_channels": int(device.get("max_output_channels", 0)),
                "default_samplerate": int(float(device.get("default_samplerate", 0))),
                "is_bluetooth": flags["is_bluetooth"],
                "is_handsfree": flags["is_handsfree"],
            }
        )
    return results


def _parse_device_index(selection: str | int) -> int | None:
    """兼容纯索引和 GUI 下拉框里的“索引: 设备名”格式。"""
    if isinstance(selection, int):
        return selection
    text = str(selection).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.match(r"^(\d+)\s*:", text)
    if match:
        return int(match.group(1))
    return None


def resolve_output_device(selection: str | int) -> tuple[int, dict]:
    """把用户输入的设备索引或名称解析成 sounddevice 设备对象。"""
    backend = _require_sounddevice()
    devices = backend.query_devices()
    if selection is None or str(selection).strip() == "":
        raise AudioValidationError("Output device is required.")
    parsed_index = _parse_device_index(selection)
    embedded_name = _extract_device_name(selection)
    if parsed_index is not None:
        index = parsed_index
        if 0 <= index < len(devices):
            device = devices[index]
            device_name = str(device.get("name", "")).strip()
            # 如果“索引: 设备名”里的索引已经因为蓝牙重连而漂移，则回退到设备名匹配。
            if int(device.get("max_output_channels", 0)) > 0 and (
                not embedded_name or device_name.casefold() == embedded_name.casefold()
            ):
                return index, device
        elif embedded_name == str(index):
            raise AudioValidationError(f"Audio device index out of range: {index}")

    query_text = embedded_name or str(selection).strip()
    query = query_text.lower()
    exact_match: tuple[int, dict] | None = None
    partial_matches: list[tuple[int, dict]] = []
    for index, device in enumerate(devices):
        if int(device.get("max_output_channels", 0)) <= 0:
            continue
        name = str(device.get("name", ""))
        normalized = name.lower()
        if normalized == query:
            exact_match = (index, device)
            break
        if query in normalized:
            partial_matches.append((index, device))
    if exact_match is not None:
        return exact_match
    if len(partial_matches) == 1:
        return partial_matches[0]
    if not partial_matches:
        raise AudioValidationError(
            f"未找到音频输出设备: {selection}。设备可能已断开，或蓝牙重连后索引发生变化，请点击“刷新音频/蓝牙设备”后重新选择。"
        )
    names = ", ".join(f"{index}:{device['name']}" for index, device in partial_matches)
    raise AudioValidationError(f"音频输出设备“{selection}”匹配到多个候选，请改用更完整的设备名。候选: {names}")


def normalize_output_device_selection(selection: str | int) -> str:
    """把 GUI/CLI 中的设备选择值归一化为稳定的设备名。"""
    text = str(selection).strip()
    if not text:
        return ""
    try:
        _index, device = resolve_output_device(selection)
        return str(device.get("name", text)).strip() or text
    except AudioValidationError:
        return _extract_device_name(text)


class PlaybackHandle:
    """单次播放的等待句柄。"""

    def __init__(self, started_at_monotonic: float, error: Exception | None = None):
        self.started_at_monotonic = started_at_monotonic
        self._done = threading.Event()
        self._done.set()
        self._error = error

    def wait(self, timeout: float | None = None) -> None:
        """等待播放完成，如播放线程报错则在此抛出。"""
        self._done.wait(timeout=timeout)
        if self._error:
            raise self._error


class NoiseLoopHandle:
    """持续噪声播放的控制句柄。"""

    def stop(self) -> None:
        return None


class DryRunAudioBackend:
    """dry-run 模式下的伪造音频后端。"""

    def list_output_devices(self) -> list[dict[str, str | int]]:
        return [
            {"index": 0, "name": "DryRun Mouth", "max_output_channels": 1, "default_samplerate": 16000},
            {"index": 1, "name": "DryRun Noise", "max_output_channels": 2, "default_samplerate": 16000},
        ]

    def validate_output(self, selection: str | int, asset: AudioAsset) -> None:
        return None

    def start_noise_loop(self, selection: str | int, asset: AudioAsset) -> NoiseLoopHandle:
        return NoiseLoopHandle()

    def play_once(self, selection: str | int, asset: AudioAsset) -> PlaybackHandle:
        return PlaybackHandle(started_at_monotonic=time.monotonic())


class _SoundDevicePlaybackHandle(PlaybackHandle):
    """真实设备上的单次音频播放句柄。"""

    def __init__(self, device_index: int, asset: AudioAsset):
        self._backend = _require_sounddevice()
        self._device_index = device_index
        self._asset = asset
        self._done = threading.Event()
        self._started = threading.Event()
        self._error: Exception | None = None
        self.started_at_monotonic = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=2.0):
            raise AudioValidationError("Timed out while starting playback stream.")
        if self._error:
            raise self._error

    def _run(self) -> None:
        position = 0
        finished = threading.Event()

        def callback(outdata, frames, _time_info, _status):
            nonlocal position
            outdata.fill(0)
            end = min(position + frames, len(self._asset.samples))
            chunk = self._asset.samples[position:end]
            if len(chunk):
                outdata[: len(chunk)] = chunk
            position = end
            # 播到末尾后主动停止 callback，让 OutputStream 自然收尾。
            if position >= len(self._asset.samples):
                raise self._backend.CallbackStop()

        try:
            with self._backend.OutputStream(
                device=self._device_index,
                samplerate=self._asset.sample_rate,
                channels=self._asset.channels,
                dtype="float32",
                callback=callback,
                finished_callback=finished.set,
            ):
                self.started_at_monotonic = time.monotonic()
                self._started.set()
                finished.wait(timeout=max(self._asset.duration_seconds + 5.0, 5.0))
        except Exception as exc:  # pragma: no cover - hardware specific
            self._error = exc
            self._started.set()
        finally:
            self._done.set()


class _SoundDeviceNoiseLoopHandle(NoiseLoopHandle):
    """真实设备上的循环噪声播放句柄。"""

    def __init__(self, device_index: int, asset: AudioAsset):
        self._backend = _require_sounddevice()
        self._device_index = device_index
        self._asset = asset
        self._stop_requested = threading.Event()
        self._started = threading.Event()
        self._done = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=2.0):
            raise AudioValidationError("Timed out while starting noise stream.")
        if self._error:
            raise self._error

    def _run(self) -> None:
        position = 0
        finished = threading.Event()

        def callback(outdata, frames, _time_info, _status):
            nonlocal position
            outdata.fill(0)
            if self._stop_requested.is_set():
                raise self._backend.CallbackStop()
            remaining = frames
            offset = 0
            while remaining > 0:
                available = len(self._asset.samples) - position
                chunk_size = min(available, remaining)
                chunk = self._asset.samples[position : position + chunk_size]
                outdata[offset : offset + chunk_size] = chunk
                offset += chunk_size
                remaining -= chunk_size
                position += chunk_size
                # 到达素材尾部后回绕，实现无缝循环噪声。
                if position >= len(self._asset.samples):
                    position = 0

        try:
            with self._backend.OutputStream(
                device=self._device_index,
                samplerate=self._asset.sample_rate,
                channels=self._asset.channels,
                dtype="float32",
                callback=callback,
                finished_callback=finished.set,
            ):
                self._started.set()
                finished.wait()
        except Exception as exc:  # pragma: no cover - hardware specific
            self._error = exc
            self._started.set()
        finally:
            self._done.set()

    def stop(self) -> None:
        """请求循环噪声停止。"""
        self._stop_requested.set()
        self._done.wait(timeout=3.0)
        if self._error:
            raise self._error


class SoundDeviceAudioBackend:
    """基于 sounddevice 的真实音频后端。"""

    def list_output_devices(self) -> list[dict[str, str | int]]:
        return list_output_devices()

    def validate_output(self, selection: str | int, asset: AudioAsset) -> None:
        """用 sounddevice 原生校验设备输出能力。"""
        backend = _require_sounddevice()
        device_index, _device = resolve_output_device(selection)
        backend.check_output_settings(
            device=device_index,
            samplerate=asset.sample_rate,
            channels=asset.channels,
            dtype="float32",
        )

    def start_noise_loop(self, selection: str | int, asset: AudioAsset) -> NoiseLoopHandle:
        """启动循环噪声。"""
        device_index, _device = resolve_output_device(selection)
        return _SoundDeviceNoiseLoopHandle(device_index=device_index, asset=asset)

    def play_once(self, selection: str | int, asset: AudioAsset) -> PlaybackHandle:
        """播放一次唤醒词。"""
        device_index, _device = resolve_output_device(selection)
        return _SoundDevicePlaybackHandle(device_index=device_index, asset=asset)


def create_audio_backend(dry_run: bool = False):
    """根据运行模式选择真实或伪造音频后端。"""
    return DryRunAudioBackend() if dry_run else SoundDeviceAudioBackend()
