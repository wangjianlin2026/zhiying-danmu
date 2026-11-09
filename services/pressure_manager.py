from __future__ import annotations

import random
import threading

from data_access import load_pressure_menu_data, save_pressure_menu_data


# 逼单按 15 字原文直发给小智，不再额外加“请说：”前缀。
MAX_PRESSURE_CHARS = 15


DEFAULT_PRESSURE_MESSAGES = [
    "库存不多抓紧下单",
    "现在下单更划算",
    "今天福利别错过",
    "有问题就打在公屏",
]


class PressureManager:
    def __init__(self):
        self._lock = threading.RLock()
        data = load_pressure_menu_data()
        self.messages = list(data.get("messages", []))
        self.interval = max(1, int(data.get("interval", 60)))
        self.send_mode = str(data.get("send_mode", "sequential"))
        try:
            self.current_index = max(0, int(data.get("current_index", 0) or 0))
        except (TypeError, ValueError):
            self.current_index = 0

    def save(self):
        with self._lock:
            data = load_pressure_menu_data()
            data["messages"] = list(self.messages)
            data["interval"] = self.interval
            data["send_mode"] = self.send_mode
            data["current_index"] = self.current_index
            return save_pressure_menu_data(data)

    def add(self, message: str) -> bool:
        message = message.strip()
        if not message or len(message) > MAX_PRESSURE_CHARS:
            return False
        with self._lock:
            self.messages.append(message)
            return self.save()

    def edit(self, index: int, message: str) -> bool:
        message = message.strip()
        with self._lock:
            if not message or len(message) > MAX_PRESSURE_CHARS or not 0 <= index < len(self.messages):
                return False
            self.messages[index] = message
            return self.save()

    def delete(self, index: int) -> bool:
        with self._lock:
            if not 0 <= index < len(self.messages):
                return False
            del self.messages[index]
            if self.current_index >= len(self.messages):
                self.current_index = 0
            return self.save()

    def clear(self) -> bool:
        with self._lock:
            self.messages = []
            self.current_index = 0
            return self.save()

    def reset(self) -> bool:
        with self._lock:
            self.messages = list(DEFAULT_PRESSURE_MESSAGES)
            self.current_index = 0
            return self.save()

    def set_interval(self, interval: int) -> bool:
        with self._lock:
            self.interval = max(1, int(interval))
            return self.save()

    def set_mode(self, mode: str) -> bool:
        if mode not in {"sequential", "random"}:
            return False
        with self._lock:
            self.send_mode = mode
            if mode == "sequential":
                self.current_index = 0
            return self.save()

    def get_next(self) -> str | None:
        with self._lock:
            if not self.messages:
                return None
            if self.send_mode == "random":
                return random.choice(self.messages)
            message = self.messages[self.current_index % len(self.messages)]
            self.current_index = (self.current_index + 1) % len(self.messages)
            self.save()
            return message
