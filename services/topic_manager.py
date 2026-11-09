from __future__ import annotations

import random
import threading
import time

from data_access import load_topics_data, save_topics_data


# 话题按 15 字原文直发给小智，不再额外加“请说：”前缀。
MAX_TOPIC_CHARS = 15

# 话题分类。第一个固定为“全部”，其余为实际分类。
ALL_TOPIC_CATEGORIES = "全部"
DEFAULT_TOPIC_CATEGORY = "生活日常"
TOPIC_CATEGORIES = [
    "生活日常",
    "健康习惯",
    "美食饮品",
    "娱乐影视",
    "情感话题",
    "旅行见闻",
    "历史故事",
    "地理风光",
    "人文百科",
    "数学思维",
]

# 兼容旧版 2 字分类名，首次加载时自动迁移为 4 字分类。
TOPIC_CATEGORY_ALIASES = {
    "历史": "历史故事",
    "地理": "地理风光",
    "人文": "人文百科",
    "数学": "数学思维",
}


def normalize_topic_category(name: str) -> str:
    text = str(name or "").strip()
    return TOPIC_CATEGORY_ALIASES.get(text, text)

DEFAULT_TOPICS = [
    "今天天气真不错",
    "今天心情怎么样",
    "大家早上吃什么",
    "早餐吃甜还是咸",
    "最近在追什么剧",
    "有什么下饭综艺",
    "周末一般怎么过",
    "下班后喜欢干嘛",
    "大家平时几点睡",
    "最近有好好睡觉吗",
    "最近单曲循环哪首",
    "喜欢什么类型的歌",
    "养过什么小动物",
    "猫和狗你选哪个",
    "最拿手的一道菜",
    "会做饭还是点外卖",
    "小时候爱吃啥零食",
    "你最喜欢吃什么水果",
    "火锅先下什么菜",
    "奶茶喜欢加什么料",
    "早上第一件事做啥",
    "通勤路上听什么",
    "喜欢咖啡还是茶",
    "最近学会什么新技能",
    "家里养了什么绿植",
    "多久收拾一次房间",
    "喜欢晴天还是雨天",
    "最近去过什么公园",
    "放假最想去哪儿",
    "坐火车喜欢靠窗吗",
    "旅行必带什么东西",
    "最喜欢哪个季节",
    "冬天最想吃什么",
    "夏天怎么解暑",
    "有什么坚持很久的习惯",
    "最近一次早睡是啥时",
    "喜欢一个人待着吗",
    "无聊时会做什么",
    "最想学会什么乐器",
    "最近看过什么好电影",
    "有什么童年小游戏",
    "小时候最怕什么",
    "喜欢什么运动",
    "微信头像用了多久",
    "手机里最常用啥",
    "家里最舒服的角落",
    "你最近在忙什么",
    "今天遇到什么趣事",
    "大家来自哪个城市",
    "家乡有什么好吃的",
    "最近有什么小目标",
    "最近有什么开心事",
    "新来的朋友打个招呼",
    "大家从哪刷到直播",
    "今天想聊点什么",
]


