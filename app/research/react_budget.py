"""主循环与子研究共享的物理请求账本；不取代各自的局部决策上限。"""

import threading
from collections import Counter

from app.research.workbench_runtime import _RequestBudget


class UnifiedRunBudget(_RequestBudget):
    def __init__(self, maximum, max_tools=64):
        super().__init__(maximum)
        if type(max_tools) is not int or not 1 <= max_tools <= 64:
            raise ValueError("invalid_tool_budget")
        self.max_tools = max_tools
        self.tools_used = 0
        self.tool_usage = Counter()
        self.role_usage = Counter()

    def reserve(self, kind="llm", *, role=None):
        super().reserve(kind)
        with self._lock:
            self.role_usage[role or kind] += 1

    def reserve_tool(self, source):
        with self._lock:
            if self.stopped or self.tools_used >= self.max_tools:
                return False
            self.tools_used += 1
            self.tool_usage[source] += 1
            return True

    def snapshot(self):
        with self._lock:
            return {
                "model_calls": self.used,
                "llm_calls": self.llm_used,
                "vlm_calls": self.vlm_used,
                "model_remaining": max(0, self.maximum - self.used),
                "tool_calls": self.tools_used,
                "tool_remaining": max(0, self.max_tools - self.tools_used),
                "role_model_calls": dict(self.role_usage),
                "tool_usage": dict(self.tool_usage),
            }


class ResearchBudgetView:
    """子任务共享总账本，但留出主Agent最后一次决策机会。"""

    def __init__(self, parent):
        self.parent = parent
        self.start = parent.used
        self.limit = max(0, parent.maximum - parent.used - 1)
        self.rejected = False
        self._scope_lock = threading.Lock()

    def reserve(self, kind="llm", *, role=None):
        from app.research.retry import DependencyCallError

        with self._scope_lock:
            if self.parent.used - self.start >= self.limit:
                self.rejected = True
                raise DependencyCallError(code="budget_exhausted")
            self.parent.reserve(kind, role=role)

    def __getattr__(self, name):
        return getattr(self.parent, name)
