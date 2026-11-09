from __future__ import annotations

import random
import threading

from data_access import load_barrage_categories, save_barrage_categories


# 硬件通道会自动补“请说：”前缀，再加一个防吞字的逗号停顿。
# 15 字上限里给前缀留 3 字，分类话术正文最多 12 字。
MAX_CATEGORY_BODY_CHARS = 12
_SAMPLE_CONTEXT = {
    "username": "新朋友",
    "content": "今天天气怎么样",
    "count": 1,
    "total": 1,
    "gift_name": "礼物",
    "gift_count": 1,
    "gift_value": 0,
    "fansclub_name": "粉丝团",
    "share_platform": "抖音",
    "level": 1,
    "member_count": 0,
}


DEFAULT_CATEGORIES = {
    "like": {
        "name": "点赞",
        "messages": [
            "感谢{username}点赞",
            "谢谢{username}的点赞",
            "{username}点了{count}个赞，谢谢",
            "{username}的赞收到啦",
            "谢谢{username}支持",
            "点赞收到，谢谢{username}",
            "感谢大家点赞",
            "{username}点赞真给力",
            "赞收到啦，谢谢大家",
        ],
        "send_mode": "sequential",
        "current_index": 0,
    },
    "follow": {
        "name": "关注",
        "messages": [
            "感谢{username}关注主播",
            "欢迎{username}关注，谢谢支持",
            "{username}关注了，感谢支持",
            "{username}关注啦，欢迎",
            "谢谢{username}点关注",
            "新朋友{username}你好",
            "感谢关注，欢迎常来",
            "{username}的关注收到啦",
            "欢迎{username}加入直播间",
        ],
        "send_mode": "random",
        "current_index": 0,
    },
    "gift": {
        "name": "礼物",
        "messages": [
            "感谢{username}的{gift_name}",
            "谢谢{username}送出的{gift_name}",
            "收到{username}的{gift_name}，谢谢",
            "{username}的{gift_name}收到啦",
            "谢谢{username}的礼物",
            "感谢{username}的支持",
            "{gift_name}收到，谢谢{username}",
            "谢谢{username}送的{gift_name}",
            "礼物收到啦，谢谢",
        ],
        "send_mode": "random",
        "current_index": 0,
    },
    "fansclub": {
        "name": "粉丝团",
        "messages": [
            "欢迎{username}加入粉丝团",
            "感谢{username}加入粉丝团",
            "{username}加入粉丝团了，欢迎",
            "欢迎{username}成为家人",
            "谢谢{username}加入粉丝团",
            "{username}加入粉丝团啦",
            "粉丝团新朋友{username}",
            "感谢{username}的支持",
            "欢迎{username}回家",
        ],
        "send_mode": "random",
        "current_index": 0,
    },
    "share": {
        "name": "分享",
        "messages": [
            "感谢{username}分享直播间",
            "谢谢{username}的分享",
            "{username}分享了直播间，谢谢",
            "谢谢{username}帮忙分享",
            "{username}分享啦，感谢",
            "感谢{username}转发直播间",
            "分享收到，谢谢{username}",
            "谢谢{username}让更多人看到",
            "{username}分享得真好",
        ],
        "send_mode": "random",
        "current_index": 0,
    },
    "enter": {
        "name": "进入直播间",
        "messages": [
            "欢迎{username}来到直播间",
            "欢迎{username}进入直播间",
            "{username}来了，欢迎欢迎",
            "欢迎新朋友{username}",
            "{username}来啦，欢迎",
            "欢迎{username}进来坐坐",
            "新朋友{username}你好呀",
            "欢迎{username}来到现场",
            "{username}来啦，大家好",
            "欢迎{username}，常来玩",
        ],
        "send_mode": "sequential",
        "current_index": 0,
    },
    "message": {
        "name": "互动",
        "messages": [
            "{content}",
        ],
        "send_mode": "random",
        "current_index": 0,
    },
}

