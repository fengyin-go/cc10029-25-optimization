"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS

# 内部数据集表：代理降级决策数据集、代理检测批次、爬虫引擎代理分配记录台账。
# 它们承载跨模块数据流（代理池 → 爬虫引擎），不是独立业务模块，不进运营概览。
HIDDEN_TABLES = {"proxy_degradation", "proxy_detection", "crawler_allocation"}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        for name in HIDDEN_TABLES:
            self._tables.setdefault(name, [])

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in HIDDEN_TABLES)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """同一事务：任一环节失败就整体回退到事务前状态，不留下半份数据。"""
        snapshot = copy.deepcopy(self._tables)
        try:
            yield
        except Exception:
            self._tables = snapshot
            raise

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
