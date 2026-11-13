"""汇总层聚合接口：维护聚合流水，覆盖启动聚合、暂停聚合、归档流水等动作。

事实定义口径变更后，本页承担受影响聚合的重跑与核对；只有这里核对通过，
新版本才允许在应用层输出。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dws import DwsService

router = APIRouter(prefix="/api/dws", tags=["汇总层聚合"])

service = DwsService()

LIST_FIELDS = ["流水编号", "输入表名", "输出表名", "聚合字段", "汇总粒度", "时间窗口", "预计算开关", "执行批次"]
STATUSES = ["草稿", "运行中", "已暂停", "已完成"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按流水编号检索"),
    status: str | None = Query(default=None, description="草稿、运行中、已暂停、已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按流水编号与状态过滤汇总层聚合列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条聚合流水明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"聚合流水 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条聚合流水，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="聚合流水已登记", entry=entry)


@router.get("/{entry_id}/review", response_model=dict)
def review_detail(entry_id: int) -> dict[str, Any]:
    """汇总页读取该聚合流水的影响窗口、旧/新口径与复核结论，与事实页共用同一份视图。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"聚合流水 {entry_id} 不存在或已归档")
    return service.review_view(entry_id)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条聚合流水执行启动聚合、暂停聚合、归档流水，以及变更后的重跑与核对。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(
        entry_id,
        action,
        conclusion=payload.values.get("复核结论"),
        values=payload.values,
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出汇总层聚合清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dws", "total": total, "items": items}
