from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Callable

from data_access import (
    load_barrage_interval_settings,
    load_filter_keywords_data,
    load_filter_states,
    load_like_dedup_settings,
    load_number_patterns_data,
    load_priority_settings,
    load_barrages,
    save_barrages,
)
from services.category_manager import CATEGORY_FOR_TYPE, CategoryManager
from services.device_state import DeviceStateMachine
from services.udp_client import UdpClient, response_ok


PRIORITY_TYPES = {
    "manual", "keyboard_input", "like", "follow", "social", "gift",
    "fansclub", "share", "member", "enter",
}
SKIP_TYPES = {"roomUserSeq", "control", "roomStats"}
# 弹幕保鲜期：超过这个时间还没念的就作废，避免把几小时前的进场欢迎补播出去
STALE_SECONDS = {
    "member": 120, "enter": 120, "like": 300, "social": 600,
    "follow": 600, "fansclub": 600, "gift": 900, "share": 900,
}
DEFAULT_STALE_SECONDS = 600
TYPE_LABELS = {
    "message": "互动", "like": "点赞", "social": "关注", "gift": "礼物",
    "fansclub": "粉丝团", "share": "分享", "member": "进入直播间",
}
# 事件源偶尔会发 follow/enter 这组别名；统一映射到设置页使用的类型。
TYPE_ALIASES = {"follow": "social", "enter": "member"}


def _speakable_name(username: str) -> str:
    """纯符号/表情昵称不适合朗读，统一用“新朋友”。"""
    name = str(username or "").strip()
    if not name:
        return "新朋友"
    if any(ch.isalnum() for ch in name):
        return name
    return "新朋友"


LABEL_TYPES = {label: key for key, label in TYPE_LABELS.items()}


