from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from typing import Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from data_access import (
    load_ai_barrage_settings,
    load_barrage_interval_settings,
    load_category_settings,
    load_filter_states,
    load_priority_settings,
)
from services.category_manager import CATEGORY_FOR_TYPE, CategoryManager
from services.hardware_text import MAX_PROMPT_CHARS, PREFIX


DEBUG_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data",
    "ai_responder_debug.log",
)
# 已实测设备上限 13 字（含“请说：”）。这里同步到 13，避免这里先按
# 15 字放行、发送层再硬切最后两个字，导致话说到一半掉字。
MAX_HARDWARE_TEXT = 13
# 分类话术后面还会由硬件桥自动补“请说：”，正文必须给前缀留出空间。
MAX_CATEGORY_BODY = MAX_PROMPT_CHARS - len(PREFIX)

# 直播间是实时场景：积压太久的弹幕不再补播。
STALE_SECONDS = {
    "member": 120,
    "enter": 120,
    # 观众提问只回答“当前直播间”的问题；超过 2 分钟就丢弃，不再补播。
    "message": 120,
    "like": 300,
    "social": 600,
    "follow": 600,
    "fansclub": 600,
    "gift": 900,
    "share": 900,
}
DEFAULT_STALE_SECONDS = 600


def _dbg(text: str) -> None:
    """把队列流转写入文件，GUI 日志之外方便排查漏读。"""
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%H:%M:%S} {text}\n")
    except Exception:
        pass


