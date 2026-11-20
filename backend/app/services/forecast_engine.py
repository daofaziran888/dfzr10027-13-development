"""预测分析引擎：缺口感知的训练样本构建与真实预测计算。

训练样本不再直接读 fixtures，而是基于已确认观测与缺口校验结果：
- 预测模块按缺口结果确定训练样本日期，逐日标记实测与估计的来源；
- 缺日只能估计（线性插值或邻近实测），缺日不能被填成实测零；
- 拟合窗口中段漏采而末段已有足够连续观测时，保留早期周期信息与减少
  估计误差存在冲突，窗口决策随结果记录，供随后报表核对预测数字；
- 每次训练都追加不可变快照到 _forecast_runs，历史预测的训练明细保留
  当时的数据，两次训练的输入可以分别核对。
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any

from app.services.trend_gap import business_days, confirmed_observations, find_series
from app.store import store

RUN_TABLE = "_forecast_runs"
DATE_FORMAT = "%Y-%m-%d"

# 末段连续实测达到该天数即视为“已有足够的连续观测”，可独立支撑训练
MIN_TRAILING_ACTUAL_DAYS = 7

STRATEGIES = ("估计保留", "截断末段")


def _to_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _to_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _parse_horizon(raw: Any, default: int = 7) -> int:
    text = str(raw or "").replace("天", "").strip()
    try:
        value = int(text)
        return value if value > 0 else default
    except ValueError:
        return default


def resolve_series_for_metric(metric_code: str) -> dict[str, Any] | None:
    """按观测指标找到挂接的时序序列。"""
    for row in store.rows("trend_series"):
        if row.get("观测指标") == metric_code:
            return row
    return None


def _metric_value(metric_code: str, observation: dict[str, Any]) -> float:
    """按指标口径从观测行取值。"""
    code = metric_code.lower()
    if "cvr" in code:
        total = _to_float(observation.get("total_uv"))
        return _to_float(observation.get("paid_uv")) / total if total else 0.0
    if "uv" in code and "gmv" not in code:
        return _to_float(observation.get("uv"))
    return _to_float(observation.get("gmv"))


def _trailing_actual_days(days: list[str], observed: dict[str, dict[str, Any]]) -> int:
    """窗口末段连续已确认观测的天数。"""
    count = 0
    for day in reversed(days):
        if day in observed:
            count += 1
        else:
            break
    return count


def _estimate_values(
    days: list[str],
    actual_values: dict[str, float],
) -> dict[str, tuple[float, str]]:
    """为缺日估计取值：优先线性插值，边缘缺日用邻近实测；绝不返回零。"""
    estimates: dict[str, tuple[float, str]] = {}
    known_indexes = [i for i, day in enumerate(days) if day in actual_values]
    if not known_indexes:
        return estimates
    for i, day in enumerate(days):
        if day in actual_values:
            continue
        prev_idx = max((k for k in known_indexes if k < i), default=None)
        next_idx = min((k for k in known_indexes if k > i), default=None)
        if prev_idx is not None and next_idx is not None:
            prev_val = actual_values[days[prev_idx]]
            next_val = actual_values[days[next_idx]]
            ratio = (i - prev_idx) / (next_idx - prev_idx)
            estimated = prev_val + (next_val - prev_val) * ratio
            estimates[day] = (round(estimated, 2), "线性插值")
        elif prev_idx is not None:
            estimates[day] = (round(actual_values[days[prev_idx]], 2), "邻近实测(向前)")
        else:
            estimates[day] = (round(actual_values[days[next_idx]], 2), "邻近实测(向后)")
    return estimates


def build_training_samples(
    series_code: str,
    metric_code: str,
    start: date,
    end: date,
    strategy: str = "估计保留",
) -> tuple[dict[str, Any] | None, str]:
    """按缺口结果确定训练样本日期，并逐日标记实测与估计的来源。"""
    days = business_days(start, end)
    observed = confirmed_observations(series_code, start, end)
    actual_values = {day: _metric_value(metric_code, row) for day, row in observed.items()}
    if not actual_values:
        return None, f"序列 {series_code} 在 {start.isoformat()} 至 {end.isoformat()} 内没有已确认观测，无法训练"

    missing = [day for day in days if day not in observed]
    trailing = _trailing_actual_days(days, observed)

    if not missing:
        decision = "窗口内观测完整，训练样本全部为实测"
        used_days = days
        estimates: dict[str, tuple[float, str]] = {}
    elif strategy == "截断末段" and trailing >= MIN_TRAILING_ACTUAL_DAYS:
        dropped = [day for day in days if day < days[len(days) - trailing]]
        used_days = days[len(days) - trailing:]
        estimates = {}
        decision = (
            f"窗口中段缺日 {len(missing)} 天（{'、'.join(missing)}），末段连续实测 {trailing} 天已足够独立支撑训练；"
            f"按策略「截断末段」优先减少估计误差，未采用早期日期 {'、'.join(dropped) or '无'}"
        )
    else:
        used_days = days
        estimates = _estimate_values(days, actual_values)
        fallback = ""
        if strategy == "截断末段":
            fallback = f"末段连续实测仅 {trailing} 天（不足 {MIN_TRAILING_ACTUAL_DAYS} 天），回退为「估计保留」；"
        conflict = ""
        if trailing >= MIN_TRAILING_ACTUAL_DAYS:
            conflict = (
                f"末段连续实测 {trailing} 天已可独立支撑训练，保留早期周期信息与减少估计误差存在冲突，"
                "当前按策略保留全窗口，估计样本已单独标记；"
            )
        decision = (
            f"窗口中段缺日 {len(missing)} 天（{'、'.join(missing)}）。{fallback}{conflict}"
            "缺日按邻近实测估计且绝不填实测零，估计值不参与实测统计"
        )

    samples: list[dict[str, Any]] = []
    for day in used_days:
        if day in actual_values:
            samples.append({
                "业务日期": day,
                "值": round(actual_values[day], 4),
                "来源": "实测",
                "观测来源": observed[day].get("来源", "采集"),
            })
        else:
            value, method = estimates[day]
            samples.append({
                "业务日期": day,
                "值": value,
                "来源": "估计",
                "估计方法": method,
            })

    return {
        "训练窗口": {"起始": start.isoformat(), "截止": end.isoformat()},
        "缺日列表": missing,
        "实测天数": sum(1 for s in samples if s["来源"] == "实测"),
        "估计天数": sum(1 for s in samples if s["来源"] == "估计"),
        "末段连续实测天数": trailing,
        "窗口决策": decision,
        "训练样本": samples,
    }, "样本就绪"


def linear_forecast(values: list[float], horizon: int = 7) -> dict[str, Any]:
    """线性趋势外推。"""
    n = len(values)
    if n < 2:
        return {"error": "数据不足"}

    xs = list(range(n))
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(values)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    slope = num / den if den else 0.0
    intercept = y_mean - slope * x_mean

    predictions = []
    for i in range(1, horizon + 1):
        pred = slope * (n - 1 + i) + intercept
        predictions.append(round(pred, 2))

    return {
        "model": "linear_trend",
        "slope": round(slope, 4),
        "intercept": round(intercept, 2),
        "predictions": predictions,
        "r2": round(1 - sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, values)) / sum((y - y_mean) ** 2 for y in values), 4) if sum((y - y_mean) ** 2 for y in values) else 1.0,
    }


def moving_average_forecast(values: list[float], window: int = 7, horizon: int = 14) -> dict[str, Any]:
    """移动平均预测。"""
    n = len(values)
    if n < window:
        return {"error": "数据不足"}

    last_window = values[-window:]
    avg = statistics.mean(last_window)
    predictions = [round(avg, 2)] * horizon

    # 计算 MAPE
    mape = sum(abs(values[i] - statistics.mean(values[max(0, i - window):i + 1])) / values[i] for i in range(window, n) if values[i]) / max(n - window, 1)

    return {
        "model": "moving_average",
        "window": window,
        "predictions": predictions,
        "mape": round(mape, 4),
    }


def exponential_smoothing_forecast(values: list[float], alpha: float = 0.3, horizon: int = 7) -> dict[str, Any]:
    """指数平滑预测。"""
    n = len(values)
    if n < 2:
        return {"error": "数据不足"}

    smoothed = [values[0]]
    for v in values[1:]:
        smoothed.append(alpha * v + (1 - alpha) * smoothed[-1])

    last_smoothed = smoothed[-1]
    predictions = [round(last_smoothed, 2)] * horizon

    # 简单趋势调整
    trend = last_smoothed - smoothed[-2] if len(smoothed) >= 2 else 0.0
    for i in range(horizon):
        predictions[i] = round(last_smoothed + trend * (i + 1), 2)

    # 计算 MSE
    mse = sum((values[i] - smoothed[i]) ** 2 for i in range(n)) / n

    return {
        "model": "exponential_smoothing",
        "alpha": alpha,
        "predictions": predictions,
        "mse": round(mse, 2),
    }


def _run_algorithm(algorithm: str, values: list[float], horizon: int) -> dict[str, Any]:
    if "移动" in algorithm:
        return moving_average_forecast(values, min(7, len(values)), horizon)
    if "平滑" in algorithm or "指数" in algorithm:
        return exponential_smoothing_forecast(values, 0.3, horizon)
    return linear_forecast(values, horizon)


def run_gap_aware_forecast(
    *,
    metric_code: str,
    algorithm: str | None = None,
    horizon: int | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    strategy: str = "估计保留",
    scheme: dict[str, Any] | None = None,
    trigger: str = "手工执行",
) -> tuple[dict[str, Any] | None, str]:
    """缺口感知预测：按缺口结果确定训练样本，记录快照并返回运行记录。"""
    series = resolve_series_for_metric(metric_code)
    if series is None:
        return None, f"指标 {metric_code} 未挂接时序序列，无法确定训练样本日期"
    series_code = str(series.get("序列编号"))

    if scheme is not None:
        algorithm = algorithm or str(scheme.get("预测算法") or "线性趋势")
        if horizon is None:
            horizon = _parse_horizon(scheme.get("预测周期"), 7)
    if algorithm is None:
        algorithm = "线性趋势"
    if horizon is None:
        horizon = 7
    if strategy not in STRATEGIES:
        return None, f"缺口策略「{strategy}」不支持，可选：{'、'.join(STRATEGIES)}"

    start = window_start or datetime.strptime(str(series.get("起始时间")), DATE_FORMAT).date()
    end = window_end or datetime.strptime(str(series.get("截止时间")), DATE_FORMAT).date()
    if start > end:
        return None, "训练窗口起始不能晚于截止"

    sample_info, message = build_training_samples(series_code, metric_code, start, end, strategy)
    if sample_info is None:
        return None, message

    values = [float(sample["值"]) for sample in sample_info["训练样本"]]
    algo_result = _run_algorithm(algorithm, values, horizon)
    if "error" in algo_result:
        return None, f"训练样本不足：{algo_result['error']}"

    last_day = datetime.strptime(sample_info["训练窗口"]["截止"], DATE_FORMAT).date()
    algo_result["forecast_dates"] = [(last_day + timedelta(days=i)).isoformat() for i in range(1, horizon + 1)]
    actual_values = [float(s["值"]) for s in sample_info["训练样本"] if s["来源"] == "实测"]
    algo_result["last_actual"] = round(actual_values[-1], 2) if actual_values else 0.0

    runs = store.rows(RUN_TABLE)
    record = {
        "run_id": f"FRUN-{len(runs) + 1:04d}",
        "方案编号": scheme.get("方案编号") if scheme else None,
        "预测指标": metric_code,
        "序列编号": series_code,
        "预测算法": algorithm,
        "触发来源": trigger,
        "缺口策略": strategy,
        "horizon": horizon,
        **sample_info,
        "预测结果": algo_result,
        "生成时间": datetime.now().isoformat(timespec="seconds"),
    }
    runs.append(record)
    return record, "预测完成"


def list_forecast_runs(scheme_code: str | None = None, metric_code: str | None = None) -> list[dict[str, Any]]:
    """列出预测训练快照；历史训练明细保留当时的数据，不会被后续补数改写。"""
    rows = store.rows(RUN_TABLE)
    if scheme_code:
        rows = [row for row in rows if row.get("方案编号") == scheme_code]
    if metric_code:
        rows = [row for row in rows if row.get("预测指标") == metric_code]
    return [dict(row) for row in rows]


def get_forecast_run(run_id: str) -> dict[str, Any] | None:
    for row in store.rows(RUN_TABLE):
        if row.get("run_id") == run_id:
            return dict(row)
    return None