# 旧版本内置过一批长句/带“请说：”的模板。分类页可能已经把旧内容
# 写回 data/barrage_categories.json，启动时如果还认得出这些旧模板，
# 就自动换回短句，避免重启后继续把长句截成半截。
LEGACY_MESSAGES = {
    "like": {
        "❤️ {username} 给你点了 {count} 个赞，猜猜他是哪个年代的人",
        "感谢{username}的点赞支持！",
        "{username}点赞了，谢谢支持！",
        "收到{username}的点赞，太棒了！",
    },
    "follow": {
        "⭐{username}关注你了，分析下他的网名欢迎{username}关注主播！",
        "感谢{username}的关注，新朋友你好！",
        "{username}关注了，谢谢你的支持！",
    },
    "gift": {
        "🎁{username} 给你送出了{count}个{gift_name}，给他写首藏头诗",
        "感谢{username}送出的{count}个{gift_name}！",
        "收到{username}的礼物{gift_name}，太感谢了！",
        "{username}送礼物了，谢谢你的慷慨！",
    },
    "fansclub": {
        "欢迎{username}加入粉丝团！",
        "感谢{username}成为粉丝团成员！",
        "{username}加入粉丝团了，欢迎新成员！",
    },
    "share": {
        "感谢{username}分享直播间！",
        "谢谢{username}的分享，让更多人看到！",
        "{username}分享了直播间，太棒了！",
    },
    "enter": {
        "请说：欢迎{username}来到直播间",
        "欢迎{username}进入直播间！",
        "{username}来了，欢迎欢迎！",
        "欢迎新朋友{username}！",
        "{username}进入直播间，大家欢迎！",
    },
    "message": {
        "感谢{username}的互动：{content}",
        "{username}说：{content}",
        "收到{username}的互动：{content}",
        "{username}：{content}",
    },
}

# 上一版内置的 3~4 条短模板，和用户自定义内容无法区分；
# 只要整组完全一致，就迁移到当前更丰富的默认话术。
LEGACY_SHORT_DEFAULTS = {
    "like": [
        "感谢{username}点赞",
        "谢谢{username}的点赞",
        "{username}点了{count}个赞，谢谢",
    ],
    "follow": [
        "感谢{username}关注主播",
        "欢迎{username}关注，谢谢支持",
        "{username}关注了，感谢支持",
    ],
    "gift": [
        "感谢{username}的{gift_name}",
        "谢谢{username}送出的{gift_name}",
        "收到{username}的{gift_name}，谢谢",
    ],
    "fansclub": [
        "欢迎{username}加入粉丝团",
        "感谢{username}加入粉丝团",
        "{username}加入粉丝团了，欢迎",
    ],
    "share": [
        "感谢{username}分享直播间",
        "谢谢{username}的分享",
        "{username}分享了直播间，谢谢",
    ],
    "enter": [
        "欢迎{username}来到直播间",
        "欢迎{username}进入直播间",
        "{username}来了，欢迎欢迎",
        "欢迎新朋友{username}",
    ],
}


