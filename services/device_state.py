from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable


class DeviceState(Enum):
    OFFLINE = "offline"
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"
    WAITING_ACK = "waiting_ack"
    ERROR = "error"


class DeviceStateMachine:
    """ESP32 状态机；只有 idle/listening 允许发送。"""

    def __init__(self, ack_timeout: float = 5.0):
        self._state = DeviceState.OFFLINE
        self._changed_at = time.time()
        self._lock = threading.RLock()
        self._ack_lock = threading.RLock()
        self._ack_timeout = ack_timeout
        self._ack_timer = None
        self._previous_state = None
        self._callbacks: list[Callable[[DeviceState, DeviceState], None]] = []

    def add_state_change_callback(self, callback):
        self._callbacks.append(callback)

    def set_state(self, new_state: DeviceState) -> bool:
        with self._lock:
            if new_state == self._state:
                return False
            old_state = self._state
            if new_state == DeviceState.WAITING_ACK:
                # 任何来源把状态推进 WAITING_ACK 都要有超时恢复，
                # 不能只依赖 start_sending_message 这一条路径。
                self._previous_state = (
                    old_state
                    if old_state in (DeviceState.IDLE, DeviceState.LISTENING)
                    else DeviceState.IDLE
                )
            self._state = new_state
            self._changed_at = time.time()
            callbacks = list(self._callbacks)
        if new_state == DeviceState.WAITING_ACK:
            self._start_ack_timer()
        for callback in callbacks:
            try:
                callback(old_state, new_state)
            except Exception:
                pass
        return True

    def get_state(self) -> DeviceState:
        with self._lock:
            return self._state

    def get_state_value(self) -> str:
        return self.get_state().value

    def get_state_duration(self) -> float:
        with self._lock:
            return time.time() - self._changed_at

    def can_send_barrage(self) -> bool:
        state = self.get_state()
        if (
            state == DeviceState.WAITING_ACK
            and self.get_state_duration() > self._ack_timeout
        ):
            # 外部状态轮询也可能把设备推进 WAITING_ACK；超过 ACK 超时
            # 必须自动回退，否则界面会一直显示“等待 ACK”且再也发不出。
            self.handle_ack_timeout()
            state = self.get_state()
        return state in (DeviceState.IDLE, DeviceState.LISTENING)

    def is_idle(self) -> bool:
        return self.get_state() == DeviceState.IDLE

    def is_listening(self) -> bool:
        return self.get_state() == DeviceState.LISTENING

    def is_waiting_ack(self) -> bool:
        return self.get_state() == DeviceState.WAITING_ACK

    def start_sending_message(self) -> bool:
        with self._ack_lock:
            if not self.can_send_barrage():
                return False
            self._previous_state = self.get_state()
            self.set_state(DeviceState.WAITING_ACK)
            return True

    def receive_ack(self) -> bool:
        with self._ack_lock:
            if not self.is_waiting_ack():
                return False
            self._cancel_ack_timer()
            previous = self._previous_state or DeviceState.IDLE
            self._previous_state = None
            self.set_state(previous)
            return True

    def handle_ack_timeout(self):
        with self._ack_lock:
            if not self.is_waiting_ack():
                return
            self._cancel_ack_timer()
            previous = self._previous_state or DeviceState.IDLE
            self._previous_state = None
            self.set_state(previous)

    def reset(self):
        with self._ack_lock:
            self._cancel_ack_timer()
            self._previous_state = None
        self.set_state(DeviceState.OFFLINE)

    def _start_ack_timer(self):
        self._cancel_ack_timer()
        self._ack_timer = threading.Timer(self._ack_timeout, self.handle_ack_timeout)
        self._ack_timer.daemon = True
        self._ack_timer.start()

    def _cancel_ack_timer(self):
        if self._ack_timer is not None:
            self._ack_timer.cancel()
            self._ack_timer = None