class TopicManager:
    def __init__(self):
        self._lock = threading.RLock()
        data = load_topics_data()
        self.topics = list(data.get("topics", []))
        self.current_index = max(0, int(data.get("current_index", 0)))
        self.send_mode = str(data.get("send_mode", "sequential"))
        migrated = False
        loaded_categories = data.get("categories", [])
        self.categories = list(TOPIC_CATEGORIES)
        for category in loaded_categories:
            text = normalize_topic_category(category)
            if text != str(category or "").strip():
                migrated = True
            if text and text != ALL_TOPIC_CATEGORIES and text not in self.categories:
                self.categories.append(text)
        raw_categories = data.get("topic_categories", {})
        if not isinstance(raw_categories, dict):
            raw_categories = {}
        self.topic_categories = {}
        for topic in self.topics:
            category = normalize_topic_category(
                raw_categories.get(topic, "")
            )
            if category != str(raw_categories.get(topic, "") or "").strip():
                migrated = True
            if category not in self.categories:
                category = DEFAULT_TOPIC_CATEGORY
            self.topic_categories[topic] = category
        active_category = str(
            data.get("active_category", ALL_TOPIC_CATEGORIES) or ""
        ).strip()
        normalized_active = normalize_topic_category(active_category)
        if normalized_active != active_category:
            migrated = True
            active_category = normalized_active
        if active_category != ALL_TOPIC_CATEGORIES and active_category not in self.categories:
            active_category = ALL_TOPIC_CATEGORIES
        self.active_category = active_category

        # 用户在“消息管理”页勾选的话题。非空时只在这些话题里轮播；
        # 为空时表示使用当前分类下的全部话题。
        raw_selected = data.get("selected_topics", [])
        if not isinstance(raw_selected, list):
            raw_selected = []
        self.selected_topics = []
        for topic in raw_selected:
            text = str(topic or "").strip()
            if text and text in self.topics and text not in self.selected_topics:
                self.selected_topics.append(text)
        try:
            self.no_repeat_seconds = max(0, int(data.get("no_repeat_seconds", 300)))
        except (TypeError, ValueError):
            self.no_repeat_seconds = 300
        self.recent_topics = []
        stale_recent = False
        for item in data.get("recent_topics", []):
            if not isinstance(item, dict) or not item.get("topic"):
                stale_recent = True
                continue
            try:
                topic = str(item["topic"]).strip()
                at = float(item.get("at", 0))
            except (TypeError, ValueError):
                stale_recent = True
                continue
            # 旧版本曾把“请说：xxx”长句写进不重复历史；这种记录既超出
            # 15 字硬件上限，也不该再参与话题去重，启动时顺手清掉。
            if (
                not topic
                or len(topic) > MAX_TOPIC_CHARS
                or topic.startswith("请说：")
                or topic.startswith("请说:")
            ):
                stale_recent = True
                continue
            self.recent_topics.append({"topic": topic, "at": at})
        if stale_recent:
            migrated = True
        if migrated:
            self.save()

    def save(self):
        with self._lock:
            data = load_topics_data()
            data["topics"] = list(self.topics)
            data["current_index"] = self.current_index
            data["send_mode"] = self.send_mode
            data["no_repeat_seconds"] = self.no_repeat_seconds
            data["categories"] = list(self.categories)
            data["topic_categories"] = {
                topic: self.topic_categories.get(topic, DEFAULT_TOPIC_CATEGORY)
                for topic in self.topics
            }
            data["active_category"] = self.active_category
            data["selected_topics"] = list(self.selected_topics)
            data["recent_topics"] = list(self.recent_topics)
            return save_topics_data(data)

    @property
    def active_topics(self) -> list[str]:
        with self._lock:
            if self.active_category == ALL_TOPIC_CATEGORIES:
                return list(self.topics)
            return [
                topic
                for topic in self.topics
                if self.topic_categories.get(topic) == self.active_category
            ]

    @property
    def effective_topics(self) -> list[str]:
        """已确认的素材库全集：只有确认过的话题，未确认时为空。"""
        with self._lock:
            return [
                topic for topic in self.selected_topics if topic in self.topics
            ]

    @property
    def runtime_topics(self) -> list[str]:
        """当前话题分类下真正参与轮播的素材：已确认 ∩ 当前分类。"""
        with self._lock:
            selected = [
                topic for topic in self.selected_topics if topic in self.topics
            ]
            if self.active_category == ALL_TOPIC_CATEGORIES:
                return selected
            return [
                topic
                for topic in selected
                if self.topic_categories.get(topic, DEFAULT_TOPIC_CATEGORY)
                == self.active_category
            ]

    def category_counts(self) -> dict[str, int]:
        with self._lock:
            counts = {category: 0 for category in self.categories}
            for topic in self.topics:
                category = self.topic_categories.get(
                    topic, DEFAULT_TOPIC_CATEGORY
                )
                counts[category] = counts.get(category, 0) + 1
            return counts

    def category_for_topic(self, topic: str) -> str:
        with self._lock:
            return self.topic_categories.get(
                str(topic or ""), DEFAULT_TOPIC_CATEGORY
            )

    def add_category(self, name: str) -> bool:
        """新增 4 字话题分类；分类名必须正好 4 个字符。"""
        name = str(name or "").strip()
        with self._lock:
            if (
                len(name) != 4
                or name in {ALL_TOPIC_CATEGORIES, "全部分类"}
                or name in self.categories
            ):
                return False
            self.categories.append(name)
            return self.save()

    def set_active_category(self, category: str) -> bool:
        category = str(category or "").strip()
        with self._lock:
            if category != ALL_TOPIC_CATEGORIES and category not in self.categories:
                return False
            self.active_category = category
            self.current_index = 0
            return self.save()

    def apply_visible_selection(self, visible_topics, checked_topics) -> bool:
        """确认当前分类可见的勾选结果，保留其它分类已确认的话题。"""
        visible = {str(topic or "").strip() for topic in list(visible_topics or [])}
        checked = {str(topic or "").strip() for topic in list(checked_topics or [])}
        with self._lock:
            kept = {topic for topic in self.selected_topics if topic not in visible}
            kept |= {topic for topic in checked if topic in self.topics}
            self.selected_topics = [
                topic for topic in self.topics if topic in kept
            ]
            self.current_index = 0
            return self.save()

    def set_selected_topics(self, topics) -> bool:
        """设置确认后的素材库；传空列表表示素材库为空。"""
        with self._lock:
            selected = []
            for topic in list(topics or []):
                text = str(topic or "").strip()
                if text in self.topics and text not in selected:
                    selected.append(text)
            self.selected_topics = selected
            self.current_index = 0
            return self.save()

    def clear_selected_topics(self) -> bool:
        return self.set_selected_topics([])

    def is_topic_selected(self, topic: str) -> bool:
        with self._lock:
            return str(topic or "") in self.selected_topics

    def set_topic_category(self, topic: str, category: str) -> bool:
        topic = str(topic or "").strip()
        category = str(category or "").strip()
        with self._lock:
            if topic not in self.topics or category not in self.categories:
                return False
            self.topic_categories[topic] = category
            return self.save()

    def add(self, topic: str, category: str | None = None) -> bool:
        topic = topic.strip()
        if not topic or len(topic) > MAX_TOPIC_CHARS:
            return False
        with self._lock:
            category = str(category or "").strip()
            if category not in self.categories:
                category = (
                    self.active_category
                    if self.active_category != ALL_TOPIC_CATEGORIES
                    else DEFAULT_TOPIC_CATEGORY
                )
            self.topics.append(topic)
            self.topic_categories[topic] = category
            return self.save()

    def edit(self, index: int, topic: str, category: str | None = None) -> bool:
        topic = topic.strip()
        with self._lock:
            if not topic or len(topic) > MAX_TOPIC_CHARS or not 0 <= index < len(self.topics):
                return False
            old_topic = self.topics[index]
            old_category = self.topic_categories.get(
                old_topic, DEFAULT_TOPIC_CATEGORY
            )
            new_category = str(category or "").strip()
            if new_category not in self.categories:
                new_category = old_category
            self.topics[index] = topic
            if old_topic != topic and old_topic not in self.topics:
                self.topic_categories.pop(old_topic, None)
            self.topic_categories[topic] = new_category
            if old_topic in self.selected_topics:
                selected_index = self.selected_topics.index(old_topic)
                self.selected_topics[selected_index] = topic
            self.current_index = 0
            return self.save()

    def delete(self, index: int) -> bool:
        with self._lock:
            if not 0 <= index < len(self.topics):
                return False
            removed = self.topics[index]
            del self.topics[index]
            if removed not in self.topics:
                self.topic_categories.pop(removed, None)
            if removed in self.selected_topics:
                self.selected_topics.remove(removed)
            if self.current_index >= len(self.topics):
                self.current_index = 0
            return self.save()

    def clear(self) -> bool:
        with self._lock:
            self.topics = []
            self.current_index = 0
            self.topic_categories = {}
            self.selected_topics = []
            return self.save()

    def reset(self) -> bool:
        with self._lock:
            self.topics = list(DEFAULT_TOPICS)
            self.current_index = 0
            self.topic_categories = {
                topic: DEFAULT_TOPIC_CATEGORY for topic in self.topics
            }
            self.selected_topics = []
            return self.save()

    def set_mode(self, mode: str) -> bool:
        if mode not in {"sequential", "random"}:
            return False
        with self._lock:
            self.send_mode = mode
            if mode == "sequential":
                self.current_index = 0
            return self.save()

    def set_no_repeat_seconds(self, seconds: int) -> bool:
        with self._lock:
            self.no_repeat_seconds = max(0, int(seconds))
            return self.save()

    def _prune_recent(self, now: float) -> set[str]:
        """只保留不重复时间窗内的记录，返回窗内已用过的话题名。"""
        cutoff = now - self.no_repeat_seconds
        self.recent_topics = [
            item for item in self.recent_topics if item["at"] >= cutoff
        ][-100:]
        return {item["topic"] for item in self.recent_topics}

    def get_next_topic(self) -> str | None:
        with self._lock:
            candidates_all = list(self.runtime_topics)
            if not candidates_all:
                return None
            now = time.time()
            if self.send_mode == "random":
                recent_names = self._prune_recent(now)
                candidates = [
                    t for t in candidates_all if t not in recent_names
                ]
                if not candidates:
                    # 本分类话题太少、全部都在不重复窗口内时，只能从本分类再抽。
                    candidates = list(candidates_all)
                topic = random.choice(candidates)
            else:
                index = self.current_index % len(candidates_all)
                topic = candidates_all[index]
                self.current_index = (index + 1) % len(candidates_all)
            self.recent_topics.append({"topic": topic, "at": now})
            self._prune_recent(now)
            self.save()
            return topic
