"""预测分析接口：维护预测方案，覆盖启动训练、发布模型、废弃方案等动作。

缺口感知预测：按缺口结果确定训练样本日期，训练快照可独立核对。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import forecast_engine
from app.services.forecast import ForecastService

router = APIRouter(prefix="/api/forecast", tags=["预测分析"])

service = ForecastService()

LIST_FIELDS = ["方案编号", "预测指标", "预测算法", "训练窗口", "预测周期", "置信水平", "特征列表", "模型版本"]
STATUSES = ["草稿", "训练中", "已发布", "已废弃"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、训练中、已发布、已废弃"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、方案编号关键字与状态过滤预测分析列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="forecast.csv"'})


@router.get("/runs", response_model=dict)
def list_runs(
    方案编号: str | None = Query(default=None, description="按方案编号过滤训练快照"),
    预测指标: str | None = Query(default=None, description="按预测指标过滤训练快照"),
) -> dict:
    """列出预测训练快照：历史预测的训练明细保留当时的数据，两次训练输入可分别核对。"""
    items = forecast_engine.list_forecast_runs(scheme_code=方案编号, metric_code=预测指标)
    return {"items": items, "total": len(items)}


@router.get("/runs/{run_id}", response_model=dict)
def get_run(run_id: str) -> dict:
    """读取单次训练快照，含训练样本、实测与估计来源与所用训练窗口。"""
    record = forecast_engine.get_forecast_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"训练快照 {run_id} 不存在")
    return record


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条预测方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"预测方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条预测方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="预测方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条预测方案执行启动训练、发布模型、废弃方案；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/run", response_model=ActionResult)
def run_forecast(entry_id: int, payload: dict[str, object]) -> ActionResult:
    """缺口感知执行预测：按缺口结果确定训练样本日期，快照记录实测与估计来源及所用范围。"""
    record, message = service.run_gap_aware(
        entry_id,
        window_start=str(payload.get("窗口起始") or "") or None,
        window_end=str(payload.get("窗口截止") or "") or None,
        strategy=str(payload.get("缺口策略") or "估计保留"),
    )
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)
