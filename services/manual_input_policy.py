"""手动键盘输入的优先/间隔策略。

设置页里的“键盘输入优先”“输入间隔”原本没有任何消费方，属于死开关。
这里把它们接到“给小智发语音”的手动输入链路上：

- 优先开启：自动弹幕正在处理时，手动输入仍可立即进入发送流程；
- 优先关闭：自动弹幕忙时先拦住手动输入，避免两条语音打架；
- 输入间隔：两次手动输入之间的最小时间差。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from data_access import load_keyboard_settings


@dataclass(frozen=True)
class ManualInputDecision:
    allowed: bool
    priority: bool
    interval: float
    reason: str = ""


class ManualInputPolicy:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_at = 0.0

    @staticmethod
    def evaluate(
        settings: dict, last_at: float, now: float | None = None
    ) -> ManualInputDecision:
        priority = bool((settings or {}).get("keyboard_input_priority", True))
        try:
            interval = max(
                0.0,
                float((settings or {}).get("keyboard_input_interval", 0.5)),
            )
        except (TypeError, ValueError):
            interval = 0.5
        current = time.time() if now is None else float(now)
        if last_at > 0 and current - last_at < interval:
            return ManualInputDecision(
                False,
                priority,
                interval,
                f"手动输入间隔未到（{interval:g} 秒）",
            )
        return ManualInputDecision(True, priority, interval)

    def allow(self, now: float | None = None) -> ManualInputDecision:
        with self._lock:
            decision = self.evaluate(
                load_keyboard_settings(), self._last_at, now
            )
            if decision.allowed:
                self._last_at = time.time() if now is None else float(now)
            return decision