class CategoryManager:
    def __init__(self):
        self._lock = threading.RLock()
        # 随机模式也要避开“上一句刚说过的话”，否则 2~3 条模板会
        # 连续抽中同一条，观众听起来就是在重复念稿。
        self._recent_random: dict[str, list[str]] = {}
        self.categories = load_barrage_categories() or {}
        for key, default in DEFAULT_CATEGORIES.items():
            self.categories.setdefault(key, default.copy())
            self.categories[key].setdefault("messages", [])
            self.categories[key].setdefault("send_mode", "random")
            self.categories[key].setdefault("current_index", 0)
        self._migrate_legacy_messages()

    def _migrate_legacy_messages(self) -> None:
        """把旧内置长模板一次性迁回短句，保留用户后来新加的模板。"""
        changed = False
        for key, legacy in LEGACY_MESSAGES.items():
            data = self.categories.get(key)
            if not isinstance(data, dict):
                continue
            messages = [str(item) for item in data.get("messages", [])]
            old_short = LEGACY_SHORT_DEFAULTS.get(key)
            is_old_short = bool(old_short) and messages == old_short
            if not is_old_short and not any(item in legacy for item in messages):
                continue
            data["messages"] = list(DEFAULT_CATEGORIES[key]["messages"])
            data["send_mode"] = DEFAULT_CATEGORIES[key]["send_mode"]
            data["current_index"] = 0
            changed = True
        if changed:
            self.save()

    def save(self):
        with self._lock:
            return save_barrage_categories(self.categories)

    def category_names(self) -> dict:
        with self._lock:
            return {key: str(value.get("name", key)) for key, value in self.categories.items()}

    def messages(self, key: str) -> list:
        with self._lock:
            return list((self.categories.get(key) or {}).get("messages", []))

    def add_message(self, key: str, message: str) -> bool:
        with self._lock:
            if key not in self.categories:
                return False
            if not category_message_fits(message):
                return False
            self.categories[key].setdefault("messages", []).append(message)
            return self.save()

    def edit_message(self, key: str, index: int, message: str) -> bool:
        with self._lock:
            items = self.categories.get(key, {}).get("messages", [])
            if not 0 <= index < len(items):
                return False
            if not category_message_fits(message):
                return False
            items[index] = message
            return self.save()

    def delete_message(self, key: str, index: int) -> bool:
        with self._lock:
            items = self.categories.get(key, {}).get("messages", [])
            if not 0 <= index < len(items):
                return False
            del items[index]
            data = self.categories[key]
            if data.get("current_index", 0) >= len(items):
                data["current_index"] = 0
            return self.save()

    def clear_messages(self, key: str) -> bool:
        with self._lock:
            if key not in self.categories:
                return False
            self.categories[key]["messages"] = []
            self.categories[key]["current_index"] = 0
            return self.save()

    def reset_messages(self, key: str) -> bool:
        with self._lock:
            if key not in DEFAULT_CATEGORIES:
                return False
            self.categories[key]["messages"] = list(DEFAULT_CATEGORIES[key]["messages"])
            self.categories[key]["current_index"] = 0
            return self.save()

    def set_send_mode(self, key: str, mode: str) -> bool:
        with self._lock:
            if key not in self.categories or mode not in {"sequential", "random"}:
                return False
            self.categories[key]["send_mode"] = mode
            if mode == "sequential":
                self.categories[key]["current_index"] = 0
            return self.save()

    def get_next_message(self, key: str, context: dict | None = None) -> str | None:
        with self._lock:
            data = self.categories.get(key)
            if not data:
                return None
            messages = data.get("messages") or []
            if not messages:
                return None
            if data.get("send_mode", "random") == "random":
                if len(messages) > 1:
                    recent = self._recent_random.setdefault(key, [])
                    pool = [item for item in messages if item not in recent]
                    message = random.choice(pool or messages)
                    recent.append(message)
                    keep = max(1, min(2, len(messages) - 1))
                    del recent[:-keep]
                else:
                    message = messages[0]
            else:
                index = int(data.get("current_index", 0)) % len(messages)
                message = messages[index]
                data["current_index"] = (index + 1) % len(messages)
                self.save()
            return replace_variables(str(message), context or {})


def replace_variables(message: str, context: dict) -> str:
    values = {
        "username": context.get("username", "未知用户"),
        "content": context.get("content", ""),
        "count": context.get("count", context.get("gift_count", 1)),
        "total": context.get("total", 0),
        "gift_name": context.get("gift_name", "礼物"),
        "gift_count": context.get("gift_count", context.get("count", 1)),
        "gift_value": context.get("gift_value", 0),
        "fansclub_name": context.get("fansclub_name", "粉丝团"),
        "share_platform": context.get("share_platform", "未知平台"),
        "level": context.get("level", context.get("fansclub_level", 1)),
        "member_count": context.get("member_count", 0),
    }
    result = message
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def rendered_category_length(message: str) -> int:
    """按常用变量长度估算分类话术送到硬件时的正文长度。"""
    rendered = replace_variables(str(message or ""), _SAMPLE_CONTEXT)
    return len(rendered.strip())


def category_message_fits(message: str) -> bool:
    """分类话术正文必须给“请说：”前缀留出空间，避免运行时被截断。"""
    return 0 < rendered_category_length(message) <= MAX_CATEGORY_BODY_CHARS


CATEGORY_FOR_TYPE = {
    "like": "like",
    "social": "follow",
    "follow": "follow",
    "gift": "gift",
    "fansclub": "fansclub",
    "share": "share",
    "member": "enter",
    "enter": "enter",
    "message": "message",
}
