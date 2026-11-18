"""爬虫引擎 时间线接口：返回作业时间序列结构。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.crawler import CrawlerService

router = APIRouter(prefix="/api/crawler", tags=["爬虫引擎"])
service = CrawlerService()
LIST_FIELDS = ["任务编号", "任务名称", "关联站点", "关联规则", "并发线程", "调度周期", "超时秒数", "下发批次"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("")
def list_entries() -> dict[str, Any]:
    items = service.get_timeline()
    return {"items": items}


@router.get("/allocations", response_model=PageResult[dict])
def list_allocations(
    status: str | None = Query(default=None, description="等待中、已恢复、已失败"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=500),
) -> PageResult[dict]:
    """代理分配记录台账：等待任务与已恢复任务状态分离，校验结论同步自代理降级决策数据集。"""
    items, total = service.list_allocations(status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/allocations/{allocation_id}/actions", response_model=ActionResult)
def run_allocation_action(allocation_id: int, payload: ActionPayload) -> ActionResult:
    """标记失败 / 请求重放：已失败的请求不能无提示地重放，必须携带重放说明。"""
    entry, message = service.allocation_action(allocation_id, payload.action.strip(), payload.remark)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.get_timeline()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=crawler.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"爬虫任务 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="爬虫任务已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
