"""时序分析接口：维护时序序列，覆盖初始化序列、标记完整、标记缺口等动作。

缺口能力：按业务日期校验指标时序数据（时序缺口数据集）、补数登记与观测核对。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.trend_gap import TrendGapService
from app.services.trend_series import TrendSeriesService

router = APIRouter(prefix="/api/trend_series", tags=["时序分析"])

service = TrendSeriesService()
gap_service = TrendGapService()

LIST_FIELDS = ["序列编号", "观测指标", "时间粒度", "起始时间", "截止时间", "填充策略", "异常检测", "序列版本"]
STATUSES = ["待初始化", "采集中", "完整", "有缺口"]


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
    keyword: str | None = Query(default=None, description="按序列编号检索"),
    status: str | None = Query(default=None, description="待初始化、采集中、完整、有缺口"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、序列编号关键字与状态过滤时序分析列表。"""
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
        headers={"Content-Disposition": 'attachment; filename="trend_series.csv"'})


@router.post("/gaps/check", response_model=ActionResult)
def check_gaps(payload: dict[str, object]) -> ActionResult:
    """按指定业务日期范围校验缺日；一次查询只确认该范围内的缺日，结果写入时序缺口数据集。"""
    record, message = gap_service.check_gaps(
        str(payload.get("序列编号", "") or ""),
        payload.get("起始日期"),
        payload.get("截止日期"),
    )
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/gaps", response_model=dict)
def list_gap_checks(
    序列编号: str | None = Query(default=None, description="按序列编号过滤时序缺口数据集"),
) -> dict:
    """查询时序缺口数据集：补数前后的校验记录都保留，可逐条核对观测。"""
    items = gap_service.list_gap_checks(序列编号)
    return {"items": items, "total": len(items)}


@router.get("/gaps/{check_id}", response_model=dict)
def get_gap_check(check_id: int) -> dict:
    """读取单条缺口校验记录，用于补数前后对比。"""
    record = gap_service.get_gap_check(check_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"缺口校验记录 {check_id} 不存在")
    return record


@router.post("/gaps/backfill", response_model=ActionResult)
def backfill_observation(payload: dict[str, object]) -> ActionResult:
    """补数登记：缺日不能被填成实测零；同一营业日重传后仍只有一条被确认的真实观测。"""
    result, message = gap_service.backfill_observation(
        str(payload.get("序列编号", "") or ""),
        payload.get("业务日期"),
        payload,
    )
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/observations", response_model=dict)
def list_observations(
    序列编号: str = Query(..., description="序列编号，如 TREN-GMV"),
    起始日期: str = Query(..., description="业务日期起，YYYY-MM-DD"),
    截止日期: str = Query(..., description="业务日期止，YYYY-MM-DD"),
) -> dict:
    """列出指定范围内的已确认观测，补数前后都能逐日核对。"""
    items, message = gap_service.list_observations(序列编号, 起始日期, 截止日期)
    if items is None:
        raise HTTPException(status_code=404, detail=message)
    return {"items": items, "total": len(items)}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条时序序列明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"时序序列 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条时序序列，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="时序序列已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条时序序列执行初始化序列、标记完整、标记缺口；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