class AiBarrageResponder(QObject):
    """把抖音弹幕排队送给小智，让小智用语音复述并回复。"""

    status_changed = pyqtSignal(str)

    PRIORITY = {
        "message": 0,
        "like": 2,
        "gift": 1,
        "social": 2,
        "fansclub": 2,
        "share": 3,
        "member": 0,
        "enter": 0,
    }

    def __init__(
        self,
        ai_client,
        log: Callable[[str], None] | None = None,
        parent=None,
        category_manager: CategoryManager | None = None,
    ):
        super().__init__(parent)
        self.ai_client = ai_client
        self.log = log or (lambda _text: None)
        self.category_manager = category_manager or CategoryManager()
        self._queue: list[dict] = []
        self._seq = 0
        self._busy = False
        self._busy_since = 0.0
        self._last_sent_at = 0.0
        self._last_voice_at = 0.0
        self._current_item = None
        self._pending_enters: dict[str, float] = {}
        self._last_welcome_at = 0.0
        self._last_type_queue_at: dict[str, float] = {}
        self._last_reply = ""
        self._last_cooldown_status_at = 0.0
        self._settings = load_ai_barrage_settings()
        self._category_settings = load_category_settings()
        self._interval_settings = load_barrage_interval_settings()
        self._priority_settings = load_priority_settings()
        self._filter_states = load_filter_states()
        self._settings_loaded_at = 0.0
        self._last_state_diag_at = 0.0
        self._client_busy_since = 0.0
        self._idle_since = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._welcome_timer = QTimer(self)
        self._welcome_timer.setInterval(500)
        self._welcome_timer.timeout.connect(self._flush_enters)
        self._welcome_timer.start()
        self.ai_client.reply_finished.connect(self._on_reply_finished)
        self.ai_client.error_occurred.connect(self._on_client_error)
        self.ai_client.stt_received.connect(self._on_stt)

    def reload_settings(self):
        self._refresh_runtime_settings(force=True)

    def _refresh_runtime_settings(self, force: bool = False):
        """配置改了不用重启：最多 1 秒重新读一次弹幕设置页的全部配置。"""
        now = time.time()
        if not force and now - self._settings_loaded_at < 1.0:
            return
        self._settings = load_ai_barrage_settings()
        self._category_settings = load_category_settings()
        self._interval_settings = load_barrage_interval_settings()
        self._priority_settings = load_priority_settings()
        self._filter_states = load_filter_states()
        self._settings_loaded_at = now

    def rebind_client(self, ai_client):
        """在同一套弹幕队列上切换软件小智/硬件小智。"""
        if ai_client is self.ai_client:
            return
        old = self.ai_client
        for signal_name, slot in (
            ("reply_finished", self._on_reply_finished),
            ("error_occurred", self._on_client_error),
            ("stt_received", self._on_stt),
        ):
            try:
                getattr(old, signal_name).disconnect(slot)
            except Exception:
                pass
        self.ai_client = ai_client
        ai_client.reply_finished.connect(self._on_reply_finished)
        ai_client.error_occurred.connect(self._on_client_error)
        ai_client.stt_received.connect(self._on_stt)
        self._queue.clear()
        self._busy = False
        self._current_item = None
        self._emit_status()

    @property
    def pending_count(self) -> int:
        # 排队中的弹幕 + 还没轮到的进场人都算积压，定时话题要给它们让路。
        return len(self._queue) + len(self._pending_enters)

    @property
    def last_reply(self) -> str:
        return self._last_reply

    def submit(self, kind: str, data: dict, force: bool = False) -> bool:
        if not isinstance(data, dict):
            return False
        if not force and not self._settings.get("enabled", True):
            _dbg(f"REJECT disabled kind={kind}")
            return False
        msg_type = str(kind or data.get("msg_type") or "message")
        answer_enabled = bool(self._settings.get("answer_enabled", True))
        welcome_enabled = bool(self._settings.get("welcome_enabled", True))
        if msg_type == "message":
            if not (answer_enabled or force):
                _dbg("REJECT answer_enabled=off")
                return False
        elif msg_type in {"member", "enter", "like", "social", "follow", "gift", "fansclub", "share"}:
            if not (welcome_enabled or force):
                _dbg(f"REJECT welcome_enabled=off type={msg_type}")
                return False
        else:
            return False

        content = str(data.get("content") or "").strip()
        username = str(data.get("username") or "观众").strip() or "观众"
        if msg_type == "message" and not content:
            return False
        if len(content) > 200:
            content = content[:200] + "…"

        # 点赞是高频事件：短时间内只回一次，避免几十个赞把队列刷满。
        if msg_type == "like" and not force:
            now = time.time()
            try:
                cooldown = max(
                    0.0, float(self._settings.get("like_queue_cooldown", 30.0))
                )
            except (TypeError, ValueError):
                cooldown = 30.0
            last = self._last_type_queue_at.get("like", 0.0)
            already_pending = any(
                item.get("msg_type") == "like" for item in self._queue
            )
            if already_pending or (cooldown and now - last < cooldown):
                _dbg(f"SKIP-LIKE-COOLDOWN {username}")
                return True
            self._last_type_queue_at["like"] = now

        # 进场的人先攒着，隔一段时间随机挑一位问候，不用每个人都欢迎。
        if msg_type in {"member", "enter"}:
            now = time.time()
            self._pending_enters[username] = now
            _dbg(f"PENDING-ENTER {username} total={len(self._pending_enters)}")
            if len(self._pending_enters) > 20:
                oldest = sorted(self._pending_enters.items(), key=lambda kv: kv[1])
                dropped = [name for name, _when in oldest[:-20]]
                self._pending_enters = dict(oldest[-20:])
                self.log(
                    "🗑️ 进场积压超过 20 人，丢弃最早："
                    + "、".join(dropped[:3])
                )
            self._emit_status()
            return True

        prompt = self._build_prompt(msg_type, username, content, data)
        if not prompt:
            return False
        self._seq += 1
        self._queue.append(
            {
                "prompt": prompt,
                "dialog": msg_type == "message" and not prompt.startswith("请说"),
                "msg_type": msg_type,
                "priority": self._item_priority(msg_type),
                "interval": self._item_interval(msg_type),
                "seq": self._seq,
                "force": bool(force),
                "label": f"{username}: {content}" if content else f"{username} {msg_type}",
                "received_at": time.time(),
            }
        )
        self._trim_queue()
        _dbg(f"QUEUE {msg_type} {username}: {content[:20]} -> {len(self._queue)}")
        self._emit_status()
        return True

    def _like_dedupe_window(self) -> float:
        mode = str(self._category_settings.get("like_dedup_mode", "once") or "once")
        if mode == "all":
            return 0.0
        try:
            return max(
                0.0,
                float(self._category_settings.get("like_dedup_interval", 60) or 60),
            )
        except (TypeError, ValueError):
            return 60.0

    @staticmethod
    def _selected_types(settings: dict) -> set[str]:
        raw = settings.get("types") or settings.get("priority_types") or {}
        if isinstance(raw, dict):
            return {str(key) for key, value in raw.items() if value}
        if isinstance(raw, (list, tuple, set)):
            return {str(key) for key in raw}
        return set()

    @staticmethod
    def _type_levels(settings: dict) -> dict[str, int]:
        raw = settings.get("levels") or settings.get("priority_levels") or {}
        if not isinstance(raw, dict):
            return {}
        levels = {}
        for key, value in raw.items():
            try:
                levels[str(key)] = max(1, min(10, int(value)))
            except (TypeError, ValueError):
                levels[str(key)] = 5
        return levels

    def _priority_type_enabled(self, msg_type: str) -> bool:
        if not bool(self._priority_settings.get("enabled", False)):
            return False
        raw_type = "social" if msg_type == "follow" else (
            "member" if msg_type == "enter" else msg_type
        )
        selected = self._selected_types(self._priority_settings)
        return raw_type in selected or msg_type in selected

    def _item_priority(self, msg_type: str) -> int:
        """数值越小越优先；开启优先发送后，勾选类型按设置等级排序。"""
        # 观众提问永远是第一优先：不能被进场欢迎、点赞等事件插到前面。
        # 这里直接固定为 0，不受界面上“类型等级”的配置影响。
        if msg_type == "message":
            return 0
        # “普通”模式按到达顺序走，只有“智能”模式才按类型等级重排。
        if str(self._priority_settings.get("send_mode", "normal")) != "smart":
            return 50 + self.PRIORITY.get(msg_type, 9)
        raw_type = "social" if msg_type == "follow" else (
            "member" if msg_type == "enter" else msg_type
        )
        if self._priority_type_enabled(msg_type):
            levels = self._type_levels(self._priority_settings)
            return levels.get(raw_type, levels.get(msg_type, 5))
        # 未勾选的类型排在所有优先类型之后，但彼此仍保持合理顺序。
        return 50 + self.PRIORITY.get(raw_type, 9)

    def _enter_priority_level(self) -> int | None:
        """进场类型在“智能优先”里的等级；None 表示没勾选/未开启。"""
        if str(self._priority_settings.get("send_mode", "normal")) != "smart":
            return None
        if not self._priority_type_enabled("enter"):
            return None
        levels = self._type_levels(self._priority_settings)
        return levels.get("member", levels.get("enter", 5))

    def _item_interval(self, msg_type: str) -> float:
        """普通/优先间隔来自设置页；小智旧间隔仅作缺省兜底。"""
        try:
            fallback = float(self._settings.get("min_interval", 4.0))
        except (TypeError, ValueError):
            fallback = 4.0
        try:
            normal = float(
                self._interval_settings.get("normal_interval", fallback)
            )
        except (TypeError, ValueError):
            normal = fallback
        try:
            priority = float(
                self._interval_settings.get("priority_interval", normal)
            )
        except (TypeError, ValueError):
            priority = normal
        selected = self._item_priority(msg_type) <= 10
        return max(0.5, priority if selected else normal)

    def _category_prompt(
        self, msg_type: str, username: str, content: str, data: dict
    ) -> str:
        """按弹幕类型读取「分类话术」，让硬件和软件通道共用同一套模板。"""
        # 分类智能发送关掉时，仍要给事件一句最短的保底回应，不能让
        # 点赞/关注/礼物等事件因为设置页开关而静默丢掉。
        if not bool(self._category_settings.get("category_monitor", True)):
            return self._default_event_prompt(msg_type)
        category = CATEGORY_FOR_TYPE.get(msg_type)
        if not category:
            return ""
        context = dict(data or {})
        context["username"] = self._speakable_name(username)
        context["content"] = str(content or "").strip()
        context.setdefault("count", context.get("gift_count", 1))
        context.setdefault("gift_count", context.get("count", 1))
        context.setdefault("gift_name", "礼物")
        context.setdefault("fansclub_name", "粉丝团")
        context.setdefault("share_platform", "抖音")
        try:
            text = self.category_manager.get_next_message(category, context)
        except Exception as exc:
            _dbg(f"CATEGORY-ERR {msg_type} {exc}")
            return self._default_event_prompt(msg_type)
        fitted = self._fit_category_text(
            str(text or "").strip(), context, limit=MAX_CATEGORY_BODY
        )
        # 分类被清空时也不静默丢事件，回退到内置短句。
        return fitted or self._default_event_prompt(msg_type)

    @staticmethod
    def _default_event_prompt(msg_type: str) -> str:
        """分类话术关闭/为空时的保底短句，正文不超过 12 字。"""
        return {
            "like": "感谢大家点赞",
            "social": "感谢关注",
            "follow": "感谢关注",
            "gift": "感谢送出的礼物",
            "fansclub": "欢迎加入粉丝团",
            "share": "感谢分享直播间",
            "member": "欢迎新朋友来到直播间",
            "enter": "欢迎新朋友来到直播间",
        }.get(msg_type, "")

    @staticmethod
    def _fit_category_text(text: str, context: dict, limit: int) -> str:
        """分类话术必须适配硬件短句上限，优先缩短昵称而不是砍掉句尾。"""
        body = str(text or "").strip()
        if len(body) <= limit:
            return body
        # 长昵称最常见：先按超出长度缩短昵称，保留“欢迎/感谢”等完整语义。
        for field in ("username", "gift_name", "fansclub_name"):
            value = str(context.get(field) or "").strip()
            if not value or value not in body:
                continue
            overflow = len(body) - limit
            keep = max(1, len(value) - overflow)
            if keep < len(value):
                replacement = value[:keep].rstrip("，,、。.：:；; ")
                # 昵称只保留能放下的前几个字，宁意念半个名字，也不要
                # 整句只剩“新朋友”，否则用户会以为这个人被跳过了。
                if field == "username" and len(replacement) < 2:
                    replacement = "新朋友"
                body = body.replace(value, replacement)
            if len(body) <= limit:
                break
        return body[:limit].strip("，。！？!?、,.：:；;…~～ ")

    def _build_prompt(self, msg_type: str, username: str, content: str, data: dict) -> str:
        if msg_type == "message":
            # 小智是对话式的：把弹幕当成观众说的话原样投给它，让它自己回。
            # 不能加“复述/回答”前缀，否则它只会复读。
            # 硬件文本通道硬上限 16 字，长弹幕优先保留问题主干。
            original = str(content or "").strip()
            body = self._shorten_message(original, MAX_HARDWARE_TEXT)
            if body != original:
                _dbg(
                    f"SHORTEN {len(original)}->{len(body)} "
                    f"{original[:24]} => {body}"
                )
            # 关键：观众弹幕直接原句发送，不带昵称、不带“请说：”。
            # 带前缀会被小智当成朗读指令，只复述问题不回答。
            return body
        # 点赞、关注、礼物、粉丝团、分享、进场全部走分类话术。
        return self._category_prompt(msg_type, username, content, data)

    @staticmethod
    def _speakable_name(username: str) -> str:
        """纯符号/表情昵称不适合朗读，统一念成“新朋友”。"""
        name = str(username or "").strip()
        if not name:
            return "新朋友"
        if any(ch.isalnum() for ch in name):
            return name
        return "新朋友"

    @staticmethod
    def _shorten_message(text: str, limit: int = MAX_HARDWARE_TEXT) -> str:
        """把长弹幕压成小智能接住的短句，优先保留疑问主干。"""
        original = str(text or "").strip()
        # 15 字以内必须原样交给小智，不删“小智/主播/请问”等开头，
        # 否则观众的问题会被平台改写，小智回答的就不是原问题了。
        if len(original) <= limit:
            return original
        body = original
        body = re.sub(r"https?://\S+", "", body)
        body = re.sub(r"@\S+", "", body)
        body = re.sub(r"\s+", "", body)
        body = body.strip("，。！？!?、,.：:；;…~～ ")
        if len(body) <= limit:
            return body

        question_words = (
            "怎么", "为什么", "什么", "啥", "多少", "哪里", "哪个",
            "能不能", "可以", "是不是", "有没有", "吗", "呢",
        )
        clauses = [
            item.strip("，。！？!?、,.：:；;…~～ ")
            for item in re.split(r"[，。！？!?；;\\n]+", body)
            if item.strip()
        ]
        candidates = [
            item for item in clauses
            if any(word in item for word in question_words)
        ]
        if candidates:
            # 最后一句通常是真正的提问；它太长时从疑问词前留一点上下文。
            picked = candidates[-1]
            if len(picked) > limit:
                hit = min(
                    (picked.find(word) for word in question_words if word in picked),
                    default=-1,
                )
                start = max(0, hit - 4) if hit >= 0 else 0
                picked = picked[start:start + limit]
            body = picked
        else:
            body = body[:limit]
        return body.strip("，。！？!?、,.：:；;…~～ ")

    @staticmethod
    def _fit(prefix: str, body: str, suffix: str = "", limit: int = 15) -> str:
        """小智的文本意图接口对长句不敏感，控制在短句内才能稳定触发回复。"""
        room = max(1, limit - len(prefix) - len(suffix))
        body = str(body or "").strip()
        if len(body) > room:
            body = body[:room]
        return f"{prefix}{body}{suffix}"

    def _multi_welcome_prompt(self, candidates):
        """多人进场只念真正放进句子的昵称，返回（话术, 已念名单）。

        小智文本通道正文只有 12 字，不能像以前那样固定念“新朋友”，
        否则日志里选中了人、语音里却没名字，看起来就像被跳过。
        """
        prefix = "欢迎"
        limit = MAX_CATEGORY_BODY
        picked = []
        used = 0
        for raw in candidates:
            spoken = self._speakable_name(raw).strip()
            if not spoken:
                continue
            room = limit - len(prefix) - used - (1 if picked else 0)
            if room < 2:
                break
            # 单个昵称最多取 5 字，给后面的人留位置。
            take = spoken[:min(room, 5)].rstrip("，,、。.：:；; ")
            if len(take) < 2:
                break
            picked.append((raw, take))
            used += len(take) + (1 if len(picked) > 1 else 0)
        if not picked:
            return "", []
        prompt = prefix + "、".join(take for _raw, take in picked)
        return prompt, [raw for raw, _take in picked]

    def _flush_enters(self):
        """进场稀疏时随机挑一位问候；密集时立即合并播报。"""
        if not self._pending_enters:
            return
        if getattr(self.ai_client, "is_in_cooldown", False):
            return
        if not bool(self._settings.get("welcome_enabled", True)):
            return
        now = time.time()
        # 进场名单也不能无限囤着：超过 2 分钟的人大概率已经离开直播间，
        # 再欢迎没有意义，直接丢弃，避免人一多就补播很久以前的进场。
        stale_enters = [
            name
            for name, when in self._pending_enters.items()
            if now - float(when or 0.0) > STALE_SECONDS["enter"]
        ]
        for name in stale_enters:
            self._pending_enters.pop(name, None)
        if stale_enters:
            _dbg(
                f"DROP-STALE-ENTER {len(stale_enters)} "
                + "、".join(stale_enters[:3])
            )
        if not self._pending_enters:
            return
        if self._busy or bool(getattr(self.ai_client, "is_busy", False)):
            return
        if now - self._last_voice_at < 3.0:
            return

        # 进场类型被评为 1 级（最高优先）时走实时通道：只看“优先发送间隔”，
        # 不再受 30 秒欢迎限速拖住；没人进场时仍按原有间隔限速。
        enter_level = self._enter_priority_level()
        realtime = enter_level == 1
        collect_window = 0.5 if realtime else 1.5

        # 进场欢迎必须限速：直播间进场机器人很多，若每 10 秒就 Abort 一次，
        # 硬件会不停重建 MQTT 会话，严重时直接重启，表现就是“突然没声音”。
        # 但 1 级优先的进场事件以实时为先，限速改用“优先发送间隔”。
        # 只有同时挤进来 3 个以上才合并成一句；合并时也尽量把名字念出来，
        # 绝不出现“选中了人但语音里只有新朋友”的假欢迎。
        dense = len(self._pending_enters) >= 3
        if realtime:
            try:
                min_gap = float(
                    self._interval_settings.get("priority_interval", 2.0)
                )
            except (TypeError, ValueError):
                min_gap = 2.0
            min_gap = max(1.0, min_gap)
        else:
            min_gap = float(
                self._settings.get("welcome_min_interval", 30.0 if dense else 15.0)
            )
        if now - self._last_welcome_at < min_gap:
            return

        # 队列里已经有一条欢迎在等时，不再继续往里堆。名字先留在
        # _pending_enters 里攒着，等下一条欢迎真正发出去后再合并播报，
        # 否则设备一忙（比如定时话题占着），欢迎会排成一长串、越拖越久。
        if any(
            item.get("msg_type") in {"member", "enter"}
            for item in self._queue
        ):
            return

        # 普通弹幕/问题已经等了 8 秒以上时先让它们发，欢迎不能无限插队。
        # 直播间进场事件是持续流，如果每轮都让欢迎优先，普通提问会被饿死。
        for item in self._queue:
            if item.get("msg_type") in {"member", "enter"}:
                continue
            try:
                waited = now - float(item.get("received_at") or now)
            except (TypeError, ValueError):
                waited = 0.0
            if waited >= 8.0:
                return

        ordered = sorted(self._pending_enters.items(), key=lambda kv: kv[1])
        if dense:
            # 多人进场：按进场先后尽量多念名字，念不下的继续留在队列里。
            candidates = [name for name, _when in ordered]
            prompt, names = self._multi_welcome_prompt(candidates)
            if not prompt:
                candidates = random.sample(
                    list(self._pending_enters),
                    min(3, len(self._pending_enters)),
                )
                prompt, names = self._multi_welcome_prompt(candidates)
        else:
            ready = [
                name
                for name, when in ordered
                if now - when >= collect_window
            ]
            if not ready:
                return
            names = [ready[0]] if realtime else [random.choice(ready)]
            spoken = self._speakable_name(names[0])
            prompt = self._category_prompt(
                "member", names[0], "进入直播间", {}
            )
            # 模板把长昵称缩成“新朋友”时，改念昵称前几个字，
            # 保证用户点名的这个人确实被读出来。
            if not prompt or (spoken and spoken[:2] not in prompt):
                prompt = "欢迎" + spoken[:5]

        # 只移除本轮真正选中的人，其余继续排队，后面随机再欢迎；
        # 不要一次清空，否则密集进场时很多人会被“秒欢迎”或直接漏掉。
        for name in names:
            self._pending_enters.pop(name, None)
        if not names:
            return
        self._last_welcome_at = now
        self._seq += 1
        if not prompt:
            # 极端兜底：至少播一句通用欢迎，不让事件静默消失。
            prompt = "欢迎新朋友"
        self._queue.append(
            {
                "prompt": prompt,
                "dialog": False,
                "msg_type": "member",
                "priority": self._item_priority("member"),
                "interval": self._item_interval("member"),
                "seq": self._seq,
                "force": False,
                "label": f"{'、'.join(names)}: 进入直播间",
                "received_at": time.time(),
            }
        )
        _dbg(
            f"ENQUEUE-WELCOME {prompt} | {'、'.join(names)} -> {len(self._queue)}"
        )
        self._trim_queue()
        self._emit_status()

    def _trim_queue(self):
        try:
            limit = max(1, int(self._settings.get("max_queue", 20)))
        except (TypeError, ValueError):
            limit = 20
        if len(self._queue) <= limit:
            return
        # 队列满时保留“高优先级 + 较新”的弹幕，丢掉已经过时的旧弹幕。
        self._queue.sort(key=lambda item: (item["priority"], -item["seq"]))
        dropped = self._queue[limit:]
        self._queue = self._queue[:limit]
        if dropped:
            labels = "、".join(
                str(item.get("label", ""))[:20] for item in dropped[:3]
            )
            self.log(
                f"🗑️ 队列已满（{limit}），丢弃 {len(dropped)} 条低优先级弹幕：{labels}"
            )

    def _tick(self):
        self._refresh_runtime_settings()
        now = time.time()
        self._drop_stale(now)
        try:
            client_busy = bool(getattr(self.ai_client, "is_busy", False))
        except Exception:
            client_busy = False
        if client_busy:
            if not self._client_busy_since:
                self._client_busy_since = now
        else:
            self._client_busy_since = 0.0
        # 播报全部结束后，隔 5 秒再把麦克风还给自动对话；
        # 这样整条弹幕/欢迎队列不会被现场人声反复顶掉。
        if not self._queue and not self._pending_enters and not self._busy:
            if not self._idle_since:
                self._idle_since = now
            elif now - self._idle_since >= 5.0:
                self._idle_since = 0.0
                release = getattr(self.ai_client, "release_auto_listening", None)
                if callable(release):
                    try:
                        release()
                    except Exception:
                        pass
        else:
            self._idle_since = 0.0
        if (
            now - self._last_state_diag_at >= 5.0
            and (self._queue or self._pending_enters or self._busy)
        ):
            self._last_state_diag_at = now
            connected = bool(getattr(self.ai_client, "_connected", False))
            audio_capture = bool(getattr(self.ai_client, "_audio_capture", False))
            reply_busy = bool(getattr(self.ai_client, "_reply_busy", False))
            bridge = getattr(self.ai_client, "_audio_bridge", None)
            playing = bool(getattr(bridge, "is_playing", False)) if bridge else False
            tts_at = float(getattr(self.ai_client, "_last_tts_at", 0.0) or 0.0)
            stt_info = getattr(self.ai_client, "_last_auto_stt", (None, 0.0))
            stt_at = float(stt_info[1] or 0.0) if isinstance(stt_info, tuple) else 0.0
            _dbg(
                f"STATE q={len(self._queue)} pending={len(self._pending_enters)} "
                f"busy={int(self._busy)} client_busy={int(client_busy)} "
                f"conn={int(connected)} audio_cap={int(audio_capture)} "
                f"sent_age={now - self._last_sent_at:.1f} "
                f"rb={int(reply_busy)} play={int(playing)} "
                f"tts_age={(now - tts_at) if tts_at else -1:.1f} "
                f"stt_age={(now - stt_at) if stt_at else -1:.1f}"
            )
        if getattr(self.ai_client, "is_in_cooldown", False):
            remaining = int(
                getattr(self.ai_client, "cooldown_remaining_minutes", 0) or 0
            )
            if now - self._last_cooldown_status_at > 30.0:
                self._last_cooldown_status_at = now
                self._emit_status(
                    f"小智连续对话保护中，暂停发送（剩余约 {remaining} 分钟）"
                )
            return
        if bool(getattr(self.ai_client, "is_paused", False)):
            # 设备重启/云端连接卡死时不把队列刷空，等它恢复后继续读。
            if now - self._last_cooldown_status_at > 15.0:
                self._last_cooldown_status_at = now
                self._emit_status("小智连接异常，暂停朗读，等待恢复")
            return
        try:
            busy_timeout = float(
                getattr(self.ai_client, "busy_timeout_seconds", 12.0) or 12.0
            )
        except (TypeError, ValueError):
            busy_timeout = 12.0
        # 某些网络/服务端情况下 tts stop 丢失，ai_client.is_busy 会一直挂住，
        # 表现为整条弹幕队列几分钟不动。超过硬上限就强制结束这一轮，
        # 宁可丢掉一次回复，也不能让后面所有弹幕一起饿死。
        hard_busy_timeout = max(20.0, busy_timeout)
        bridge = getattr(self.ai_client, "_audio_bridge", None)
        audio_playing = bool(getattr(bridge, "is_playing", False)) if bridge else False
        last_voice = float(getattr(self, "_last_voice_at", 0.0) or 0.0)
        last_tts = float(getattr(self.ai_client, "_last_tts_at", 0.0) or 0.0)
        last_audio = float(getattr(bridge, "last_audio_at", 0.0) or 0.0) if bridge else 0.0
        quiet_for = now - max(last_voice, last_tts, last_audio)
        should_recover = bool(
            self._client_busy_since
            and not self._busy
            and (self._queue or self._pending_enters)
            and not audio_playing
            and quiet_for >= 10.0
            and now - self._client_busy_since > hard_busy_timeout
        )
        if should_recover:
            # 只有在确实有弹幕积压、且用户和本机音频都安静了 10 秒以上
            # 才允许做一次兜底恢复；绝不在用户说话时抢麦。
            self._force_recover_client_busy(now)
            client_busy = False
        if self._busy and now - self._busy_since > busy_timeout:
            item = self._current_item
            self._busy = False
            self._current_item = None
            # 点赞是低价值高频事件：设备偶发卡顿时直接放弃，不能放回
            # 队首重试，否则一条点赞会反复占住 60 秒并堵住正常弹幕。
            # 欢迎语已经开过口，重试只会把同一个人名再念一遍；
            # 宁可少念一次，也不要让直播间听到重复欢迎。
            retryable = (
                item is not None
                and item.get("msg_type") not in {"like", "member", "enter"}
                and item.get("attempts", 0) < 2
            )
            if retryable:
                # 小智偶发不回（或连接刚恢复），把这条放回队首重试一次。
                self._queue.insert(0, item)
                self.log(f"⚠️ 小智回复超时，稍后重试：{item['label']}")
                self._emit_status()
            elif item is not None and item.get("msg_type") in {"like", "member", "enter"}:
                self.log(f"⚠️ 欢迎/点赞回复超时，已跳过：{item['label']}")
                self._emit_status()
            else:
                self.log("⚠️ 小智回复超时，继续处理下一条弹幕")
                self._emit_status()

        if not self._queue:
            return
        if not (self._settings.get("enabled", True) or any(item["force"] for item in self._queue)):
            return
        if not bool(getattr(self.ai_client, "_connected", False)):
            return
        # 真人语音优先级最高：刚识别到用户说话时，弹幕/进场/定时话题
        # 一律继续排队，不能为了清空队列去停用户的麦。
        if now - self._last_voice_at < 3.0:
            return
        if bool(getattr(self.ai_client, "_audio_capture", False)):
            # 自动对话会一直开着麦，必须先临时停麦才能发文本；
            # 用户刚说过话时先让一让，避免把一句话截断。
            bridge = getattr(self.ai_client, "_audio_bridge", None)
            last_audio = float(getattr(bridge, "last_audio_at", 0.0) or 0.0)
            if last_audio and now - last_audio < 1.5:
                return
            pause = getattr(self.ai_client, "pause_listening_for_text", None)
            if pause is None or not pause():
                return
            return
        self._queue.sort(key=lambda item: (item["priority"], item["seq"]))
        item = None
        # 普通弹幕等待超过 8 秒时，哪怕欢迎优先级更高，也先让提问发出去。
        # 否则源源不断的进场事件会把问题永远压在队尾。
        starved_index = None
        for index, candidate in enumerate(self._queue):
            if candidate.get("msg_type") in {"member", "enter"}:
                continue
            if not candidate.get("force") and not self._settings.get("enabled", True):
                continue
            try:
                waited = now - float(candidate.get("received_at") or now)
            except (TypeError, ValueError):
                waited = 0.0
            if waited >= 8.0:
                starved_index = index
                break
        if starved_index is not None:
            item = self._queue.pop(starved_index)
        else:
            while self._queue:
                candidate = self._queue.pop(0)
                if candidate["force"] or self._settings.get("enabled", True):
                    item = candidate
                    break
        if item is None:
            return

        # 严格串行：当前这条没读完，任何新弹幕都只排队，绝不打断。
        # 直播间进场很频繁，打断会让小智丢掉上一句的上下文。
        if self._busy or client_busy:
            self._queue.insert(0, item)
            return
        try:
            interval = max(0.5, float(item.get("interval", 4.0)))
        except (TypeError, ValueError):
            interval = 4.0
        if now - self._last_sent_at < interval:
            self._queue.insert(0, item)
            return

        # 硬件通道下交给统一调度器；软件通道继续用原来的客户端接口。
        submit_speech = getattr(self.ai_client, "submit_speech", None)
        if callable(submit_speech):
            sent = bool(
                submit_speech(
                    item["prompt"],
                    item.get("msg_type", "message"),
                    bool(item.get("dialog", False)),
                    item.get("label", ""),
                )
            )
        else:
            send_dialog = getattr(self.ai_client, "send_dialog_text", None)
            if bool(item.get("dialog", False)) and callable(send_dialog):
                sent = bool(send_dialog(item["prompt"]))
            else:
                sent = bool(self.ai_client.send_text(item["prompt"]))
        if not sent:
            attempts = int(item.get("attempts", 0) or 0) + 1
            item["attempts"] = attempts
            if attempts < 4 and item.get("msg_type") not in {"like"}:
                # 只是被统一闸门暂时挡住时，放回队首稍后重试，不能丢消息。
                self._queue.insert(0, item)
            _dbg(f"SEND-FAIL {item['label']}")
            self.log(f"⚠️ 小智没有接住弹幕：{item['label']}")
            self._emit_status()
            return
        _dbg(f"SEND {item['prompt']} | {item['label']}")
        self._busy = True
        self._busy_since = now
        self._last_sent_at = now
        item["attempts"] = int(item.get("attempts", 0)) + 1
        self._current_item = item
        self.log(f"🤖 小智正在读弹幕：{item['label']}")
        self._emit_status("小智正在读弹幕并回复...")

    def _force_recover_client_busy(self, now: float, reason: str = "") -> None:
        """客户端忙状态长时间不释放时强制收尾，避免整条队列被卡死。"""
        current = self._current_item.get("label", "") if self._current_item else ""
        _dbg(
            f"RECOVER-BUSY elapsed={now - self._client_busy_since:.1f}s "
            f"reason={reason or '超时'} current={current}"
        )
        self.log("⚠️ 小智忙状态超时，已强制恢复，继续处理队列")
        # 软件小智能直接清掉回复标记；硬件桥没有这些字段就跳过。
        for name, value in (
            ("_reply_busy", False),
            ("_reply_started_at", 0.0),
            ("_finish_guard", False),
        ):
            try:
                setattr(self.ai_client, name, value)
            except Exception:
                pass
        abort = getattr(self.ai_client, "abort", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
        # 中断后不要马上重开麦克风，否则现场人声会立刻把它又拉回自动对话。
        hold = getattr(self.ai_client, "hold_auto_listening", None)
        if callable(hold):
            try:
                hold()
            except Exception:
                pass
        self._client_busy_since = 0.0
        self._busy = False
        self._current_item = None
        self._last_sent_at = now - 2.0
        self._emit_status()

    def _drop_stale(self, now: float | None = None) -> None:
        """丢弃积压过久的弹幕，避免恢复后补播几分钟前的互动。"""
        if not self._queue:
            return
        now = time.time() if now is None else now
        fresh = []
        dropped = 0
        for item in self._queue:
            try:
                received = float(item.get("received_at") or 0)
            except (TypeError, ValueError):
                received = 0.0
            limit = STALE_SECONDS.get(
                str(item.get("msg_type", "")), DEFAULT_STALE_SECONDS
            )
            if received > 0 and now - received > limit:
                dropped += 1
                continue
            fresh.append(item)
        if dropped:
            self._queue = fresh
            self.log(f"🗑️ 已丢弃 {dropped} 条过时弹幕")

    def _on_reply_finished(self):
        self._busy = False
        self._client_busy_since = 0.0
        self._current_item = None
        self._last_reply = ""
        self._last_sent_at = time.time()
        self._emit_status()

    def _on_client_error(self, _text: str):
        self._client_busy_since = 0.0
        if self._busy:
            self._busy = False
            item = self._current_item
            self._current_item = None
            too_long = "long texts" in str(_text or "")
            # 同上：欢迎语已提交过语音，不在中断后重念，避免重复报名字。
            retryable = (
                item is not None
                and item.get("msg_type") not in {"like", "member", "enter"}
                and item.get("attempts", 0) < 2
                and not too_long
            )
            if retryable:
                # 多半是和真人说话撞车了，放回队列等安静后重试一次。
                self._queue.insert(0, item)
                self._last_sent_at = time.time()
                self.log(f"⚠️ 小智回复中断，稍后重试：{item['label']}")
            elif item is not None and item.get("msg_type") in {"like", "member", "enter"}:
                self.log(f"⚠️ 欢迎/点赞回复中断，已跳过：{item['label']}")
            elif too_long:
                # 长度问题重试也没用，直接丢弃，避免队列反复卡住。
                self.log(f"⚠️ 文本过长被小智拒绝，已跳过：{item['label'] if item else ''}")
            else:
                self.log("⚠️ 小智回复中断，继续处理下一条弹幕")
        self._emit_status()

    def _on_stt(self, _text: str):
        self._last_voice_at = time.time()

    def _emit_status(self, prefix: str = ""):
        if prefix:
            self.status_changed.emit(f"{prefix}（待处理 {len(self._queue)} 条）")
        elif self._queue:
            self.status_changed.emit(f"待处理弹幕 {len(self._queue)} 条")
        else:
            self.status_changed.emit("等待新弹幕")

    def shutdown(self):
        self._timer.stop()
        self._welcome_timer.stop()
        self._queue.clear()