class BarrageManager:
    NORMAL_FILE = "received_barrages.json"
    PRIORITY_FILE = "priority_barrages.json"
    SENT_FILE = "sent_barrages.json"

    def __init__(
        self,
        device_state: DeviceStateMachine,
        udp_client: UdpClient | None = None,
        log: Callable[[str], None] | None = None,
        category_manager: CategoryManager | None = None,
    ):
        self.device_state = device_state
        self.udp_client = udp_client
        self.log = log or (lambda _message: None)
        self.category_manager = category_manager or CategoryManager()
        self._lock = threading.RLock()
        self._sending = False
        self._sending_enabled = True
        self._category_smart_enabled = True
        self._priority_enabled = False
        self._priority_types = set()
        self._priority_levels = {}
        self._send_mode = "normal"
        self._manual_send_mode = "normal"
        self._text_sender: Callable[[str], bool] | None = None
        self._last_send_at = 0.0
        self._duplicate_cache: dict[tuple, float] = {}
        self._spam_cache: dict[str, list[float]] = {}
        self._number_cache: dict[tuple, float] = {}
        self._like_cache: dict[str, float] = {}
        self._like_count: dict[str, int] = {}
        # 弹幕列表常驻内存，落盘改为后台去抖，避免每条弹幕都同步读写整文件。
        self._cache: dict[str, list[dict]] = {}
        self._dirty: set[str] = set()
        self._flush_stop = threading.Event()
        self._flush_interval = 1.5
        self._load_settings()
        self.reload_priority_settings()
        threading.Thread(
            target=self._flush_loop, daemon=True, name="barrage-flush"
        ).start()

    def _load_settings(self):
        interval = load_barrage_interval_settings()
        self.normal_interval = max(0.0, float(interval.get("normal_interval", 10.0)))
        self.priority_interval = max(0.0, float(interval.get("priority_interval", 2.0)))
        states = load_filter_states()
        filters = states.get("filters", {})
        self.filter_duplicate = bool(filters.get("duplicate", True))
        self.filter_spam = bool(filters.get("spam", True))
        self.filter_keyword = bool(
            states.get("enabled", True)
            and filters.get("inappropriate", True)
            and load_filter_keywords_data().get("enabled", True)
        )
        self.filter_number = bool(
            states.get("enabled", True) and load_number_patterns_data().get("enabled", True)
        )
        self.keywords = load_filter_keywords_data().get("keywords", [])
        self.number_patterns = load_number_patterns_data().get("patterns", [])
        like = load_like_dedup_settings()
        self.like_dedup_enabled = bool(like.get("enabled", True))
        self.like_dedup_interval = max(0, int(like.get("dedup_interval", 30)))
        self.like_max_per_user = max(0, int(like.get("max_likes_per_user", 3)))
        self.duplicate_window = max(1.0, float(states.get("duplicate_window", 30)))
        self.spam_window = max(1.0, float(states.get("spam_window", 10)))
        self.spam_limit = max(1, int(states.get("spam_limit", 3)))
        self.number_window = max(1.0, float(states.get("number_window", 10)))

    def reload_settings(self):
        with self._lock:
            self._load_settings()

    def reload_priority_settings(self):
        settings = load_priority_settings()
        raw_types = settings.get("types") or settings.get("priority_types") or {}
        selected = set()
        if isinstance(raw_types, dict):
            for key, value in raw_types.items():
                if value:
                    item_type = LABEL_TYPES.get(str(key), str(key))
                    selected.add(TYPE_ALIASES.get(item_type, item_type))
        elif isinstance(raw_types, (list, tuple, set)):
            for key in raw_types:
                item_type = LABEL_TYPES.get(str(key), str(key))
                selected.add(TYPE_ALIASES.get(item_type, item_type))
        raw_levels = settings.get("levels") or settings.get("priority_levels") or {}
        levels = {}
        if isinstance(raw_levels, dict):
            for key, value in raw_levels.items():
                try:
                    item_type = LABEL_TYPES.get(str(key), str(key))
                    item_type = TYPE_ALIASES.get(item_type, item_type)
                    levels[item_type] = max(
                        1, min(10, int(value))
                    )
                except (TypeError, ValueError):
                    item_type = LABEL_TYPES.get(str(key), str(key))
                    item_type = TYPE_ALIASES.get(item_type, item_type)
                    levels[item_type] = 5
        with self._lock:
            self._priority_enabled = bool(settings.get("enabled", False))
            self._priority_types = selected
            self._priority_levels = levels
            self._manual_send_mode = (
                "smart" if str(settings.get("send_mode", "normal")) == "smart"
                else "normal"
            )
            self._send_mode = self._manual_send_mode

    def set_manual_send_mode(self, mode):
        with self._lock:
            self._manual_send_mode = "smart" if mode == "smart" else "normal"
            self._send_mode = self._manual_send_mode

    def set_udp_client(self, client: UdpClient | None):
        self.udp_client = client

    def set_text_sender(self, sender: Callable[[str], bool] | None):
        """硬件语音通道下，用 TTS 发送器替代 UDP 文本接口。"""
        self._text_sender = sender

    def set_category_smart_enabled(self, enabled: bool):
        self._category_smart_enabled = bool(enabled)

    def pause_sending(self):
        self._sending_enabled = False
        self.log("⏸️ 弹幕发送已暂停")

    def resume_sending(self):
        self._sending_enabled = True
        self.log("▶️ 弹幕发送已恢复")

    @property
    def sending_enabled(self) -> bool:
        return self._sending_enabled

    @property
    def sending(self) -> bool:
        return self._sending

    def _read(self, name: str) -> list[dict]:
        with self._lock:
            if name not in self._cache:
                self._cache[name] = list(load_barrages(name))
            return list(self._cache[name])

    def _write(self, name: str, items: list[dict]):
        max_items = 1000 if name == self.SENT_FILE else 100
        if len(items) > max_items:
            items = items[-max_items:]
        with self._lock:
            self._cache[name] = list(items)
            self._dirty.add(name)

    def _flush_now(self):
        """把内存里标记为脏的弹幕文件批量写盘。"""
        with self._lock:
            pending = {
                name: list(self._cache.get(name, [])) for name in self._dirty
            }
            self._dirty.clear()
        failed = set()
        for name, items in pending.items():
            max_items = 1000 if name == self.SENT_FILE else 100
            try:
                if not save_barrages(name, items, max_items=max_items):
                    failed.add(name)
            except Exception:
                failed.add(name)
        if failed:
            with self._lock:
                self._dirty.update(failed)

    def _flush_loop(self):
        while not self._flush_stop.wait(self._flush_interval):
            try:
                self._flush_now()
            except Exception:
                pass

    def flush(self):
        """退出前立即落盘。"""
        try:
            self._flush_now()
        except Exception:
            pass

    def _cleanup_caches(self):
        now = time.time()
        self._duplicate_cache = {
            key: when for key, when in self._duplicate_cache.items()
            if now - when <= self.duplicate_window
        }
        self._number_cache = {
            key: when for key, when in self._number_cache.items()
            if now - when <= self.number_window
        }
        for username in list(self._spam_cache):
            self._spam_cache[username] = [
                when for when in self._spam_cache[username]
                if now - when <= self.spam_window
            ]
            if not self._spam_cache[username]:
                del self._spam_cache[username]
        for username in list(self._like_cache):
            if now - self._like_cache[username] > self.like_dedup_interval:
                self._like_cache.pop(username, None)
                self._like_count.pop(username, None)

    def _is_duplicate(self, item: dict) -> bool:
        if not self.filter_duplicate:
            return False
        content = str(item.get("content", ""))
        # 标点和空白差异不应绕过重复过滤；“你好”和“你好！”算同一条。
        content = re.sub(r"[\s，。！？!?、,.：:；;…~～]+", "", content).casefold()
        key = (
            str(item.get("username", "")),
            content,
            str(item.get("msg_type", "")),
        )
        # 礼物事件经常没有 content；若不把礼物名/数量纳入 key，同一观众
        # 连送两种不同礼物会被误判成重复。
        if str(item.get("msg_type", "")) == "gift":
            key += (
                str(item.get("gift_name", "")),
                str(item.get("gift_count", item.get("count", 1))),
            )
        now = time.time()
        previous = self._duplicate_cache.get(key)
        self._duplicate_cache[key] = now
        return previous is not None and now - previous <= self.duplicate_window

    def _is_spam(self, item: dict) -> bool:
        if not self.filter_spam:
            return False
        # 刷屏检测只针对聊天弹幕。点赞、礼物、关注等事件本身频率就高，
        # 若一起计数会把同一位观众正常的感谢/礼物回复误挡掉。
        if str(item.get("msg_type", "")) != "message":
            return False
        username = str(item.get("username", ""))
        now = time.time()
        timestamps = self._spam_cache.setdefault(username, [])
        timestamps.append(now)
        timestamps[:] = [when for when in timestamps if now - when <= self.spam_window]
        return len(timestamps) > self.spam_limit

    def _is_keyword_filtered(self, item: dict) -> bool:
        if not self.filter_keyword:
            return False
        content = str(item.get("content", ""))
        return any(keyword and str(keyword) in content for keyword in self.keywords)

    def _is_number_filtered(self, item: dict) -> bool:
        if not self.filter_number:
            return False
        # 数字过滤只拦“纯数字刷屏”（666、10k、99+ 这类），不能把
        # “今天25度穿什么”这种带数字的正常提问误杀。
        if str(item.get("msg_type", "")) != "message":
            return False
        content = re.sub(r"\s+", "", str(item.get("content", "")))
        if not content:
            return False
        now = time.time()
        for pattern in self.number_patterns:
            try:
                match = re.fullmatch(str(pattern), content)
            except re.error:
                continue
            if not match:
                continue
            key = ("number", content)
            previous = self._number_cache.get(key)
            self._number_cache[key] = now
            if previous is not None and now - previous <= self.number_window:
                return True
        return False

    def _should_process_like(self, username: str) -> bool:
        if not self.like_dedup_enabled:
            return True
        now = time.time()
        last = self._like_cache.get(username)
        if last is not None and now - last <= self.like_dedup_interval:
            count = self._like_count.get(username, 0)
            if self.like_max_per_user and count >= self.like_max_per_user:
                return False
        if last is None or now - last > self.like_dedup_interval:
            self._like_count[username] = 0
        self._like_cache[username] = now
        self._like_count[username] = self._like_count.get(username, 0) + 1
        return True

    def should_ai_read(self, data: dict) -> bool:
        """无副作用的朗读准入检查。

        重复/刷屏/数字/点赞去重都会更新窗口缓存，统一由 add_barrage
        执行一次；调用方必须只在 add_barrage 返回 True 后朗读，避免
        二次计数导致正常弹幕被误挡。
        """
        if not isinstance(data, dict):
            return False
        msg_type = str(data.get("msg_type", "message"))
        msg_type = TYPE_ALIASES.get(msg_type, msg_type)
        if msg_type in SKIP_TYPES:
            return False
        return not self._is_keyword_filtered(data)

    def add_barrage(self, data: dict, priority: bool | None = None) -> bool:
        if not isinstance(data, dict):
            return False
        msg_type = str(data.get("msg_type", "message"))
        msg_type = TYPE_ALIASES.get(msg_type, msg_type)
        if msg_type in SKIP_TYPES:
            return False
        if msg_type == "like" and not self._should_process_like(
            str(data.get("username", ""))
        ):
            return False

        with self._lock:
            self._cleanup_caches()
            if self._is_duplicate(data):
                return False
            if self._is_spam(data):
                self.log(f"🚫 刷屏过滤: {data.get('username', '')}")
                return False
            if self._is_keyword_filtered(data):
                self.log(f"🚫 关键词过滤: {data.get('content', '')[:40]}")
                return False
            if self._is_number_filtered(data):
                self.log(f"🚫 数字重复过滤: {data.get('content', '')[:40]}")
                return False

            item = dict(data)
            item["msg_type"] = msg_type
            # 外部事件源的 id 不一定是数字（测试注入、兼容协议），
            # 转不了就用时间戳兜底，别让一条弹幕把整个事件处理链搞崩。
            try:
                item["id"] = int(item.get("id") or time.time() * 1000)
            except (TypeError, ValueError):
                item["id"] = int(time.time() * 1000)
            item.setdefault("timestamp", datetime.now().strftime("%H:%M:%S"))
            item["received_at"] = time.time()
            item["sent"] = False
            selected_by_user = (
                self._priority_enabled and msg_type in self._priority_types
            )
            item["priority"] = bool(
                priority
                if priority is not None
                else selected_by_user
            )
            target = self.PRIORITY_FILE if item["priority"] else self.NORMAL_FILE
            items = self._read(target)
            items.append(item)
            self._write(target, items)
            self.log(
                f"📝 已加入{'优先' if item['priority'] else '普通'}弹幕: "
                f"{item.get('username', '未知用户')}: {item.get('content', '')}"
            )
            return True

    def _all_unsent(self) -> list[dict]:
        items = self._read(self.PRIORITY_FILE) + self._read(self.NORMAL_FILE)
        return [item for item in items if not item.get("sent", False)]

    def get_unsent_barrages(self) -> list[dict]:
        with self._lock:
            return self._all_unsent()

    def get_all_barrages(self) -> list[dict]:
        with self._lock:
            return self._read(self.PRIORITY_FILE) + self._read(self.NORMAL_FILE)

    def get_sent_barrages(self) -> list[dict]:
        with self._lock:
            return self._read(self.SENT_FILE)

    def get_counts(self) -> dict:
        with self._lock:
            unsent = len(self._all_unsent())
            sent = len(self._read(self.SENT_FILE))
            return {"total": unsent + sent, "unsent": unsent, "sent": sent}

    def _build_default_response(self, item: dict) -> str:
        msg_type = str(item.get("msg_type", "message"))
        username = _speakable_name(str(item.get("username", "未知用户")))
        content = str(item.get("content", ""))
        if msg_type == "gift":
            return (
                f"{username} 送出了 {item.get('gift_count', 1)} 个"
                f"{item.get('gift_name', '礼物')}，感谢支持！"
            )
        if msg_type in {"member", "enter"}:
            return f"{username} 进入直播间，欢迎！"
        if msg_type in {"social", "follow"}:
            return f"{username} 关注了主播，感谢关注！"
        if msg_type == "share":
            return f"{username} 分享了直播间，感谢支持！"
        if msg_type == "like":
            return f"感谢{username}的点赞支持！"
        if msg_type == "fansclub":
            return f"欢迎{username}加入粉丝团！"
        if msg_type == "manual":
            return content
        return content if username in content else f"{username}: {content}"

    def build_response(self, item: dict) -> str:
        msg_type = str(item.get("msg_type", "message"))
        # 互动弹幕必须原句投给小智，不能套“xxx说：/感谢互动”模板，
        # 否则又退化成复述问题。
        if msg_type == "message":
            return str(item.get("content") or "")
        if self._category_smart_enabled:
            category = CATEGORY_FOR_TYPE.get(msg_type)
            if category:
                context = dict(item)
                context["username"] = _speakable_name(
                    str(item.get("username", "未知用户"))
                )
                context["level"] = item.get("fansclub_level", item.get("level", 1))
                generated = self.category_manager.get_next_message(category, context)
                if generated:
                    return generated
        return self._build_default_response(item)

    @staticmethod
    def _is_fresh(item: dict) -> bool:
        """队列里的旧弹幕不再补播（进场 2 分钟、互动 10 分钟为限）。"""
        try:
            received = float(item.get("received_at") or 0)
        except (TypeError, ValueError):
            return True
        if received <= 0:
            return True
        limit = STALE_SECONDS.get(
            str(item.get("msg_type", "")), DEFAULT_STALE_SECONDS
        )
        return (time.time() - received) <= limit

    def _next_item(self) -> tuple[dict | None, float]:
        active_items = [
            item
            for name in (self.PRIORITY_FILE, self.NORMAL_FILE)
            for item in self._read(name)
            if not item.get("sent", False) and self._is_fresh(item)
        ]
        if not active_items:
            return None, self.normal_interval
        if (
            self._priority_enabled
            and self._send_mode == "smart"
            and self._priority_types
        ):
            matched = [
                item for item in active_items
                if str(item.get("msg_type", "")) in self._priority_types
            ]
            if matched:
                matched.sort(key=lambda item: (
                    self._priority_levels.get(str(item.get("msg_type", "")), 5),
                    item.get("received_at", 0),
                ))
                return matched[0], self.priority_interval
        active_items.sort(key=lambda item: item.get("received_at", 0))
        return active_items[0], self.normal_interval

    def next_send_delay(self) -> float | None:
        item, interval = self._next_item()
        if item is None:
            return None
        remaining = interval - (time.time() - self._last_send_at)
        return max(0.0, remaining)

    def try_send_next(self, force: bool = False, item_id=None) -> bool:
        with self._lock:
            if not self._sending_enabled or self._sending:
                return False
            if (
                (self.udp_client is None and self._text_sender is None)
                or self.device_state.get_state().value == "offline"
            ):
                return False
            if not self.device_state.can_send_barrage():
                return False

            if item_id is not None:
                all_items = self.get_all_barrages()
                item = next(
                    (entry for entry in all_items if str(entry.get("id")) == str(item_id)),
                    None,
                )
                interval = 0.0 if force else self.priority_interval
            else:
                item, interval = self._next_item()
            if item is None:
                return False
            if not force and time.time() - self._last_send_at < interval:
                return False

            self._sending = True

        try:
            if self._text_sender is None and self.device_state.is_idle():
                self.udp_client.send_audio_warmup(silent=True)
                self.udp_client.send_text(" ")
                self.udp_client.wake_audio()
                time.sleep(2.0)

            if (
                force and item_id is not None
                and self._manual_send_mode == "normal"
            ):
                text = str(item.get("content") or self.build_response(item))
            else:
                text = self.build_response(item)
            if self._text_sender is not None:
                try:
                    success = bool(self._text_sender(text))
                except Exception as exc:
                    self.log(f"❌ 硬件语音发送异常: {exc}")
                    success = False
            else:
                response = self.udp_client.send_text(text)
                success = response_ok(response)
            if not success:
                self.log(f"❌ 弹幕发送失败: {text}")
                return False
            self._last_send_at = time.time()
            self._move_to_sent(item, text)
            self.log(f"✅ 弹幕发送成功: {text}")
            return True
        finally:
            with self._lock:
                self._sending = False

    def _move_to_sent(self, item: dict, sent_content: str):
        with self._lock:
            item_id = item.get("id")
            item_received = item.get("received_at")

            def _same_entry(entry: dict) -> bool:
                if str(entry.get("id")) == str(item_id):
                    return True
                if not (
                    entry.get("username") == item.get("username")
                    and entry.get("content") == item.get("content")
                    and entry.get("msg_type") == item.get("msg_type")
                    and not entry.get("sent", False)
                ):
                    return False
                # 没有 id 时不能只凭用户名/内容删，否则同一观众连发两条
                # 相同弹幕会把还没发的那条误删。用接收时间做二次校验。
                try:
                    left = float(entry.get("received_at") or 0)
                    right = float(item_received or 0)
                except (TypeError, ValueError):
                    return False
                if left <= 0 or right <= 0:
                    return False
                return abs(left - right) <= 2.0

            for name in (self.PRIORITY_FILE, self.NORMAL_FILE):
                items = self._read(name)
                filtered = [entry for entry in items if not _same_entry(entry)]
                if len(filtered) != len(items):
                    self._write(name, filtered)
                    break
            sent = self._read(self.SENT_FILE)
            sent_item = dict(item)
            sent_item["sent"] = True
            sent_item["sent_time"] = time.time()
            sent_item["sent_content"] = sent_content
            sent.append(sent_item)
            self._write(self.SENT_FILE, sent)

    def add_sent_text(self, text: str, msg_type: str = "manual"):
        item = {
            "id": int(time.time() * 1000),
            "username": "老板说：",
            "content": text,
            "formatted_content": f"老板说：{text}",
            "msg_type": msg_type,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "received_at": time.time(),
            "sent": True,
            "priority": True,
            "sent_time": time.time(),
            "sent_content": text,
        }
        with self._lock:
            sent = self._read(self.SENT_FILE)
            sent.append(item)
            self._write(self.SENT_FILE, sent)

    def delete_ids(self, ids: list) -> int:
        wanted = {str(value) for value in ids}
        removed = 0
        with self._lock:
            for name in (self.PRIORITY_FILE, self.NORMAL_FILE, self.SENT_FILE):
                items = self._read(name)
                filtered = [item for item in items if str(item.get("id")) not in wanted]
                removed += len(items) - len(filtered)
                if len(filtered) != len(items):
                    self._write(name, filtered)
        return removed

    def mark_ids_sent(self, ids: list, content: str = "手动标记已发送") -> int:
        wanted = {str(value) for value in ids}
        moved = 0
        with self._lock:
            for name in (self.PRIORITY_FILE, self.NORMAL_FILE):
                items = self._read(name)
                keep = []
                for item in items:
                    if str(item.get("id")) in wanted:
                        self._move_to_sent(item, content)
                        moved += 1
                    else:
                        keep.append(item)
                if len(keep) != len(items):
                    self._write(name, keep)
        return moved

    def requeue_sent(self, ids: list | None = None) -> int:
        selected = None if ids is None else {str(value) for value in ids}
        moved = 0
        with self._lock:
            sent = self._read(self.SENT_FILE)
            keep = []
            for item in sent:
                if selected is None or str(item.get("id")) in selected:
                    item = dict(item)
                    item["sent"] = False
                    item.pop("sent_time", None)
                    item.pop("sent_content", None)
                    target = self.PRIORITY_FILE if item.get("priority") else self.NORMAL_FILE
                    items = self._read(target)
                    items.append(item)
                    self._write(target, items)
                    moved += 1
                else:
                    keep.append(item)
            self._write(self.SENT_FILE, keep)
        return moved

    def clear_sent(self) -> int:
        with self._lock:
            count = len(self._read(self.SENT_FILE))
            self._write(self.SENT_FILE, [])
            return count

    def clear_all(self) -> int:
        with self._lock:
            count = len(self._read(self.PRIORITY_FILE)) + len(self._read(self.NORMAL_FILE))
            self._write(self.PRIORITY_FILE, [])
            self._write(self.NORMAL_FILE, [])
            return count

    def set_intervals(self, normal: float, priority: float = 2.0):
        with self._lock:
            self.normal_interval = max(0.0, float(normal))
            self.priority_interval = max(0.0, float(priority))
