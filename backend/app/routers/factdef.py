"""事实表管理接口：维护事实定义，覆盖提交审批、发布事实、废弃事实等动作。

口径变更（粒度说明、关联维度、度量字段、分区字段）不在本页直接生效：
接口只负责登记变更并触发失效检测，受影响聚合复核完成后新版本才由服务层放行。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.factchange import fact_change_service
from app.services.factdef import FactdefService

router = APIRouter(prefix="/api/factdef", tags=["事实表管理"])

service = FactdefService()

LIST_FIELDS = ["事实编号", "事实名称", "源表名称", "粒度说明", "关联维度", "度量字段", "分区字段", "最近刷新"]
STATUSES = ["草稿", "待审批", "已生效", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按事实编号检索"),
    status: str | None = Query(default=None, description="草稿、待审批、已生效、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按事实编号与状态过滤事实表管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条事实定义明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"事实定义 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条事实定义，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="事实定义已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条事实定义执行提交审批、发布事实、废弃事实；不允许的动作会被拦下并说明原因。

    携带口径字段时视为一次定义变更：先走失效检测登记待复核，不直接改生效口径。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message, blocked = service.run_action(
        entry_id,
        action,
        payload.values,
        effective_time=payload.values.get("生效时间"),
        version=payload.values.get("版本号"),
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=not blocked, message=message, entry=entry)


@router.get("/{entry_id}/changes", response_model=dict)
def list_changes(entry_id: int) -> dict[str, Any]:
    """事实页读取该事实的变更待办与影响视图；与汇总页共用同一份影响原因和口径。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"事实定义 {entry_id} 不存在或已归档")
    tickets = fact_change_service.list_tickets(factdef_id=entry_id)
    pending = fact_change_service.pending_for_fact(entry_id)
    return {
        "module": "factdef",
        "entry": entry,
        "tickets": tickets,
        "pending": pending,
        "version": {
            "版本号": entry.get("版本号"),
            "生效时间": entry.get("生效时间"),
            "版本状态": entry.get("版本状态"),
            "处理状态": entry.get("处理状态"),
            "待生效版本号": entry.get("待生效版本号"),
        },
    }


@router.post("/{entry_id}/changes/detect", response_model=ActionResult)
def detect_change(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按提交的口径字段做失效检测；同一变更重复检测命中同一条待办。"""
    result = fact_change_service.detect_change(
        entry_id,
        payload.values,
        effective_time=payload.values.get("生效时间"),
        version=payload.values.get("版本号"),
    )
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=result.get("ticket") or result.get("entry"),
    )


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出事实表管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "factdef", "total": total, "items": items}
