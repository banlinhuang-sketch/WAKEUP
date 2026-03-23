"""YAML 配置加载、默认值回填与保存逻辑。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    AppConfig,
    AudioDeviceConfig,
    DutConfig,
    MatchRule,
    ScenarioConfig,
    TimingConfig,
)


DEFAULT_RULES = {
    "rtos": [MatchRule(type="keyword", pattern="WAKEUP_SUCCESS")],
    "qualcomm": [MatchRule(type="keyword", pattern="AudioHAL: Voice wake up triggered")],
}

QUALCOMM_LEGACY_SUCCESS_PATTERN = (
    "-----------------------------------------M33_WAKEUP_AR1 success!! ----------------------------------------------------------"
)
QUALCOMM_EARLY_WAKE_PATTERN = "DMIC wake up"


def default_config(platform: str = "rtos", base_dir: str | Path | None = None) -> AppConfig:
    """生成一份带默认值的基础配置。"""
    platform = platform.strip().lower() or "rtos"
    return AppConfig(
        platform=platform,
        dut=DutConfig(),
        audio_devices=AudioDeviceConfig(),
        match_rules=[MatchRule(**rule.to_dict()) for rule in DEFAULT_RULES[platform]],
        timing=TimingConfig(),
        scenarios=[
            ScenarioConfig(
                name="default_scene",
                noise_file="",
                wakeup_file="",
                trials=10,
                enabled=True,
            )
        ],
        base_dir=str(base_dir) if base_dir else "",
    )


def _parse_match_rule(item: Any) -> MatchRule:
    """把不同输入格式统一解析成 MatchRule。"""
    if isinstance(item, MatchRule):
        return item
    if isinstance(item, str):
        value = item.strip()
        if value.lower().startswith("regex:"):
            return MatchRule(type="regex", pattern=value[6:].strip())
        return MatchRule(type="keyword", pattern=value)
    if isinstance(item, dict):
        rule_type = str(item.get("type", "keyword")).strip().lower()
        pattern = str(item.get("pattern", "")).strip()
        return MatchRule(
            type=rule_type or "keyword",
            pattern=pattern,
            case_sensitive=bool(item.get("case_sensitive", False)),
            description=str(item.get("description", "")).strip(),
        )
    raise TypeError(f"Unsupported match rule: {item!r}")


def parse_match_rules(raw: Any, platform: str) -> list[MatchRule]:
    """兼容 list/dict/多行文本三种规则输入格式。"""
    if raw is None:
        return [MatchRule(**rule.to_dict()) for rule in DEFAULT_RULES[platform]]
    if isinstance(raw, dict):
        if "rules" in raw:
            raw = raw["rules"]
        elif platform in raw:
            raw = raw[platform]
        else:
            raw = [raw]
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        raise TypeError("match_rules must be a list, dict, or multi-line string.")
    rules = [_parse_match_rule(item) for item in raw]
    if not rules:
        return [MatchRule(**rule.to_dict()) for rule in DEFAULT_RULES[platform]]
    return rules


def _apply_compatibility_migrations(config: AppConfig) -> AppConfig:
    """Apply targeted compatibility migrations for known legacy configs."""
    if config.platform != "qualcomm":
        return config

    if config.timing.success_window_ms > 3000:
        return config

    rules = config.match_rules
    if len(rules) != 1:
        return config

    legacy_rule = rules[0]
    if legacy_rule.type != "keyword":
        return config
    if legacy_rule.pattern != QUALCOMM_LEGACY_SUCCESS_PATTERN:
        return config

    config.match_rules = [
        MatchRule(type="keyword", pattern=QUALCOMM_EARLY_WAKE_PATTERN),
        MatchRule(**legacy_rule.to_dict()),
    ]
    return config


def _parse_scenarios(raw: Any) -> list[ScenarioConfig]:
    """把场景列表原始字典转换成强类型对象。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("scenarios must be a list.")
    scenarios: list[ScenarioConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError(f"Unsupported scenario: {item!r}")
        scenarios.append(
            ScenarioConfig(
                name=str(item.get("name", "")).strip() or f"scenario_{len(scenarios) + 1}",
                noise_file=str(item.get("noise_file", "")).strip(),
                noise_gain_db=float(item.get("noise_gain_db", 0.0)),
                wakeup_file=str(item.get("wakeup_file", "")).strip(),
                wakeup_gain_db=float(item.get("wakeup_gain_db", 0.0)),
                trials=int(item.get("trials", 10)),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return scenarios


def config_from_dict(data: dict[str, Any], base_dir: str | Path | None = None) -> AppConfig:
    """从字典构建配置对象，并补齐默认值。"""
    if not isinstance(data, dict):
        raise TypeError("Config payload must be a dictionary.")
    platform = str(data.get("platform", "rtos")).strip().lower()
    base_dir_str = str(base_dir) if base_dir else ""
    base = default_config(platform, base_dir=base_dir_str)
    dut_data = data.get("dut", {}) or {}
    audio_data = data.get("audio_devices", {}) or {}
    timing_data = data.get("timing", {}) or {}
    config = AppConfig(
        platform=platform,
        dut=DutConfig(
            serial_port=str(dut_data.get("serial_port", base.dut.serial_port)).strip(),
            baudrate=int(dut_data.get("baudrate", base.dut.baudrate)),
            adb_serial=str(dut_data.get("adb_serial", base.dut.adb_serial)).strip(),
        ),
        audio_devices=AudioDeviceConfig(
            mouth_output=str(audio_data.get("mouth_output", base.audio_devices.mouth_output)).strip(),
            noise_output=str(audio_data.get("noise_output", base.audio_devices.noise_output)).strip(),
        ),
        match_rules=parse_match_rules(data.get("match_rules"), platform),
        timing=TimingConfig(
            pre_noise_roll_ms=int(timing_data.get("pre_noise_roll_ms", base.timing.pre_noise_roll_ms)),
            trial_interval_ms=int(timing_data.get("trial_interval_ms", base.timing.trial_interval_ms)),
            success_window_ms=int(timing_data.get("success_window_ms", base.timing.success_window_ms)),
        ),
        scenarios=_parse_scenarios(data.get("scenarios")) or base.scenarios,
        allow_same_device=bool(data.get("allow_same_device", False)),
        output_root=str(data.get("output_root", "")).strip(),
        base_dir=base_dir_str,
    )
    config = _apply_compatibility_migrations(config)
    config.validate()
    return config


def load_config(path: str | Path) -> AppConfig:
    """从 YAML 文件读取配置。"""
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return config_from_dict(payload, base_dir=config_path.parent)


def save_config(path: str | Path, config: AppConfig) -> None:
    """把配置对象保存到 YAML 文件。"""
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def apply_platform_override(config: AppConfig, platform: str | None) -> AppConfig:
    """在保留原有配置的前提下覆盖平台类型。"""
    if not platform:
        return config
    updated = config_from_dict(config.to_dict(), base_dir=config.base_dir)
    updated.platform = platform.strip().lower()
    if not config.match_rules:
        updated.match_rules = [MatchRule(**rule.to_dict()) for rule in DEFAULT_RULES[updated.platform]]
    updated.validate()
    return updated
