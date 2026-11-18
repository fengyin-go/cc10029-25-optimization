"""代理池业务规则：状态流转、字段校验与筛选口径都收在这里。

代理降级决策数据集的处理步骤（沿用既有数据流）：
1. 提交检测批次：读取空池（无已启用节点）与检测失败（已启用节点全部失效）；
2. 生成代理降级决策数据集：记录空池原因、最近检测批次与检测时点，
   让任务进入可控等待或指定替代区域（未完成检测的节点不能当作可用替代）；
3. 同一事务落库：决策数据集与爬虫引擎代理分配记录台账一起提交，失败整体回退；
4. 空池恢复：只允许从等待任务继续分配，已失败的请求不能无提示地重放；
5. 幂等与冲突：重复送交只写入一份；全池失效与局部恢复同时到达时，
   下游只得到一条带检测时点的生效决策。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.crawler import CrawlerService
from app.store import store

MODULE = "proxy"
DECISION_TABLE = "proxy_degradation"
DETECTION_TABLE = "proxy_detection"
LIST_FIELDS = ["节点编号", "IP地址", "端口号", "协议类型", "匿程度", "响应延迟", "存活检测", "所属区域"]
REQUIRED_FIELDS = ["节点编号", "IP地址", "端口号"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

# 检测结论与数据版本取值
CONCLUSIONS = {"存活", "失效", "未检测"}
DATA_VERSIONS = {"冻结版", "最新版"}
# 降级决策状态
DECISION_ACTIVE = "生效中"
DECISION_RECOVERED = "已恢复"
DECISION_SUPERSEDED = "已替换"

_crawler = CrawlerService()


class ProxyService:
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("节点编号", "")).casefold()]
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
            return None, f"代理节点 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于代理池可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"代理节点已{action}"

    # ---------- 代理降级决策数据集 ----------

    def list_decisions(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """代理降级决策数据集：沿用统一分页结构；为空时由代理池页面展示空态。"""
        rows = store.rows(DECISION_TABLE)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def active_decision(self) -> dict[str, Any] | None:
        for row in store.rows(DECISION_TABLE):
            if row.get("status") == DECISION_ACTIVE:
                return row
        return None

    def submit_detection(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """提交检测批次并按需生成代理降级决策数据集。

        返回 (entry, message)：entry 为 None 表示校验或落库失败；
        健康时 entry 为检测批次记录，降级/恢复时 entry 为决策记录。
        """
        results = values.get("结果")
        if not isinstance(results, list) or not results:
            return None, "缺少检测结果：请按节点提交检测结论"
        version = str(values.get("数据版本") or "冻结版").strip() or "冻结版"
        if version not in DATA_VERSIONS:
            return None, f"数据版本「{version}」不支持，仅允许冻结版或最新版"
        nodes = store.rows(MODULE)
        by_no = {str(node.get("节点编号")): node for node in nodes}
        conclusions: dict[str, str] = {}
        for item in results:
            if not isinstance(item, dict):
                return None, "检测结果格式不正确：需要 {节点编号, 检测结论} 列表"
            node_no = str(item.get("节点编号") or "").strip()
            conclusion = str(item.get("检测结论") or "").strip()
            if node_no not in by_no:
                return None, f"代理节点 {node_no or '(空)'} 不存在，不能写入检测批次"
            if conclusion not in CONCLUSIONS:
                return None, f"检测结论「{conclusion}」不合法，仅允许 存活/失效/未检测"
            conclusions[node_no] = conclusion

        batches = store.rows(DETECTION_TABLE)
        batch_no = str(values.get("批次号") or "").strip() or f"CHK-{len(batches) + 1:04d}"
        detected_at = str(values.get("检测时点") or "").strip() or datetime.now().isoformat(timespec="seconds")
        idem_key = str(values.get("幂等键") or "").strip() or f"{batch_no}|{version}"

        # 幂等：再次送交同一决策数据集只保留一份，后续请求读取已存在结果。
        for row in store.rows(DECISION_TABLE):
            if row.get("幂等键") == idem_key:
                return row, f"已存在相同降级决策 {row.get('决策编号')}（幂等键 {idem_key}），读取既有结果，未重复写入"

        try:
            with store.transaction():
                # 1. 记录检测批次并刷新节点存活状态（未检测 → 未完成检测，不能当作可用替代）。
                for node_no, conclusion in conclusions.items():
                    by_no[node_no]["存活检测"] = "未完成检测" if conclusion == "未检测" else conclusion
                batch = {
                    "id": max((int(row.get("id", 0)) for row in batches), default=0) + 1,
                    "批次号": batch_no,
                    "检测时点": detected_at,
                    "节点总数": len(nodes),
                    "存活数": sum(1 for conclusion in conclusions.values() if conclusion == "存活"),
                    "失效数": sum(1 for conclusion in conclusions.values() if conclusion == "失效"),
                    "未检测数": sum(1 for conclusion in conclusions.values() if conclusion == "未检测"),
                }
                batches.append(batch)

                # 2. 评估可用性：冻结版只看本批结论，最新版看节点当前累计状态 —— 两种血缘。
                enabled = [node for node in nodes if node.get("status") == "已启用"]
                alive_in_batch = [n for n in enabled if conclusions.get(str(n.get("节点编号"))) == "存活"]
                alive_current = [n for n in enabled if n.get("存活检测") == "存活"]
                available = alive_in_batch if version == "冻结版" else alive_current
                lineage = (
                    f"冻结版@{batch_no}（本批已启用存活{len(alive_in_batch)}/{len(enabled)}）"
                    if version == "冻结版"
                    else f"最新版@{batch_no}（累计已启用存活{len(alive_current)}/{len(enabled)}）"
                )
                active = self.active_decision()

                if available:
                    # 3a. 空池恢复：只允许从等待任务继续分配，已失败请求不自动重放。
                    if active is not None:
                        active["status"] = DECISION_RECOVERED
                        active["恢复时点"] = datetime.now().isoformat(timespec="seconds")
                        released = _crawler.release_waiting_allocations()
                        if values.get("模拟落库失败"):
                            raise RuntimeError("模拟落库失败")
                        return active, (
                            f"空池已恢复（{version}判定 {len(available)} 个已启用节点存活）："
                            f"仅从等待任务继续分配 {released} 条，已失败请求需人工确认后重放"
                        )
                    return batch, f"检测完成：代理池可用（{len(available)} 个已启用节点存活），未生成降级决策"

                # 3b. 空池或全部检测失败 → 生成代理降级决策数据集。
                reason = "代理池无已启用节点" if not enabled else "全部代理节点检测失败"
                # 替代区域只认已完成检测且存活的节点；未完成检测的节点不能当作可用替代。
                if version == "冻结版":
                    candidates = [n for n in nodes if conclusions.get(str(n.get("节点编号"))) == "存活"]
                else:
                    candidates = [n for n in nodes if n.get("存活检测") == "存活"]
                region = ""
                if candidates:
                    counts: dict[str, int] = {}
                    for node in candidates:
                        name = str(node.get("所属区域") or "").strip() or "未分区"
                        counts[name] = counts.get(name, 0) + 1
                    region = max(counts, key=lambda key: counts[key])
                strategy = "替代区域" if region else "可控等待"

                # 可用性冲突：全池失效与局部恢复同时到达时，按最新检测时点收敛，
                # 下游只得到一条带检测时点的生效决策。
                conflict_note = ""
                if active is not None:
                    active["status"] = DECISION_SUPERSEDED
                    active["恢复时点"] = detected_at
                    conflict_note = f"；与生效中的 {active.get('决策编号')} 发生可用性冲突，已按最新检测时点收敛为单条决策"

                decisions = store.rows(DECISION_TABLE)
                new_id = max((int(row.get("id", 0)) for row in decisions), default=0) + 1
                decision: dict[str, Any] = {
                    "id": new_id,
                    "决策编号": f"DEG-{new_id:04d}",
                    "空池原因": reason,
                    "最近检测批次": batch_no,
                    "检测时点": detected_at,
                    "降级策略": strategy,
                    "替代区域": region or "—",
                    "影响任务数": 0,
                    "校验结论": "通过",
                    "数据版本": version,
                    "血缘": lineage,
                    "幂等键": idem_key,
                    "恢复时点": "",
                    "status": DECISION_ACTIVE,
                    "pending": True,
                    "abnormal": True,
                }
                # 校验结论：决策要素齐全，且替代区域只能来自已完成检测的存活节点。
                well_formed = bool(reason and batch_no and detected_at) and (strategy == "可控等待" or bool(region))
                decision["校验结论"] = "通过" if well_formed else "未通过"
                decisions.append(decision)

                # 4. 同一事务：爬虫引擎消费决策数据集，生成代理分配记录并同步校验结论。
                decision["影响任务数"] = _crawler.sync_allocations_for_decision(decision)
                if values.get("模拟落库失败"):
                    raise RuntimeError("模拟落库失败")
                return decision, f"已生成代理降级决策 {decision['决策编号']}（{strategy}），影响任务 {decision['影响任务数']} 个{conflict_note}"
        except RuntimeError:
            # 同一事务失败：代理池回退，代理分配记录不会读到半份数据。
            return None, "代理降级决策数据集落库失败：同一事务已回退，代理池与代理分配记录均未写入半份数据"

    def decision_action(
        self, decision_id: int, action: str, remark: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        decision: dict[str, Any] | None = None
        for row in store.rows(DECISION_TABLE):
            if int(row.get("id", 0)) == decision_id:
                decision = row
                break
        if decision is None:
            return None, f"降级决策 {decision_id} 不存在"
        if action != "确认恢复":
            return None, f"动作「{action}」不属于降级决策可执行范围"
        if decision.get("status") != DECISION_ACTIVE:
            return None, f"决策 {decision.get('决策编号')} 已{decision.get('status')}，无需重复恢复"
        # 中断恢复要说明原因：恢复前复核当前可用性，仍无可用节点时保持等待。
        nodes = store.rows(MODULE)
        enabled_alive = [n for n in nodes if n.get("status") == "已启用" and n.get("存活检测") == "存活"]
        if not enabled_alive:
            return None, (
                f"恢复中断：最近检测批次 {decision.get('最近检测批次')} 之后仍无已完成检测且存活的已启用节点，"
                f"等待任务保持等待，请先提交新的存活检测"
            )
        with store.transaction():
            decision["status"] = DECISION_RECOVERED
            decision["恢复时点"] = datetime.now().isoformat(timespec="seconds")
            released = _crawler.release_waiting_allocations()
        return decision, f"空池已恢复：仅从等待任务继续分配 {released} 条，已失败请求需人工确认后重放"
