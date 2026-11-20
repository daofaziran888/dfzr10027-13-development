"""时序缺口业务规则：按业务日期校验指标时序数据，维护时序缺口数据集与补数。

口径约定：
- 一次校验只按调用方指定的范围确认缺日，不扫描范围外日期；
- 缺日不能被填成实测零：补数必须给出正值实测，预测侧的缺日只能标记为估计；
- 同一营业日的补数重传后依然只有一条被确认的真实观测（按 序列编号+业务日期 去重）；
- 每次校验都追加到时序缺口数据集（_trend_gap_checks），补数前后都能核对观测。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

OBSERVATION_TABLE = "_trend_observations"
GAP_CHECK_TABLE = "_trend_gap_checks"

DATE_FORMAT = "%Y-%m-%d"


def _parse_day(raw: Any, field: str) -> tuple[date | None, str | None]:
    text = str(raw or "").strip()
    if not text:
        return None, f"{field}不能为空"
    try:
        return datetime.strptime(text, DATE_FORMAT).date(), None
    except ValueError:
        return None, f"{field}「{text}」不是 YYYY-MM-DD 格式的业务日期"


def _to_float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def find_series(series_code: str) -> dict[str, Any] | None:
    for row in store.rows("trend_series"):
        if row.get("序列编号") == series_code:
            return row
    return None


def business_days(start: date, end: date) -> list[str]:
    """日粒度序列在指定范围内的全部业务日期（闭区间，逐日展开）。"""
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def confirmed_observations(series_code: str, start: date, end: date) -> dict[str, dict[str, Any]]:
    """读取范围内已确认观测，按业务日期索引；同一营业日只会有一条。"""
    found: dict[str, dict[str, Any]] = {}
    for row in store.rows(OBSERVATION_TABLE):
        if row.get("序列编号") != series_code or row.get("确认状态") != "已确认":
            continue
        day = str(row.get("业务日期", ""))
        if start.isoformat() <= day <= end.isoformat():
            found[day] = row
    return found


def detect_gaps(series_code: str, start: date, end: date) -> dict[str, Any]:
    """纯函数：只在指定范围内比对业务日期与已确认观测，给出缺日。"""
    days = business_days(start, end)
    observed = confirmed_observations(series_code, start, end)
    missing = [day for day in days if day not in observed]
    detail = []
    for day in days:
        row = observed.get(day)
        detail.append({
            "业务日期": day,
            "状态": "已确认" if row else "缺失",
            "来源": row.get("来源") if row else "—",
            "gmv": row.get("gmv") if row else None,
        })
    return {
        "序列编号": series_code,
        "校验范围起始": start.isoformat(),
        "校验范围截止": end.isoformat(),
        "应有天数": len(days),
        "实测天数": len(days) - len(missing),
        "缺日数": len(missing),
        "缺日列表": missing,
        "核对明细": detail,
    }


class TrendGapService:
    """时序缺口校验、时序缺口数据集查询与补数登记。"""

    def check_gaps(self, series_code: str, start_raw: Any, end_raw: Any) -> tuple[dict[str, Any] | None, str]:
        """按指定范围校验缺日，并把结果追加到时序缺口数据集。"""
        series = find_series(series_code)
        if series is None:
            return None, f"时序序列 {series_code} 不存在或已归档"
        if series.get("时间粒度") != "日":
            return None, f"时序序列 {series_code} 粒度为「{series.get('时间粒度')}」，缺口校验目前只支持日粒度"
        start, err = _parse_day(start_raw, "起始日期")
        if err:
            return None, err
        end, err = _parse_day(end_raw, "截止日期")
        if err:
            return None, err
        assert start is not None and end is not None
        if start > end:
            return None, "起始日期不能晚于截止日期"

        result = detect_gaps(series_code, start, end)
        checks = store.rows(GAP_CHECK_TABLE)
        record = {
            "id": max((int(row.get("id", 0)) for row in checks), default=0) + 1,
            "校验编号": f"GAP-{len(checks) + 1:04d}",
            "观测指标": series.get("观测指标"),
            "校验时间": datetime.now().isoformat(timespec="seconds"),
            **result,
        }
        checks.append(record)
        return record, "校验完成"

    def list_gap_checks(self, series_code: str | None = None) -> list[dict[str, Any]]:
        """查询时序缺口数据集：补数前后的校验记录都保留，可逐条核对。"""
        rows = store.rows(GAP_CHECK_TABLE)
        if series_code:
            rows = [row for row in rows if row.get("序列编号") == series_code]
        return [dict(row) for row in rows]

    def get_gap_check(self, check_id: int) -> dict[str, Any] | None:
        for row in store.rows(GAP_CHECK_TABLE):
            if int(row.get("id", 0)) == check_id:
                return dict(row)
        return None

    def list_observations(self, series_code: str, start_raw: Any, end_raw: Any) -> tuple[list[dict[str, Any]] | None, str]:
        """列出指定范围内的已确认观测，用于补数前后逐日核对。"""
        if find_series(series_code) is None:
            return None, f"时序序列 {series_code} 不存在或已归档"
        start, err = _parse_day(start_raw, "起始日期")
        if err:
            return None, err
        end, err = _parse_day(end_raw, "截止日期")
        if err:
            return None, err
        assert start is not None and end is not None
        observed = confirmed_observations(series_code, start, end)
        return [dict(observed[day]) for day in sorted(observed)], "查询完成"

    def backfill_observation(self, series_code: str, day_raw: Any, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """补数登记：同一营业日重传幂等，仍只保留一条被确认的真实观测。"""
        if find_series(series_code) is None:
            return None, f"时序序列 {series_code} 不存在或已归档"
        day, err = _parse_day(day_raw, "业务日期")
        if err:
            return None, err
        assert day is not None

        gmv = _to_float(values.get("gmv"))
        if gmv is None or gmv <= 0:
            return None, "缺日不能被填成实测零：补数必须给出大于 0 的实测 GMV"

        rows = store.rows(OBSERVATION_TABLE)
        existing = [
            row for row in rows
            if row.get("序列编号") == series_code and row.get("业务日期") == day.isoformat()
        ]
        if existing:
            # 重传：更新原记录而不是新增，确认观测数保持为 1
            entry = existing[0]
            entry["gmv"] = gmv
            for field in ("uv", "paid_uv", "total_uv"):
                number = _to_float(values.get(field))
                if number is not None:
                    entry[field] = int(number)
            entry["版本"] = int(entry.get("版本", 1)) + 1
            entry["确认状态"] = "已确认"
            entry["登记时间"] = datetime.now().isoformat(timespec="seconds")
            message = f"{day.isoformat()} 已存在确认观测，重传已合并，仍只有一条被确认的真实观测"
            retransmitted = True
        else:
            entry = {
                "序列编号": series_code,
                "业务日期": day.isoformat(),
                "gmv": gmv,
                "uv": int(_to_float(values.get("uv")) or 0),
                "paid_uv": int(_to_float(values.get("paid_uv")) or 0),
                "total_uv": int(_to_float(values.get("total_uv")) or 0),
                "来源": "补数",
                "确认状态": "已确认",
                "版本": 1,
                "登记时间": datetime.now().isoformat(timespec="seconds"),
            }
            rows.append(entry)
            message = f"{day.isoformat()} 补数已登记为确认观测"
            retransmitted = False

        confirmed_count = sum(
            1 for row in rows
            if row.get("序列编号") == series_code
            and row.get("业务日期") == day.isoformat()
            and row.get("确认状态") == "已确认"
        )
        result = {
            "观测": dict(entry),
            "确认观测数": confirmed_count,
            "重传合并": retransmitted,
        }
        return result, message
