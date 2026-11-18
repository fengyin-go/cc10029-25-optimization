"""爬虫引擎 时间线服务：生成作业时间序列和示例数据。

同时承接「代理分配记录台账」：消费代理池的代理降级决策数据集，
把等待任务与已恢复任务分开落账，校验结论随决策同步过来。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "crawler"
ALLOCATION_TABLE = "crawler_allocation"
DECISION_TABLE = "proxy_degradation"
DETECTION_TABLE = "proxy_detection"
REQUIRED_FIELDS = ["任务编号", "任务名称", "关联站点"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

# 代理分配记录的分配状态：等待任务与已恢复任务必须分开。
ALLOC_WAITING = "等待中"
ALLOC_RECOVERED = "已恢复"
ALLOC_FAILED = "已失败"


class CrawlerService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get(list(row.keys())[1] if len(row) > 1 else "name", "")),
                "start": start,
                "end": end,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if row.get('status') == STATUS_ORDER[-1] else '进行中'}",
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"爬虫任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于爬虫引擎可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"爬虫任务已{action}"

    # ---------- 代理分配记录台账（消费代理降级决策数据集） ----------

    def list_allocations(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        size: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        """代理分配记录台账：每条记录带检测时点核对结论，供操作者核对是否用了过期检测节点。"""
        rows = store.rows(ALLOCATION_TABLE)
        if status:
            rows = [row for row in rows if row.get("分配状态") == status]
        batches = store.rows(DETECTION_TABLE)
        latest_ts = str(batches[-1].get("检测时点", "")) if batches else ""
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            # 仍在等待、但依据的检测时点落后于最近检测批次 → 依据已过期，不能作为分配依据。
            stale = (
                row.get("分配状态") == ALLOC_WAITING
                and latest_ts
                and str(row.get("检测时点", "")) < latest_ts
            )
            item["核对结论"] = "过期" if stale else "一致"
            items.append(item)
        total = len(items)
        start = (page - 1) * size
        return items[start:start + size], total

    def sync_allocations_for_decision(self, decision: dict[str, Any]) -> int:
        """消费一条代理降级决策：为已启用爬虫任务生成/迁移等待中的代理分配记录。

        - 校验结论随决策同步到台账；
        - 同一任务已有等待中记录时迁移到新决策（下游始终只看到一个带检测时点的依据）；
        - 必须在代理池的同一事务里调用，失败一起回退。
        """
        tasks = [row for row in store.rows(MODULE) if row.get("status") == "已启用"]
        rows = store.rows(ALLOCATION_TABLE)
        notice = (
            "任务进入可控等待"
            if decision.get("降级策略") == "可控等待"
            else f"任务等待切换至替代区域 {decision.get('替代区域')}"
        )
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        affected = 0
        for task in tasks:
            task_no = str(task.get("任务编号") or task.get("id"))
            waiting = next(
                (row for row in rows if row.get("关联任务") == task_no and row.get("分配状态") == ALLOC_WAITING),
                None,
            )
            if waiting is not None:
                waiting.update({
                    "来源决策": decision.get("决策编号"),
                    "校验结论": decision.get("校验结论", ""),
                    "检测时点": decision.get("检测时点", ""),
                    "最近检测批次": decision.get("最近检测批次", ""),
                    "血缘": decision.get("血缘", ""),
                    "提示": notice,
                })
            else:
                rows.append({
                    "id": next_id,
                    "分配编号": f"ALLOC-{next_id:04d}",
                    "关联任务": task_no,
                    "来源决策": decision.get("决策编号"),
                    "分配状态": ALLOC_WAITING,
                    "校验结论": decision.get("校验结论", ""),
                    "检测时点": decision.get("检测时点", ""),
                    "最近检测批次": decision.get("最近检测批次", ""),
                    "血缘": decision.get("血缘", ""),
                    "提示": notice,
                    "status": ALLOC_WAITING,
                    "pending": True,
                    "abnormal": False,
                })
                next_id += 1
            affected += 1
        return affected

    def release_waiting_allocations(self) -> int:
        """空池恢复：只允许从等待任务继续分配；已失败的请求不在此自动重放。"""
        released = 0
        for row in store.rows(ALLOCATION_TABLE):
            if row.get("分配状态") == ALLOC_WAITING:
                row["分配状态"] = ALLOC_RECOVERED
                row["status"] = ALLOC_RECOVERED
                row["提示"] = "空池恢复，从等待任务继续分配"
                released += 1
        return released

    def allocation_action(
        self, allocation_id: int, action: str, remark: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry: dict[str, Any] | None = None
        for row in store.rows(ALLOCATION_TABLE):
            if int(row.get("id", 0)) == allocation_id:
                entry = row
                break
        if entry is None:
            return None, f"代理分配记录 {allocation_id} 不存在"
        if action == "标记失败":
            if entry.get("分配状态") != ALLOC_WAITING:
                return None, f"仅等待中的分配可标记失败（当前为{entry.get('分配状态')}）"
            entry["分配状态"] = ALLOC_FAILED
            entry["status"] = ALLOC_FAILED
            entry["abnormal"] = True
            entry["提示"] = "请求已失败：未自动重放，需人工确认"
            return entry, "已标记失败：该请求不会自动重放"
        if action == "请求重放":
            if entry.get("分配状态") != ALLOC_FAILED:
                return None, f"仅已失败的分配需要重放（当前为{entry.get('分配状态')}）"
            if not (remark or "").strip():
                return None, "已失败的请求不能无提示地重放：请在备注中说明重放原因"
            entry["分配状态"] = ALLOC_WAITING
            entry["status"] = ALLOC_WAITING
            entry["abnormal"] = False
            entry["提示"] = f"人工确认重放：{(remark or '').strip()}"
            return entry, "已按人工确认重放，任务重新进入等待"
        return None, f"动作「{action}」不属于代理分配记录可执行范围"
