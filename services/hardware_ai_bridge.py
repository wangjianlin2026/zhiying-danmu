"""硬件模式下的语音适配器：所有文本都交给统一调度器。

这个类不再自己维护 busy 标志、超时定时器和发送线程。它只做三件事：
1. 把弹幕/话题/手动消息整理成硬件能接受的短文本；
2. 投递给 SpeechScheduler；
3. 把设备状态和兼容信号暴露给上层。

真正“同一时刻只朗读一条、读完再接下一条”的串行控制全部在
services/speech_scheduler.py 里完成。
"""
from __future__ import annotations

import random
import re
import time
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from services.hardware_text import (
    MAX_PROMPT_CHARS,
    PREFIX,
    build_hardware_message,
    build_prompt,
)
from services.speech_scheduler import (
    PRIORITY_ENTER,
    PRIORITY_LIKE,
    PRIORITY_MANUAL,
    PRIORITY_PRESSURE,
    PRIORITY_REPLY,
    PRIORITY_TOPIC,
    SpeechScheduler,
)

# 弹幕事件优先级：数字越小越先读。
_EVENT_PRIORITY = {
    "message": PRIORITY_REPLY,
    "gift": PRIORITY_REPLY,
    "like": PRIORITY_LIKE,
    "social": PRIORITY_LIKE,
    "follow": PRIORITY_LIKE,
    "fansclub": PRIORITY_LIKE,
    "member": PRIORITY_ENTER,
    "enter": PRIORITY_ENTER,
    "share": PRIORITY_ENTER,
}

# 不同事件的保鲜时间：过期就不补播。
_EVENT_TTL = {
    "message": 150.0,
    "gift": 180.0,
    "like": 60.0,
    "social": 120.0,
    "follow": 120.0,
    "fansclub": 120.0,
    "member": 90.0,
    "enter": 90.0,
    "share": 120.0,
}

_DIRECT_TYPES = {"定时话题", "手动话题", "定时逼单消息", "手动逼单消息"}

# 话题默认只发一次。之前“先念一遍再回答”的两段式会让小智先把问题
# 完整回答一遍，紧接着第二段又把同一问题回答一遍，听起来就是同一个
# 问题读两遍，因此这里关闭“先念”分支。
_QUESTION_MARKERS = (
    "吗", "怎么", "怎样", "什么", "哪", "多少", "为什么", "如何",
    "是不是", "要不要", "有没有", "谁", "几", "呢", "?", "？",
)
_TOPIC_LEAD_INS = ("", "", "诶，", "来，", "问大家一下，", "咱们聊聊，")
_READ_TOPIC_PROBABILITY = 0.0


def _clean_topic_text(text: str) -> str:
    body = str(text or "").strip()
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"@\S+", "", body)
    body = re.sub(r"\s+", "", body)
    return body.strip(
        "，。！？!?、,.：:；;…~～\"'“”‘’（）()【】[]《》<> "
    )


