"""统计汇总与报告文件输出。"""

from __future__ import annotations

import csv
from pathlib import Path
import statistics
from typing import Any

import yaml

from .models import (
    AppConfig,
    LogEvent,
    TRIAL_STATUS_PASS,
    TRIAL_STATUS_SKIPPED,
    TrialResult,
    local_now_iso,
)


def _percentile(values: list[float], percentile: float) -> float | None:
    """计算线性插值分位数，用于时延 P95。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _scenario_summary(trials: list[TrialResult]) -> dict[str, Any]:
    """按场景或全局维度汇总成功率与时延指标。"""
    attempted = [trial for trial in trials if trial.status != TRIAL_STATUS_SKIPPED]
    passed = [trial for trial in attempted if trial.status == TRIAL_STATUS_PASS]
    latencies = [trial.latency_ms for trial in passed if trial.latency_ms is not None]
    return {
        "total_trials": len(trials),
        "attempted_trials": len(attempted),
        "passed_trials": len(passed),
        "failed_trials": len([trial for trial in attempted if trial.status not in {TRIAL_STATUS_PASS}]),
        "success_rate": round((len(passed) / len(attempted) * 100.0), 3) if attempted else 0.0,
        "latency_ms": {
            "avg": round(statistics.fmean(latencies), 3) if latencies else None,
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 3) if latencies else None,
        },
    }


def build_summary(config: AppConfig, trials: list[TrialResult], output_dir: str | Path) -> dict[str, Any]:
    """构建 summary.json 对应的内存对象。"""
    scenarios: dict[str, list[TrialResult]] = {}
    for trial in trials:
        scenarios.setdefault(trial.scenario_name, []).append(trial)
    overall = _scenario_summary(trials)
    return {
        "generated_at": local_now_iso(),
        "platform": config.normalized_platform(),
        "output_dir": str(Path(output_dir)),
        "overall": overall,
        "timing": config.timing.to_dict(),
        "scenarios": {name: _scenario_summary(items) for name, items in scenarios.items()},
    }


def write_reports(
    output_dir: str | Path,
    config: AppConfig,
    trials: list[TrialResult],
    events: list[LogEvent],
) -> dict[str, Any]:
    """把本轮测试结果写成 JSON、CSV 和配置快照。"""
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(config, trials, run_dir)

    summary_path = run_dir / "summary.json"
    trial_csv_path = run_dir / "trial_results.csv"
    event_csv_path = run_dir / "event_log.csv"
    snapshot_path = run_dir / "run_config_snapshot.yaml"

    import json

    # JSON 汇总更适合机器读取，CSV 更适合测试同学直接打开分析。
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    with trial_csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "platform",
                "scenario_name",
                "trial_index",
                "trial_label",
                "wakeup_started_monotonic",
                "wakeup_started_iso",
                "status",
                "matched",
                "latency_ms",
                "matched_line",
                "failure_reason",
            ],
        )
        writer.writeheader()
        for trial in trials:
            writer.writerow(trial.to_dict())

    with event_csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "timestamp_monotonic",
                "timestamp_iso",
                "source",
                "matched",
                "trial_label",
                "matched_window",
                "raw_line",
            ],
        )
        writer.writeheader()
        for event in events:
            writer.writerow(event.to_dict())

    snapshot_path.write_text(
        yaml.safe_dump(config.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return summary
