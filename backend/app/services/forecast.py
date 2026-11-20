"""预测分析业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import forecast_engine
from app.store import store

MODULE = "forecast"
LIST_FIELDS = ["方案编号", "预测指标", "预测算法", "训练窗口", "预测周期", "置信水平", "特征列表", "模型版本"]
REQUIRED_FIELDS = ["方案编号", "预测指标", "预测算法"]
STATUS_ORDER = ["草稿", "训练中", "已发布", "已废弃"]
ACTION_RULES = {"启动训练": "训练中", "发布模型": "已发布", "废弃方案": "已废弃"}
NEGATIVE_ACTIONS = []


class ForecastService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("方案编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"预测方案 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于预测分析可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"预测方案已{action}"

    def run_gap_aware(
        self,
        entry_id: int,
        *,
        window_start: str | None = None,
        window_end: str | None = None,
        strategy: str = "估计保留",
    ) -> tuple[dict[str, Any] | None, str]:
        """按缺口结果执行预测：训练样本日期、实测与估计来源、所用范围都随快照保留。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"预测方案 {entry_id} 不存在或已归档"

        def _parse(raw: str | None, field: str):
            if not raw:
                return None, None
            try:
                return datetime.strptime(raw.strip(), "%Y-%m-%d").date(), None
            except ValueError:
                return None, f"{field}「{raw}」不是 YYYY-MM-DD 格式的业务日期"

        start, err = _parse(window_start, "窗口起始")
        if err:
            return None, err
        end, err = _parse(window_end, "窗口截止")
        if err:
            return None, err

        record, message = forecast_engine.run_gap_aware_forecast(
            metric_code=str(entry.get("预测指标", "")),
            window_start=start,
            window_end=end,
            strategy=strategy,
            scheme=entry,
            trigger=f"预测方案 {entry.get('方案编号')} 手工执行",
        )
        if record is None:
            return None, message
        entry["最近运行"] = record["run_id"]
        return record, message