class HardwareAiBridge(QObject):
    stt_received = pyqtSignal(str)
    reply_received = pyqtSignal(str)
    reply_finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        sender,
        log: Callable[[str], None] | None = None,
        parent=None,
        send_lock=None,
    ):
        super().__init__(parent)
        self._sender = sender
        # 保留参数兼容旧调用；真正的串行由调度器保证。
        self._send_lock = send_lock
        self.log = log or (lambda _text: None)
        # AiBarrageResponder 会读这两个兼容字段。
        self._connected = True
        self._audio_capture = False
        self._scheduler = SpeechScheduler(
            sender, log=self.log, parent=self
        )
        # 只有弹幕来源完成时才转发 reply_finished，避免手动/话题
        # 的完成信号把弹幕队列的 busy 误清掉。
        self._scheduler.reply_finished.connect(self.reply_finished)
        self._scheduler.fatal_error.connect(self.error_occurred)

    # ---------- 兼容属性 ----------
    @property
    def is_in_cooldown(self) -> bool:
        return False

    @property
    def cooldown_remaining_minutes(self) -> int:
        return 0

    @property
    def is_busy(self) -> bool:
        return self._scheduler.is_busy

    @property
    def busy_timeout_seconds(self) -> float:
        # 调度器内部硬上限约 55 秒；给上层留出余量，避免误判超时。
        return 75.0

    @property
    def is_paused(self) -> bool:
        return self._scheduler.is_paused

    @property
    def scheduler(self) -> SpeechScheduler:
        return self._scheduler

    # ---------- 文本整理 ----------
    @staticmethod
    def _normalize_message(text: str, add_prefix: bool = True) -> str:
        body = str(text or "").strip()
        if not body:
            return ""
        if body.startswith(PREFIX) or body.startswith("请说:"):
            return body[:MAX_PROMPT_CHARS].strip(
                "，。！？!?、,.：:；;…~～ "
            )
        if not add_prefix:
            return body[:MAX_PROMPT_CHARS].strip(
                "，。！？!?、,.：:；;…~～ "
            )
        return build_hardware_message(body)

    # ---------- 统一入口：main.py 手动/话题/逼单 ----------
    def submit_message(self, text, msg_type="手动消息", on_done=None) -> bool:
        msg_type = str(msg_type or "")
        if msg_type in _DIRECT_TYPES:
            if "话题" in msg_type:
                return self._submit_topic(text, msg_type, on_done)
            # 话题/逼单压成一句短指令，不加“请说：”，让小智自己展开。
            message = build_prompt(text, MAX_PROMPT_CHARS)
            source = "topic" if "话题" in msg_type else "pressure"
            if msg_type.startswith("手动"):
                priority = PRIORITY_REPLY
                ttl = 150.0
            elif source == "topic":
                priority = PRIORITY_TOPIC
                ttl = 25.0
            else:
                priority = PRIORITY_PRESSURE
                ttl = 40.0
        else:
            message = self._normalize_message(text, add_prefix=True)
            source = "manual"
            priority = PRIORITY_MANUAL
            ttl = 300.0
        if not message:
            return False
        coalesce_key = None
        if msg_type == "定时话题":
            coalesce_key = "topic"
        elif msg_type == "定时逼单消息":
            coalesce_key = "pressure"
        return self._scheduler.submit(
            message,
            source=source,
            priority=priority,
            ttl=ttl,
            coalesce_key=coalesce_key,
            label=f"{msg_type}: {str(text or '')[:20]}",
            on_done=on_done,
        )

    @staticmethod
    def _topic_read_text(text: str) -> str:
        """按场景生成“先念一遍”的第一段；返回空串表示这次直接回答。"""
        body = _clean_topic_text(text)
        if not body:
            return ""
        if not any(marker in body for marker in _QUESTION_MARKERS):
            return ""
        if _READ_TOPIC_PROBABILITY <= 0:
            return ""
        if random.random() > _READ_TOPIC_PROBABILITY:
            return ""
        room = MAX_PROMPT_CHARS - len(PREFIX)
        if len(body) > room:
            # 太长硬念会截断成半句话，宁可这次直接自然回答。
            return ""
        leads = list(_TOPIC_LEAD_INS)
        random.shuffle(leads)
        for lead in leads:
            if len(lead) + len(body) <= room:
                return f"{PREFIX}{lead}{body}"
        return f"{PREFIX}{body}"

    def _submit_topic(self, text, msg_type, on_done=None) -> bool:
        topic = str(text or "").strip()
        answer = build_prompt(topic, MAX_PROMPT_CHARS)
        if not answer:
            return False
        manual = msg_type.startswith("手动")
        priority = PRIORITY_REPLY if manual else PRIORITY_TOPIC
        ttl = 150.0 if manual else 25.0
        coalesce_key = None if manual else "topic"
        label = f"{msg_type}: {topic[:20]}"
        read_text = self._topic_read_text(topic)
        if not read_text:
            return self._scheduler.submit(
                answer,
                source="topic",
                priority=priority,
                ttl=ttl,
                coalesce_key=coalesce_key,
                label=label,
                on_done=on_done,
            )

        def _read_done(result):
            # 第一段没念出来时不要把“回答”单独播出去；
            # 同时把失败结果回传给上层，界面仍能看到未出声原因。
            if not result.get("ok") and callable(on_done):
                on_done(dict(result, text=topic, label=label))

        follow_up = {
            "source": "topic",
            "text": answer,
            "priority": priority,
            "ttl": ttl,
            "coalesce_key": None,
            "label": f"{msg_type}·回答: {topic[:20]}",
            "created_at": time.time(),
            "on_done": on_done,
        }
        return self._scheduler.submit(
            read_text,
            source="topic",
            priority=priority,
            ttl=ttl,
            coalesce_key=coalesce_key,
            label=f"{msg_type}·先念: {topic[:20]}",
            on_done=_read_done,
            follow_up=follow_up,
        )

    # ---------- 统一入口：AiBarrageResponder ----------
    def submit_speech(
        self, text, msg_type="message", dialog=False, label=""
    ) -> bool:
        msg_type = str(msg_type or "message")
        message = self._normalize_message(text, add_prefix=not dialog)
        if not message:
            return False
        return self._scheduler.submit(
            message,
            source="barrage",
            priority=_EVENT_PRIORITY.get(msg_type, PRIORITY_REPLY),
            ttl=_EVENT_TTL.get(msg_type, 120.0),
            label=str(label or message[:20]),
        )

    # ---------- 兼容旧调用 ----------
    def send_text(self, text: str, add_prefix: bool = True) -> bool:
        priority = PRIORITY_LIKE if add_prefix else PRIORITY_REPLY
        return self._scheduler.submit(
            self._normalize_message(text, add_prefix=add_prefix),
            source="barrage",
            priority=priority,
            ttl=90.0,
            label=str(text or "")[:20],
        )

    def send_dialog_text(self, text: str) -> bool:
        return self._scheduler.submit(
            self._normalize_message(text, add_prefix=False),
            source="barrage",
            priority=PRIORITY_REPLY,
            ttl=150.0,
            label=str(text or "")[:20],
        )

    def notify_device_state(self, state_value: str) -> None:
        self._scheduler.notify_device_state(state_value)

    def notify_unhealthy(self, reason: str = "") -> None:
        self._scheduler.pause_for(30.0, reason or "设备未出声")

    def stop(self) -> None:
        self._scheduler.stop()
