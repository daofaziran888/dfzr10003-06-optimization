"""事实定义变更失效检测与复核流转。

取数链路的版本引用路径：事实定义（factdef）通过「事实编号 / 源表名称」引用到
汇总层聚合流水（dws），聚合结果完成并核对后才允许事实定义的新版本对下游生效，
应用层输出（ads）再按汇总层的复核结论同步待办。

所有写操作都在 store.lock 内串行化：同一度量变更并发检测时只允许一条待办落库，
历史聚合按当时口径原样保留，重跑结果以新的聚合流水追加，不覆盖旧记录。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "factchange"
FACT_MODULE = "factdef"
DWS_MODULE = "dws"
ADS_MODULE = "ads"

# 只有这四类定义变化会影响聚合口径，需要触发失效检测。
TRACKED_FIELDS = ["粒度说明", "关联维度", "度量字段", "分区字段"]
PENDING = "待复核"
REVIEWED = "已复核"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_time(value: Any) -> str:
    """把日期或时间统一成可比较的字符串，保证版本优先级裁决稳定。"""
    text = _text(value)
    if not text:
        return ""
    if len(text) == 10:
        return f"{text} 00:00:00"
    return text


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _snapshot(row: dict[str, Any]) -> dict[str, str]:
    return {field: _text(row.get(field)) for field in TRACKED_FIELDS}


def _diff(old: dict[str, str], new: dict[str, str]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for field in TRACKED_FIELDS:
        if old.get(field, "") == new.get(field, ""):
            continue
        changes.append({"字段": field, "旧值": old.get(field, ""), "新值": new.get(field, "")})
    return changes


def _reason(changes: list[dict[str, str]]) -> str:
    if not changes:
        return "口径未变化"
    return "；".join(
        f"{item['字段']}变更：{item['旧值'] or '（空）'} → {item['新值'] or '（空）'}"
        for item in changes
    )


def _windows(entries: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for entry in entries:
        window = _text(entry.get("时间窗口"))
        if window and window not in seen:
            seen.append(window)
    return seen


class FactChangeService:
    def _next_id(self) -> int:
        return max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1

    def _pending_ticket(self, fact_id: int) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if int(row.get("事实定义ID", 0)) == fact_id and row.get("处理状态") == PENDING:
                return row
        return None

    def pending_for_fact(self, fact_id: int) -> dict[str, Any] | None:
        """事实页据此判断是否还有未复核的变更，避免单独宣布新版本已生效。"""
        with store.lock:
            ticket = self._pending_ticket(int(fact_id))
            return self.view(ticket) if ticket is not None else None

    def _fingerprint(self, fact: dict[str, Any], proposed: dict[str, str]) -> str:
        """指纹只取事实编号与新口径：同一变更重复提交、重试都命中同一条待办。"""
        body = "|".join(f"{field}={proposed.get(field, '')}" for field in TRACKED_FIELDS)
        return f"{_text(fact.get('事实编号'))}|{body}"

    def _next_version(self, fact: dict[str, Any], pending: dict[str, Any] | None) -> int:
        current = int(fact.get("版本号") or 0)
        if pending is None:
            return current + 1
        return max(current, int(pending.get("新版本号") or current)) + 1

    def _affected_aggregations(self, fact: dict[str, Any]) -> list[dict[str, Any]]:
        """沿事实定义到聚合流水的引用路径定位受影响的聚合流水。

        只取当前口径版本下的聚合：已经按新版本重跑留存的旧聚合不再重复纳入，
        否则每次变更都会把历史口径再次拖入待办。
        """
        fact_no = _text(fact.get("事实编号"))
        source = _text(fact.get("源表名称"))
        results: list[dict[str, Any]] = []
        for row in store.rows(DWS_MODULE):
            same_fact = bool(fact_no) and _text(row.get("事实编号")) == fact_no
            same_source = bool(source) and _text(row.get("输入表名")) == source
            if not (same_fact or same_source):
                continue
            if _text(row.get("复核状态")) == "历史口径已留存":
                continue
            if row.get("历史口径") is True:
                continue
            results.append(row)
        return results

    def _entry(self, row: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
        return {
            "聚合ID": int(row.get("id", 0)),
            "流水编号": _text(row.get("流水编号")),
            "输入表名": _text(row.get("输入表名")),
            "输出表名": _text(row.get("输出表名")),
            "时间窗口": _text(row.get("时间窗口")),
            "原口径版本": int(row.get("事实版本") or fact.get("版本号") or 0),
            "复核状态": PENDING,
            "复核结论": "",
            "新聚合流水": "",
            "重跑版本": 0,
        }

    def _merge_entries(
        self,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged = {int(entry["聚合ID"]): dict(entry) for entry in existing}
        for entry in incoming:
            merged.setdefault(int(entry["聚合ID"]), dict(entry))
        return list(merged.values())

    def _linked_ads(self, ticket: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        links: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for ad in store.rows(ADS_MODULE):
            for entry in ticket.get("受影响聚合", []):
                if _text(ad.get("输入表名")) == _text(entry.get("输出表名")):
                    links.append((ad, entry))
                    break
        return links

    def _sync_downstream(self, ticket: dict[str, Any], fact: dict[str, Any]) -> None:
        """待复核状态同步到汇总层聚合流水与应用层输出待办。"""
        current_version = _int(fact.get("版本号"))
        new_version = _int(ticket.get("新版本号")) or current_version
        if ticket.get("处理状态") != PENDING:
            return
        fact["版本状态"] = f"v{current_version} 已生效 · v{new_version} 待生效（复核中）"
        fact["处理状态"] = PENDING
        fact["pending"] = True
        fact["待生效版本号"] = new_version
        fact["待生效口径"] = dict(ticket.get("新口径") or {})
        fact["待生效生效时间"] = ticket.get("生效时间", "")
        fact["待复核变更"] = ticket.get("变更编号", "")

        for entry in ticket.get("受影响聚合", []):
            row = store.find(DWS_MODULE, int(entry.get("聚合ID", 0)))
            if row is None:
                continue
            # 已经核对通过的聚合不能被重复检测倒回待复核，否则完成进度会被清零。
            if entry.get("复核状态") == "已核对" and row.get("复核状态") == "已核对":
                continue
            # 已经产出新口径结果在等待核对时，不能把重跑中的状态倒回「待复核」。
            if entry.get("新聚合流水") and row.get("复核状态") in ("待核对", "重跑中"):
                continue
            row["复核状态"] = PENDING
            row["复核结论"] = ""
            row["影响原因"] = ticket.get("影响原因", "")
            row["影响窗口"] = entry.get("时间窗口", "")
            row["待复核版本"] = new_version
            row["待复核变更"] = ticket.get("变更编号", "")
            row["pending"] = True

        for ad, entry in self._linked_ads(ticket):
            ad["处理状态"] = "待复核（暂停输出）"
            ad["复核状态"] = PENDING
            ad["复核结论"] = ""
            ad["待办"] = f"复核 {entry.get('流水编号', '')} 新聚合结果后再输出"
            ad["待复核变更"] = ticket.get("变更编号", "")
            ad["pending"] = True

    def detect_change(
        self,
        fact_id: int,
        values: dict[str, Any] | None = None,
        *,
        effective_time: Any = None,
        version: Any = None,
    ) -> dict[str, Any]:
        """检测事实定义变更并落到一条待复核待办；重复检测命中同一条记录。"""
        submitted = values or {}
        with store.lock:
            fact = store.find(FACT_MODULE, fact_id)
            if fact is None:
                return {
                    "ok": False,
                    "message": f"事实定义 {fact_id} 不存在或已归档",
                    "entry": None,
                    "ticket": None,
                }

            supplied = {
                field: _text(submitted[field])
                for field in TRACKED_FIELDS
                if field in submitted
            }
            if not supplied:
                return {
                    "ok": False,
                    "message": "未提交粒度说明、关联维度、度量字段或分区字段，无法检测口径变更",
                    "entry": fact,
                    "ticket": None,
                }

            combined_snapshot = dict(_snapshot(fact))
            pending = self._pending_ticket(fact_id)
            if pending is not None:
                # 待办里已有未生效的新口径时以它为基线，避免并发的部分提交把口径改回旧值。
                combined_snapshot.update({
                    field: value for field, value in pending.get("新口径", {}).items()
                })
            combined_snapshot.update(supplied)
            combined_changes = _diff(_snapshot(fact), combined_snapshot)

            if pending is not None:
                higher = (
                    int(version) if version not in (None, "")
                    else self._next_version(fact, pending),
                    _norm_time(effective_time) or _now(),
                ) > (
                    int(pending.get("新版本号") or 0),
                    _norm_time(pending.get("生效时间")),
                )

            if pending is not None and pending.get("新口径") == combined_snapshot:
                self._sync_downstream(pending, fact)
                return {
                    "ok": True,
                    "message": (
                        f"重复检测：变更 {pending.get('变更编号')} 已存在，"
                        f"合并为同一条待办，未重复落库"
                    ),
                    "entry": fact,
                    "ticket": self.view(pending),
                    "merged": True,
                    "activated": False,
                }
            if not combined_changes:
                return {
                    "ok": True,
                    "message": "未检测到影响聚合口径的变更（粒度说明、关联维度、度量字段、分区字段均未变化）",
                    "entry": fact,
                    "ticket": None,
                    "activated": False,
                }

            fingerprint = self._fingerprint(fact, combined_snapshot)

            affected_rows = self._affected_aggregations(fact)
            new_version = int(
                version
                if version not in (None, "")
                else self._next_version(fact, pending)
            )
            effective_at = _norm_time(effective_time) or _now()

            if not affected_rows and pending is None:
                old_version = int(fact.get("版本号") or 0)
                old_snapshot = _snapshot(fact)
                fact.update(combined_snapshot)
                fact["版本号"] = new_version
                fact["生效时间"] = effective_at
                fact["版本状态"] = f"v{new_version} 已生效"
                fact["处理状态"] = "正常"
                for stale in ("待生效版本号", "待生效口径", "待生效生效时间", "待复核变更"):
                    fact.pop(stale, None)
                fact.setdefault("版本历史", []).append({
                    "版本号": old_version,
                    "口径": old_snapshot,
                    "失效时间": effective_at,
                    "说明": "无引用聚合，版本直接生效",
                })
                return {
                    "ok": True,
                    "message": f"未发现引用该事实的聚合流水，v{new_version} 已直接生效",
                    "entry": fact,
                    "ticket": None,
                    "activated": True,
                    "变更字段": [item["字段"] for item in combined_changes],
                }

            incoming_entries = [
                self._entry(row, fact) for row in affected_rows
            ]
            merged_existing = pending is not None

            if pending is not None:
                if higher:
                    # 高优先级变更接管待办：口径以它提交的字段为准，低优先级变更不再写入。
                    proposed = dict(_snapshot(fact))
                    proposed.update(pending.get("新口径", {}))
                    proposed.update(supplied)
                else:
                    # 低优先级变更不能覆盖高优先级已定义的新口径，只能补充对方未改的字段。
                    proposed = dict(_snapshot(fact))
                    pending_new = dict(pending.get("新口径", {}))
                    for field in TRACKED_FIELDS:
                        if _text(pending_new.get(field)) != _text(proposed.get(field)):
                            proposed[field] = pending_new.get(field, "")
                        elif field in supplied:
                            proposed[field] = supplied[field]
                changes = _diff(_snapshot(fact), proposed)
                if higher:
                    pending["受影响聚合"] = incoming_entries
                else:
                    pending["受影响聚合"] = self._merge_entries(
                        pending.get("受影响聚合", []), incoming_entries
                    )
                pending["影响窗口"] = _windows(pending["受影响聚合"])
                pending["合并次数"] = int(pending.get("合并次数") or 0) + 1
                pending["旧口径"] = dict(pending.get("旧口径") or _snapshot(fact))
                pending["新口径"] = {field: proposed.get(field, "") for field in TRACKED_FIELDS}
                merged_changes = _diff(pending["旧口径"], pending["新口径"])
                pending["字段差异"] = merged_changes
                pending["变更字段"] = [item["字段"] for item in merged_changes]
                pending["影响原因"] = _reason(merged_changes)
                pending["指纹"] = self._fingerprint(fact, pending["新口径"])
                if higher:
                    pending["旧版本号"] = int(fact.get("版本号") or 0)
                    pending["新版本号"] = new_version
                    pending["生效时间"] = effective_at
                    pending["优先级"] = {"版本号": new_version, "生效时间": effective_at}
                    pending["裁决说明"] = (
                        f"同一事实存在并行变更，按事实定义版本号与生效时间裁决："
                        f"v{new_version}（{effective_at}）优先级更高，先前变更已合并到本条待办"
                    )
                else:
                    pending["裁决说明"] = (
                        f"本次变更 v{new_version}（{effective_at}）优先级不高于 "
                        f"v{pending.get('新版本号')}（{pending.get('生效时间')}），已合并到"
                        f" {pending.get('变更编号')}，不重复落库"
                    )
                    return {
                        "ok": True,
                        "message": (
                            f"变更 {pending.get('变更编号')} 已存在，本次 v{new_version}"
                            f"（{effective_at}）优先级不高于既有待办 v{pending.get('新版本号')}"
                            f"（{pending.get('生效时间')}），已合并、未重复落库；"
                            f"保留既有待办的新口径"
                        ),
                        "entry": fact,
                        "ticket": self.view(pending),
                        "merged": True,
                        "activated": False,
                    }
                pending["更新时间"] = _now()
                ticket = pending
            else:
                ticket = {
                    "id": self._next_id(),
                    "status": PENDING,
                    "pending": True,
                    "abnormal": False,
                    "变更编号": "",
                    "事实定义ID": fact_id,
                    "事实编号": _text(fact.get("事实编号")),
                    "事实名称": _text(fact.get("事实名称")),
                    "源表名称": _text(fact.get("源表名称")),
                    "变更类型": "事实定义口径变更",
                    "变更字段": [item["字段"] for item in combined_changes],
                    "字段差异": combined_changes,
                    "旧版本号": int(fact.get("版本号") or 0),
                    "新版本号": new_version,
                    "生效时间": effective_at,
                    "旧口径": _snapshot(fact),
                    "新口径": combined_snapshot,
                    "影响原因": _reason(combined_changes),
                    "影响窗口": _windows(incoming_entries),
                    "受影响聚合": incoming_entries,
                    "处理状态": PENDING,
                    "复核状态": PENDING,
                    "复核结论": "",
                    "复核时间": "",
                    "指纹": fingerprint,
                    "优先级": {
                        "版本号": new_version,
                        "生效时间": effective_at,
                    },
                    "合并次数": 0,
                    "裁决说明": "",
                    "创建时间": _now(),
                    "更新时间": _now(),
                }
                ticket["变更编号"] = f"CHG-{ticket['id']:04d}"
                store.rows(MODULE).append(ticket)

            self._sync_downstream(ticket, fact)
            merged_fields = [
                "、".join(ticket.get("变更字段", [])) or "口径"
            ]
            window_text = "、".join(ticket.get("影响窗口", [])) or "（无时间窗口）"
            message = (
                f"已登记变更 {ticket['变更编号']}，影响原因（{merged_fields[0]}变化）："
                f"{ticket['影响原因']}；受影响聚合 {len(ticket.get('受影响聚合', []))} 条，"
                f"时间窗口 {window_text}，新版本 v{ticket['新版本号']} 待复核通过后生效"
            )
            if merged_existing:
                message = f"{message}；本次检测已合并到既有待办，不重复落库"
            return {
                "ok": True,
                "message": message,
                "entry": fact,
                "ticket": self.view(ticket),
                "merged": merged_existing,
                "activated": False,
            }

    def view(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """事实页与汇总页共用的同一份影响视图，保证两页展示一致。"""
        fact = store.find(FACT_MODULE, int(ticket.get("事实定义ID", 0))) or {}
        return {
            "变更编号": ticket.get("变更编号"),
            "事实定义ID": ticket.get("事实定义ID"),
            "事实编号": ticket.get("事实编号"),
            "事实名称": ticket.get("事实名称"),
            "源表名称": fact.get("源表名称", ticket.get("源表名称", "")),
            "变更字段": list(ticket.get("变更字段", [])),
            "字段差异": list(ticket.get("字段差异", [])),
            "旧版本号": ticket.get("旧版本号"),
            "新版本号": ticket.get("新版本号"),
            "生效时间": ticket.get("生效时间"),
            "影响原因": ticket.get("影响原因"),
            "旧口径": dict(ticket.get("旧口径", {})),
            "新口径": dict(ticket.get("新口径", {})),
            "影响窗口": list(ticket.get("影响窗口", [])),
            "处理状态": ticket.get("处理状态", PENDING),
            "复核状态": ticket.get("复核状态", PENDING),
            "复核结论": ticket.get("复核结论", ""),
            "版本状态": fact.get("版本状态", ""),
            "受影响聚合": [dict(entry) for entry in ticket.get("受影响聚合", [])],
            "合并次数": ticket.get("合并次数", 0),
            "裁决说明": ticket.get("裁决说明", ""),
            "创建时间": ticket.get("创建时间", ""),
            "更新时间": ticket.get("更新时间", ""),
            "复核时间": ticket.get("复核时间", ""),
        }

    def list_tickets(
        self,
        *,
        factdef_id: int | None = None,
        dws_id: int | None = None,
        fact_no: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        tickets = list(store.rows(MODULE))
        if factdef_id is not None:
            tickets = [
                row for row in tickets
                if int(row.get("事实定义ID", 0)) == int(factdef_id)
            ]
        if fact_no:
            tickets = [row for row in tickets if _text(row.get("事实编号")) == _text(fact_no)]
        if status:
            tickets = [row for row in tickets if row.get("处理状态") == status]
        if dws_id is not None:
            filtered = []
            for row in tickets:
                if any(
                    int(entry.get("聚合ID", 0)) == int(dws_id)
                    for entry in row.get("受影响聚合", [])
                ):
                    filtered.append(row)
            tickets = filtered
        return [self.view(row) for row in reversed(tickets)]

    def ticket_for_aggregation(
        self, dws_id: int
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        row = store.find(DWS_MODULE, dws_id)
        number = _text(row.get("流水编号")) if row else ""
        for ticket in reversed(store.rows(MODULE)):
            if ticket.get("处理状态") != PENDING:
                continue
            for entry in ticket.get("受影响聚合", []):
                if int(entry.get("聚合ID", 0)) == int(dws_id):
                    return ticket, entry
                if number and _text(entry.get("新聚合流水")) == number:
                    return ticket, entry
        return None, None

    def _find_by_number(self, number: str) -> dict[str, Any] | None:
        for row in store.rows(DWS_MODULE):
            if _text(row.get("流水编号")) == _text(number):
                return row
        return None

    def rerun_aggregation(self, dws_id: int) -> tuple[dict[str, Any] | None, str]:
        """按新口径重跑聚合，旧聚合流水原样保留，只追加新的结果流水。"""
        with store.lock:
            row = store.find(DWS_MODULE, dws_id)
            if row is None:
                return None, f"聚合流水 {dws_id} 不存在或已归档"
            ticket, entry = self.ticket_for_aggregation(dws_id)
            if ticket is None or entry is None:
                return None, f"聚合流水 {row.get('流水编号')} 没有待复核的口径变更，无需重跑"
            if entry.get("复核状态") == "已核对":
                return None, f"聚合流水 {entry.get('流水编号')} 已完成核对，不能重复重跑"
            if entry.get("新聚合流水"):
                produced = self._find_by_number(entry["新聚合流水"])
                if produced is not None:
                    return produced, (
                        f"重跑结果 {entry['新聚合流水']} 已产出，等待核对；旧口径聚合仍按原样保留"
                    )

            new_version = int(ticket.get("新版本号") or 0)
            new_snapshot = dict(ticket.get("新口径") or {})
            rows = store.rows(DWS_MODULE)
            base_number = _text(row.get("流水编号"))
            root_number = re.sub(r"(-R\d+)+$", "", base_number) or base_number
            rerun_count = sum(
                1 for item in rows
                if _text(item.get("流水编号")).startswith(f"{root_number}-R")
            ) + 1
            new_number = f"{root_number}-R{rerun_count}"
            new_id = max((int(item.get("id", 0)) for item in rows), default=0) + 1

            new_row = {
                "id": new_id,
                "status": "已完成",
                "pending": False,
                "abnormal": False,
                "流水编号": new_number,
                "输入表名": row.get("输入表名", ""),
                "输出表名": row.get("输出表名", ""),
                "聚合字段": new_snapshot.get("度量字段", ""),
                "汇总粒度": new_snapshot.get("粒度说明", ""),
                "时间窗口": row.get("时间窗口", ""),
                "预计算开关": row.get("预计算开关", ""),
                "执行批次": f"{row.get('执行批次', '')}-R{rerun_count}",
                "事实编号": ticket.get("事实编号", ""),
                "事实版本": new_version,
                "关联维度": new_snapshot.get("关联维度", ""),
                "分区字段": new_snapshot.get("分区字段", ""),
                "复核状态": "待核对",
                "复核结论": "",
                "影响窗口": entry.get("时间窗口", ""),
                "影响原因": ticket.get("影响原因", ""),
                "待复核版本": new_version,
                "待复核变更": ticket.get("变更编号", ""),
                "历史口径": False,
                "重跑来源": base_number,
                "生成时间": _now(),
            }
            rows.append(new_row)

            # 旧聚合先标记为待留存，口径字段原样不动；只有核对通过后才转为历史口径。
            row["pending"] = True
            row["复核状态"] = "重跑中"
            row["影响原因"] = ticket.get("影响原因", "")
            row["影响窗口"] = entry.get("时间窗口", "")
            row["待复核版本"] = new_version
            row["待复核变更"] = ticket.get("变更编号", "")
            row["重跑批次"] = new_number

            entry["新聚合流水"] = new_number
            entry["复核状态"] = "待核对"
            entry["重跑版本"] = new_version
            entry["重跑时间"] = _now()
            ticket["复核结论"] = (
                f"{base_number} 已按 v{new_version} 口径重跑，新聚合流水 {new_number} 等待核对；"
                f"历史聚合按 v{entry.get('原口径版本')} 口径保留"
            )
            ticket["更新时间"] = _now()
            return new_row, (
                f"已按 v{new_version} 口径重跑，生成 {new_number}；旧聚合 {base_number} 按原口径保留，"
                f"等待核对新结果"
            )

    def verify_aggregation(
        self, dws_id: int, conclusion: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """核对新聚合结果；全部受影响聚合核对通过后，新版本才对下游生效。"""
        with store.lock:
            row = store.find(DWS_MODULE, dws_id)
            if row is None:
                return None, f"聚合流水 {dws_id} 不存在或已归档"
            ticket, entry = self.ticket_for_aggregation(dws_id)
            if ticket is None or entry is None:
                if row.get("复核状态") == "历史口径已留存":
                    return None, f"聚合流水 {row.get('流水编号')} 的历史口径已留存，对应变更已完成复核"
                return None, f"聚合流水 {row.get('流水编号')} 没有待复核的口径变更"
            if entry.get("复核状态") == "已核对":
                return None, f"聚合流水 {entry.get('流水编号')} 已完成核对，无需重复核对"

            target = row if _text(row.get("复核状态")) == "待核对" else None
            if target is None and entry.get("新聚合流水"):
                target = self._find_by_number(entry["新聚合流水"])
            if target is None or _text(target.get("复核状态")) != "待核对":
                return None, (
                    f"聚合流水 {entry.get('流水编号')} 还没产出新口径聚合结果，"
                    f"请先执行「重跑聚合」再核对"
                )

            new_version = int(ticket.get("新版本号") or 0)
            result_text = (
                _text(conclusion)
                or f"新聚合结果已核对，与 v{new_version} 口径一致"
            )
            target["复核状态"] = "已核对"
            target["复核结论"] = result_text
            target["status"] = "已完成"
            target["pending"] = False
            target["abnormal"] = False
            target["核对时间"] = _now()
            target["处理状态"] = "已核对"

            entry["复核状态"] = "已核对"
            entry["复核结论"] = result_text
            entry["核对时间"] = _now()

            total = len(ticket.get("受影响聚合", []))
            done = sum(
                1 for item in ticket.get("受影响聚合", [])
                if item.get("复核状态") == "已核对"
            )
            if done >= total:
                self._complete_ticket(ticket, result_text)
                return target, (
                    f"聚合流水 {target.get('流水编号')} 核对通过，全部 {total} 个受影响聚合已核对完成，"
                    f"事实定义 v{new_version} 已对下游生效"
                )
            ticket["复核结论"] = (
                f"已完成 {done}/{total} 个受影响聚合的核对，待全部核对后 v{new_version} 才生效"
            )
            ticket["更新时间"] = _now()
            return target, (
                f"聚合流水 {target.get('流水编号')} 核对通过，进度 {done}/{total}；"
                f"v{new_version} 仍待剩余聚合核对完成"
            )

    def _complete_ticket(self, ticket: dict[str, Any], result_text: str) -> None:
        now = _now()
        new_version = int(ticket.get("新版本号") or 0)
        fact = store.find(FACT_MODULE, int(ticket.get("事实定义ID", 0)))
        total = len(ticket.get("受影响聚合", []))

        ticket["处理状态"] = REVIEWED
        ticket["复核状态"] = REVIEWED
        ticket["pending"] = False
        ticket["复核结论"] = (
            f"全部 {total} 个受影响聚合已按 v{new_version} 口径重跑并核对；{result_text}"
        )
        ticket["复核时间"] = now
        ticket["更新时间"] = now

        if fact is None:
            return

        old_version = int(fact.get("版本号") or 0)
        old_snapshot = _snapshot(fact)
        new_snapshot = dict(ticket.get("新口径") or {})
        fact.update(new_snapshot)
        fact["版本号"] = new_version
        fact["生效时间"] = ticket.get("生效时间") or now
        fact["版本状态"] = f"v{new_version} 已生效"
        fact["处理状态"] = "正常"
        for stale in ("待生效版本号", "待生效口径", "待生效生效时间", "待复核变更"):
            fact.pop(stale, None)
        fact.setdefault("版本历史", []).append({
            "版本号": old_version,
            "口径": old_snapshot,
            "失效时间": fact["生效时间"],
            "变更编号": ticket.get("变更编号", ""),
            "说明": "历史口径按当时版本保留，未被覆盖",
        })

        for entry in ticket.get("受影响聚合", []):
            original = store.find(DWS_MODULE, int(entry.get("聚合ID", 0)))
            if original is None:
                continue
            original["复核状态"] = "历史口径已留存"
            original["复核结论"] = (
                f"v{old_version} 历史聚合原样保留，未覆盖；新口径结果见 "
                f"{entry.get('新聚合流水') or '—'}"
            )
            original["处理状态"] = "历史口径已留存"
            original["历史口径"] = True
            original["pending"] = False
            original["abnormal"] = False
            original["生效版本"] = new_version

        for ad, _entry in self._linked_ads(ticket):
            ad["处理状态"] = REVIEWED
            ad["复核状态"] = REVIEWED
            ad["复核结论"] = f"上游变更 {ticket.get('变更编号')} 已复核，v{new_version} 口径生效"
            ad["待办"] = ""
            ad["待复核变更"] = ""
            ad["pending"] = False
            ad["abnormal"] = False

    def downstream_state(self, ad: dict[str, Any]) -> dict[str, Any]:
        """应用层输出按汇总层待办实时同步，避免未完成复核的度量被当成已完成结果。"""
        for ticket in reversed(store.rows(MODULE)):
            if ticket.get("处理状态") != PENDING:
                continue
            for entry in ticket.get("受影响聚合", []):
                if _text(ad.get("输入表名")) == _text(entry.get("输出表名")):
                    return {
                        "处理状态": "待复核（暂停输出）",
                        "复核状态": PENDING,
                        "复核结论": ticket.get("复核结论") or "上游度量变更待复核",
                        "待办": f"复核 {entry.get('流水编号', '')} 新聚合结果后再输出",
                        "待复核变更": ticket.get("变更编号", ""),
                    }
        return {}


fact_change_service = FactChangeService()
