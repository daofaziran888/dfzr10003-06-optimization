"""应用层输出接口：维护输出流水，覆盖启动输出、暂停输出、归档流水等动作。

输出前会核对上游汇总层的复核结论；待复核的度量变更会以待办形式同步到本模块。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.ads import AdsService
from app.services.factchange import fact_change_service

router = APIRouter(prefix="/api/ads", tags=["应用层输出"])

service = AdsService()

LIST_FIELDS = ["流水编号", "输入表名", "输出表名", "输出格式", "推送目标", "推送周期", "缓存时间", "执行批次"]
STATUSES = ["草稿", "运行中", "已暂停", "已完成"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按流水编号检索"),
    status: str | None = Query(default=None, description="草稿、运行中、已暂停、已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按流水编号与状态过滤应用层输出列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条输出流水明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"输出流水 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条输出流水，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="输出流水已登记", entry=entry)


@router.get("/{entry_id}/todo", response_model=dict)
def todo(entry_id: int) -> dict[str, Any]:
    """应用层输出的待办：未完成复核的度量变更不能当作已完成结果。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"输出流水 {entry_id} 不存在或已归档")
    overlap = fact_change_service.downstream_state(entry)
    if overlap:
        entry.update(overlap)
    return {
        "module": "ads",
        "entry": entry,
        "处理状态": entry.get("处理状态", ""),
        "复核状态": entry.get("复核状态", ""),
        "复核结论": entry.get("复核结论", ""),
        "待办": entry.get("待办", ""),
        "待复核变更": entry.get("待复核变更", ""),
        "可输出": not bool(overlap) and entry.get("status") == "已完成",
    }


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条输出流水执行启动输出、暂停输出、归档流水；待复核期间不允许按已完成结果输出。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出应用层输出清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "ads", "total": total, "items": items}
