"""统一语音调度器。

直播间里同时有手动消息、观众弹幕、点赞、进场、定时话题、定时逼单等多路
输入。以前它们各自维护 busy 标志、各自抢同一把发送锁，结果就是互相排队、
超时、抢断，最后设备屏幕有字但喇叭没声音。

这里改成唯一入口：

* 所有来源都 ``submit()`` 到一个有界优先队列；
* 只有一个 worker 线程操作设备，任何时刻只朗读一条；
* 一条读完并稳定回 idle 后再冷却 2 秒，才允许下一条；
* 低优先级（话题/逼单）在队列里等太久就直接丢弃，绝不补播过期内容；
* 设备 ACK 后必须真的看到 speaking 且稳定结束，否则判失败并唤醒音频。
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal

# 数值越小越优先。
PRIORITY_MANUAL = 0      # 主播手动发的消息
PRIORITY_REPLY = 1       # 观众提问 / 礼物
PRIORITY_LIKE = 2        # 点赞
PRIORITY_ENTER = 3       # 进场欢迎 / 关注 / 分享
PRIORITY_TOPIC = 4       # 定时话题
PRIORITY_PRESSURE = 5    # 定时逼单

# 每条在队列里最多等多久；超时直接丢，避免恢复后补播一分钟前的互动。
DEFAULT_TTL = {
    PRIORITY_MANUAL: 300.0,
    PRIORITY_REPLY: 150.0,
    PRIORITY_LIKE: 60.0,
    PRIORITY_ENTER: 90.0,
    PRIORITY_TOPIC: 25.0,
    PRIORITY_PRESSURE: 40.0,
}


class SpeechScheduler(QObject):
    """单设备、单 worker、带优先级的语音发送队列。"""

    item_started = pyqtSignal(object)
    item_done = pyqtSignal(object)
    # 兼容 AiBarrageResponder：只在本条来自弹幕队列时发出。
    reply_finished = pyqtSignal()
    fatal_error = pyqtSignal(str)

    def __init__(
        self,
        speaker,
        log: Callable[[str], None] | None = None,
        parent=None,
        *,
        maxsize: int = 15,
        cooldown_seconds: float = 2.0,
        idle_stable_seconds: float = 1.5,
        active_timeout: float = 12.0,
        # 长回答（尤其“先念一遍再回答”）经常超过 20 秒；阈值太短会把
        # 正常朗读误判成失败，还会触发 30 秒失败暂停。留足余量。
        finish_timeout: float = 60.0,
        failure_pause_seconds: float = 30.0,
    ):
        super().__init__(parent)
        self._speaker = speaker
        raw_log = log or (lambda _text: None)

        def _safe_log(text: str) -> None:
            try:
                raw_log(text)
            except Exception:
                # 日志回调异常不能带走唯一的工作线程。
                pass

        self.log = _safe_log
        self._maxsize = max(1, int(maxsize))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._idle_stable_seconds = max(0.5, float(idle_stable_seconds))
        self._active_timeout = max(3.0, float(active_timeout))
        self._finish_timeout = max(5.0, float(finish_timeout))
        self._failure_pause_seconds = max(0.0, float(failure_pause_seconds))

        self._cond = threading.Condition(threading.RLock())
        self._queue: list[dict] = []
        self._active: dict | None = None
        # 话题“先念一遍再回答”的第二段：第一段读完后必须紧接着播，
        # 不允许弹幕/进场等消息插到问答中间。
        self._followup: dict | None = None
        self._seq = itertools.count(1)
        self._state = "IDLE"
        self._cooldown_until = 0.0
        self._pause_until = 0.0
        self._fail_streak = 0
        self._stopping = False
        self._worker = threading.Thread(
            target=self._worker_loop, name="speech-scheduler", daemon=True
        )
        self._worker.start()

    # ---------- 对外状态 ----------
    @property
    def state(self) -> str:
        with self._cond:
            return self._state

    @property
    def is_busy(self) -> bool:
        """有正在读的、排队的、或还在冷却，都算忙。"""
        now = time.monotonic()
        with self._cond:
            return bool(
                self._active is not None
                or self._queue
                or self._followup is not None
                or self._cooldown_until > now
            )

    @property
    def is_paused(self) -> bool:
        with self._cond:
            return time.monotonic() < self._pause_until

    @property
    def pending_count(self) -> int:
        with self._cond:
            return len(self._queue) + (1 if self._followup is not None else 0)

    def has_pending(self, coalesce_key: str) -> bool:
        with self._cond:
            if self._active is not None and self._active.get("coalesce_key") == coalesce_key:
                return True
            if (
                self._followup is not None
                and self._followup.get("coalesce_key") == coalesce_key
            ):
                return True
            return any(
                item.get("coalesce_key") == coalesce_key
                for item in self._queue
            )

    def pause_for(self, seconds: float, reason: str = "") -> None:
        with self._cond:
            self._pause_until = max(
                self._pause_until, time.monotonic() + max(0.0, seconds)
            )
        if reason:
            self.log(f"⏸️ {reason}，语音发送暂停 {int(seconds)} 秒")

    def notify_device_state(self, state_value: str) -> None:
        """主程序状态轮询回调：设备重启期间别再灌文本。"""
        state_value = str(state_value or "").lower()
        if state_value in {"activating", "booting"}:
            self.pause_for(12.0)

    # ---------- 入队 ----------
    def submit(
        self,
        text: str,
        *,
        source: str = "manual",
        priority: int = PRIORITY_TOPIC,
        ttl: float | None = None,
        coalesce_key: str | None = None,
        label: str = "",
        on_done: Callable[[dict], None] | None = None,
        follow_up: dict | None = None,
    ) -> bool:
        body = str(text or "").strip()
        if not body:
            return False
        now = time.time()
        priority = max(PRIORITY_MANUAL, min(PRIORITY_PRESSURE, int(priority)))
        if ttl is None:
            ttl = DEFAULT_TTL.get(priority, 60.0)
        ttl = max(1.0, float(ttl))
        label = str(label or body[:20])

        with self._cond:
            if self._stopping:
                return False
            self._expire_locked(now)

            if coalesce_key:
                for item in self._queue:
                    if item.get("coalesce_key") == coalesce_key:
                        item["text"] = body
                        item["label"] = label
                        item["created_at"] = now
                        item["ttl"] = ttl
                        if on_done is not None:
                            item["on_done"] = on_done
                        self._cond.notify()
                        return True

            if len(self._queue) >= self._maxsize:
                worst_index = max(
                    range(len(self._queue)),
                    key=lambda index: (
                        self._queue[index]["priority"],
                        -self._queue[index]["created_at"],
                    ),
                )
                worst = self._queue[worst_index]
                if priority >= worst["priority"]:
                    self.log(
                        f"🗑️ 语音队列已满（{self._maxsize}），丢弃新消息："
                        f"{label[:24]}"
                    )
                    return False
                self._queue.pop(worst_index)
                self.log(
                    f"🗑️ 语音队列已满，丢弃低优先级：{worst['label'][:24]}"
                )

            self._queue.append(
                {
                    "seq": next(self._seq),
                    "source": str(source or ""),
                    "text": body,
                    "priority": priority,
                    "ttl": ttl,
                    "coalesce_key": coalesce_key,
                    "label": label,
                    "created_at": now,
                    "on_done": on_done,
                    "follow_up": follow_up,
                }
            )
            self._cond.notify()
        return True

    # ---------- worker ----------
    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopping:
                    now = time.monotonic()
                    if self._pause_until > now:
                        self._cond.wait(min(0.5, self._pause_until - now))
                        continue
                    self._expire_locked(time.time())
                    if self._queue or self._followup is not None:
                        break
                    self._cond.wait(0.5)
                if self._stopping:
                    return
                item = self._pick_locked()
                if item is None:
                    continue
                self._active = item
                self._state = "RESERVED"
            self._run_item(item)

    def _pick_locked(self) -> dict | None:
        # 话题问答的第二段优先出队，保证“先念问题、马上回答”不被打断。
        if self._followup is not None:
            item = self._followup
            self._followup = None
            return item
        if not self._queue:
            return None
        now = time.time()
        high_waiting = min(item["priority"] for item in self._queue)
        if high_waiting < PRIORITY_TOPIC:
            # 有手动/弹幕在等时，低优先级的话题/逼单不再补播。
            kept = []
            for item in self._queue:
                age = now - item["created_at"]
                if item["priority"] >= PRIORITY_TOPIC and age > 8.0:
                    self.log(f"🗑️ 高优先级插队，丢弃过期低优先级：{item['label'][:24]}")
                    continue
                kept.append(item)
            self._queue = kept
            if not self._queue:
                return None
        self._queue.sort(key=lambda item: (item["priority"], item["seq"]))
        return self._queue.pop(0)

    def _expire_locked(self, now: float) -> None:
        if not self._queue:
            return
        kept = []
        dropped = []
        for item in self._queue:
            if now - item["created_at"] > item["ttl"]:
                dropped.append(item)
                continue
            kept.append(item)
        if dropped:
            self._queue = kept
            labels = "、".join(item["label"][:16] for item in dropped[:3])
            self.log(f"🗑️ 已丢弃 {len(dropped)} 条过期语音：{labels}")

    def _run_item(self, item: dict) -> None:
        started = time.monotonic()
        self.item_started.emit(
            {"source": item["source"], "label": item["label"], "text": item["text"]}
        )
        ok = False
        reason = ""
        try:
            ok, reason = self._play(item["text"])
        except Exception as exc:  # noqa: BLE001 - 设备链路任何异常都不能带走 worker
            reason = f"播放异常：{exc}"
        elapsed = time.monotonic() - started

        result = {
            "ok": bool(ok),
            "reason": "" if ok else (reason or "未知原因"),
            "source": item["source"],
            "label": item["label"],
            "text": item["text"],
            "elapsed": elapsed,
        }
        # “读到一半超过确认时限”不是设备故障：音频包还在推进，只是没在
        # 阈值内回到 idle。这种情况不能报错、不能唤醒音频、也不能复位
        # 设备状态，否则会把正常朗读打断成一次“重启”。
        soft_timeout = (
            not ok
            and reason.startswith("朗读 ")
            and "未回 idle" in reason
        )
        result["soft_timeout"] = soft_timeout
        if ok:
            self._fail_streak = 0
            self.log(f"🔊 已朗读 {elapsed:.1f}s：{item['label'][:28]}")
        elif soft_timeout:
            self._fail_streak = 0
            self.log(
                f"ℹ️ 朗读确认超时，已静默放行（不重启设备）："
                f"{item['label'][:28]}"
            )
        else:
            self._fail_streak += 1
            self.log(f"❌ 朗读失败（{result['reason']}）：{item['label'][:28]}")
            self._recover_audio()
            if (self._failure_pause_seconds
                    and self._fail_streak >= 2):
                self.pause_for(
                    self._failure_pause_seconds,
                    "设备连续两次没有出声",
                )
                self._fail_streak = 0

        if item["source"] == "barrage":
            # 必须在 cooldown 前发，否则弹幕队列会一直卡在 busy。
            self.reply_finished.emit()
        self.item_done.emit(result)

        callback = item.get("on_done")
        if callable(callback):
            try:
                callback(result)
            except Exception as exc:  # noqa: BLE001
                self.log(f"⚠️ 语音完成回调异常：{exc}")

        with self._cond:
            follow = item.get("follow_up")
            if ok and isinstance(follow, dict) and follow.get("text"):
                self._followup = follow
            self._active = None
            self._state = "COOLDOWN"
            self._cooldown_until = time.monotonic() + self._cooldown_seconds
        deadline = time.monotonic() + self._cooldown_seconds
        while time.monotonic() < deadline:
            with self._cond:
                if self._stopping:
                    break
                self._cond.wait(min(0.2, deadline - time.monotonic()))
        with self._cond:
            self._state = "IDLE"

    # ---------- 设备播放 ----------
    def _play(self, text: str) -> tuple[bool, str]:
        speaker = self._speaker
        if speaker is None or not getattr(speaker, "enabled", False):
            return False, "硬件语音通道未启用"
        if not getattr(speaker, "host", ""):
            return False, "设备 IP 未配置"

        if not speaker.send_message(text, wait_ack=True):
            return False, (
                getattr(speaker, "last_error", "") or "设备未回 ACK"
            )

        active, info = self._wait_active(self._active_timeout)
        if not active:
            self.log("🔄 设备已回 ACK 但没进入朗读，唤醒音频后重发一次")
            self._recover_audio()
            time.sleep(0.8)
            if not speaker.send_message(text, wait_ack=True):
                return False, (
                    getattr(speaker, "last_error", "") or "设备未回 ACK"
                )
            active, info = self._wait_active(self._active_timeout)
            if not active:
                return False, "ACK 后仍未进入朗读（已唤醒音频）"

        return self._wait_finished(self._finish_timeout, info)

    def _request_status(self) -> dict | None:
        speaker = self._speaker
        request = getattr(speaker, "request_status", None)
        if not callable(request):
            request = getattr(speaker, "_request_status", None)
        if not callable(request):
            return None
        try:
            response = request(0.8)
        except Exception:
            return None
        return response if isinstance(response, dict) else None

    def _wait_active(self, timeout: float) -> tuple[bool, dict]:
        deadline = time.monotonic() + timeout
        last_state = ""
        while time.monotonic() < deadline:
            response = self._request_status()
            if not response:
                time.sleep(0.3)
                continue
            state = str(response.get("state", "") or "").lower()
            last_state = state or last_state
            has_counter = "audio_utterance_rx" in response
            audio_confirmed = False
            if has_counter:
                try:
                    audio_confirmed = int(
                        response.get("audio_utterance_rx") or 0
                    ) > 0
                except (TypeError, ValueError):
                    audio_confirmed = False
            if audio_confirmed:
                return True, {
                    "state": state,
                    "has_counter": True,
                    "audio_confirmed": True,
                    "speaking_since": time.monotonic(),
                }
            if state == "speaking":
                return True, {
                    "state": state,
                    "has_counter": has_counter,
                    "audio_confirmed": False,
                    "speaking_since": time.monotonic(),
                }
            if state in {"activating", "booting", "error"}:
                return False, {"state": state}
            time.sleep(0.3)
        return False, {"state": last_state or "无回包"}

    def _wait_finished(self, timeout: float, info: dict) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout
        # 只要设备还在持续收音频包，就说明 TTS 仍在正常播放，
        # 不能因为总时长超过固定阈值就判失败（长回答天然会超过 60 秒）。
        # 这里按“有进展就续期”处理，另设一个硬上限防止真正卡死时无限等。
        hard_deadline = time.monotonic() + 300.0
        has_counter = bool(info.get("has_counter", False))
        audio_confirmed = bool(info.get("audio_confirmed", False))
        speaking_since = float(info.get("speaking_since") or 0.0)
        saw_speaking = speaking_since > 0
        idle_since = 0.0
        last_state = str(info.get("state", "") or "")
        last_audio_total = -1

        while time.monotonic() < deadline:
            response = self._request_status()
            if not response:
                time.sleep(0.3)
                continue
            state = str(response.get("state", "") or "").lower()
            last_state = state or last_state
            progress = False
            for key in ("audio_rx_total", "audio_utterance_rx"):
                if key not in response:
                    continue
                try:
                    total_now = int(response.get(key) or 0)
                except (TypeError, ValueError):
                    continue
                if total_now > last_audio_total:
                    if last_audio_total >= 0:
                        progress = True
                    last_audio_total = total_now
            if progress:
                deadline = min(
                    hard_deadline,
                    max(deadline, time.monotonic() + min(timeout, 30.0)),
                )
            if "audio_utterance_rx" in response:
                has_counter = True
                try:
                    if int(response.get("audio_utterance_rx") or 0) > 0:
                        audio_confirmed = True
                except (TypeError, ValueError):
                    pass

            if state == "speaking":
                if not speaking_since:
                    speaking_since = time.monotonic()
                saw_speaking = True
                idle_since = 0.0
            elif state == "connecting":
                idle_since = 0.0
            elif state in {"idle", "listening"}:
                if not saw_speaking:
                    time.sleep(0.3)
                    continue
                if not idle_since:
                    idle_since = time.monotonic()
                    # 已经看到 idle 时，给稳定确认留出宽限，避免刚好在
                    # 超时边界上把正常结束误报成“未回 idle”。
                    deadline = max(
                        deadline,
                        idle_since + self._idle_stable_seconds + 1.0,
                    )
                elif time.monotonic() - idle_since >= self._idle_stable_seconds:
                    speaking_time = (
                        time.monotonic() - speaking_since
                        if speaking_since else 0.0
                    )
                    if has_counter and not audio_confirmed:
                        return False, "设备已回 idle，但音频包计数为 0"
                    if not audio_confirmed and speaking_time < 1.5:
                        return False, "ACK 后瞬间回 idle，未真正开口"
                    return True, ""
            elif state in {"activating", "booting", "error"}:
                return False, f"设备状态 {state}"
            time.sleep(0.3)
        return False, f"朗读 {timeout:.0f} 秒未回 idle（state={last_state or '无回包'}）"

    def _recover_audio(self) -> None:
        recover = getattr(self._speaker, "recover_audio", None)
        if not callable(recover):
            return
        try:
            recover()
        except Exception as exc:  # noqa: BLE001
            self.log(f"⚠️ 唤醒音频失败：{exc}")

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._queue.clear()
            self._followup = None
            self._cond.notify_all()
