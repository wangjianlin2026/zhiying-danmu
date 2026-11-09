"""JSON 数据访问层。

所有持久化都集中在 data 目录。部分原版 JSON 带 UTF-8 BOM，
因此统一用 utf-8-sig 读取；写入采用临时文件 + os.replace，避免断电或
并发写入把文件截断。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
_LOCK = threading.RLock()


DEFAULTS: dict[str, Any] = {
    "topics.json": {
        "topics": [],
        "current_index": 0,
        "send_mode": "sequential",
        "auto_start": True,
    },
    "barrage_categories.json": {},
    "category_settings.json": {
        "category_monitor": True,
        "barrage_event_monitor": True,
        "barrage_types": {
            "message": True, "like": True, "social": True, "gift": True,
            "fansclub": True, "share": True, "member": True,
            "roomUserSeq": False, "control": False, "roomStats": False,
        },
        "like_dedup_mode": "once",
        "like_dedup_interval": "60",
        "normal_message_interval": "3.0",
        "priority_message_interval": "2.0",
        "keyboard_input_priority": True,
        "keyboard_input_interval": "0.5",
    },
    "pressure_menu_messages.json": {
        "messages": [], "interval": 60, "send_mode": "sequential",
        "user_cleared": False,
    },
    "connection_settings.json": {
        "host": "", "port": "8888", "mac": "",
        # 首次连上后锁定的设备地址；下次启动优先直连，失败再按 MAC 扫描
        "locked": False, "last_good_at": 0,
    },
    "collector_settings.json": {"last_live_input": "", "last_web_rid": ""},
    "ai_barrage_settings.json": {
        "enabled": True,
        "welcome_enabled": True,
        "answer_enabled": True,
        "read_aloud": True,
        "min_interval": 4.0,
        "welcome_min_interval": 30.0,
        "like_queue_cooldown": 30.0,
        "max_queue": 60,
        "conversation_mode": "manual",
        "auto_active": False,
    },
    "devices.json": {"devices": [], "current_index": 0},
    "filter_keywords.json": {"keywords": [], "enabled": True},
    "filter_states.json": {
        "enabled": True,
        "filters": {"duplicate": True, "spam": True, "inappropriate": True},
        "duplicate_window": 30,
        "spam_window": 10,
        "spam_limit": 3,
        "number_enabled": True,
        "number_window": 10,
    },
    "number_patterns.json": {"patterns": [], "enabled": True},
    "message_interval_settings.json": {
        "min_message_interval": "3.0", "priority_message_interval": "2.0",
    },
    "keyboard_settings.json": {
        "keyboard_input_priority": True, "keyboard_input_interval": "0.5",
    },
    "like_dedup_settings.json": {
        "enabled": True, "dedup_interval": 30, "max_likes_per_user": 3,
    },
    "priority_barrage_settings.json": {
        "enabled": True,
        "priority_keywords": [],
        "max_priority_items": 10,
        "send_mode": "smart",
        "types": {
            "message": True, "like": True, "social": True,
            "gift": True, "fansclub": True, "share": True,
            "member": True,
        },
        "levels": {
            "message": 1, "like": 4, "social": 2, "gift": 1,
            "fansclub": 3, "share": 4, "member": 2,
        },
        "priority_types": {
            "message": True, "like": True, "social": True,
            "gift": True, "fansclub": True, "share": True,
            "member": True,
        },
        "priority_levels": {
            "message": 1, "like": 4, "social": 2, "gift": 1,
            "fansclub": 3, "share": 4, "member": 2,
        },
    },
    "barrage_interval_settings.json": {
        "normal_interval": 10.0, "priority_interval": 2.0,
    },
    "priority_barrages.json": {
        "barrages": [], "max_items": 100, "last_update": None,
    },
    "received_barrages.json": {
        "barrages": [], "max_items": 100, "last_update": "",
    },
    "sent_barrages.json": {
        "barrages": [], "max_items": 1000, "last_update": "",
    },
}


def data_path(name: str) -> Path:
    return DATA_DIR / name


def _default_for(name: str) -> Any:
    return copy.deepcopy(DEFAULTS.get(name, {}))


def load_json(name: str, default: Any = None) -> Any:
    path = data_path(name)
    with _LOCK:
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                text = handle.read().strip()
            if not text:
                return copy.deepcopy(default if default is not None else _default_for(name))
            return json.loads(text)
        except (OSError, ValueError, TypeError):
            return copy.deepcopy(default if default is not None else _default_for(name))


def save_json(name: str, data: Any) -> bool:
    path = data_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        temp_path = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return True
        except Exception:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            return False


def ensure_data_files() -> None:
    with _LOCK:
        for name, default in DEFAULTS.items():
            if not data_path(name).exists():
                save_json(name, default)


def load_topics_data() -> dict:
    return load_json("topics.json", {"topics": [], "current_index": 0, "send_mode": "sequential"})


def save_topics_data(data: dict) -> bool:
    return save_json("topics.json", data)


def load_topics() -> list:
    return list(load_topics_data().get("topics", []))


def save_topics(topics: list, current_index: int = 0, send_mode: str = "sequential") -> bool:
    data = load_topics_data()
    data["topics"] = list(topics)
    data["current_index"] = max(0, int(current_index))
    data["send_mode"] = send_mode
    return save_topics_data(data)


def load_barrage_categories() -> dict:
    return load_json("barrage_categories.json", {})


def save_barrage_categories(data: dict) -> bool:
    return save_json("barrage_categories.json", data)


def load_category_settings() -> dict:
    return load_json("category_settings.json", _default_for("category_settings.json"))


def save_category_settings(data: dict) -> bool:
    return save_json("category_settings.json", data)


def load_pressure_menu_data() -> dict:
    return load_json("pressure_menu_messages.json", _default_for("pressure_menu_messages.json"))


def save_pressure_menu_data(data: dict) -> bool:
    return save_json("pressure_menu_messages.json", data)


def load_pressure_menu_messages() -> list:
    return list(load_pressure_menu_data().get("messages", []))


def save_pressure_menu_messages(
    messages: list, interval: int = 60, send_mode: str = "sequential"
) -> bool:
    data = load_pressure_menu_data()
    data["messages"] = list(messages)
    data["interval"] = max(1, int(interval))
    data["send_mode"] = send_mode
    return save_pressure_menu_data(data)


def load_connection_settings() -> dict:
    return load_json("connection_settings.json", _default_for("connection_settings.json"))


def save_connection_settings(data: dict) -> bool:
    return save_json("connection_settings.json", data)


def load_devices() -> dict:
    return load_json("devices.json", _default_for("devices.json"))


def save_devices(data: dict) -> bool:
    return save_json("devices.json", data)


def load_filter_keywords_data() -> dict:
    return load_json("filter_keywords.json", _default_for("filter_keywords.json"))


def load_filter_keywords() -> list:
    return list(load_filter_keywords_data().get("keywords", []))


def save_filter_keywords(keywords: list, enabled: bool = True) -> bool:
    data = load_filter_keywords_data()
    data["keywords"] = list(keywords)
    data["enabled"] = bool(enabled)
    return save_json("filter_keywords.json", data)


def load_filter_states() -> dict:
    return load_json("filter_states.json", _default_for("filter_states.json"))


def save_filter_states(data: dict) -> bool:
    return save_json("filter_states.json", data)


def load_number_patterns_data() -> dict:
    return load_json("number_patterns.json", _default_for("number_patterns.json"))


def load_number_patterns() -> list:
    return list(load_number_patterns_data().get("patterns", []))


def save_number_patterns(patterns: list, enabled: bool = True) -> bool:
    data = load_number_patterns_data()
    data["patterns"] = list(patterns)
    data["enabled"] = bool(enabled)
    return save_json("number_patterns.json", data)


def load_message_interval_settings() -> dict:
    return load_json("message_interval_settings.json", _default_for("message_interval_settings.json"))


def save_message_interval_settings(data: dict) -> bool:
    return save_json("message_interval_settings.json", data)


def load_keyboard_settings() -> dict:
    return load_json("keyboard_settings.json", _default_for("keyboard_settings.json"))


def save_keyboard_settings(data: dict) -> bool:
    return save_json("keyboard_settings.json", data)


def load_like_dedup_settings() -> dict:
    return load_json("like_dedup_settings.json", _default_for("like_dedup_settings.json"))


def save_like_dedup_settings(data: dict) -> bool:
    return save_json("like_dedup_settings.json", data)


def load_priority_settings() -> dict:
    return load_json("priority_barrage_settings.json", _default_for("priority_barrage_settings.json"))


def save_priority_settings(data: dict) -> bool:
    return save_json("priority_barrage_settings.json", data)


def load_barrage_interval_settings() -> dict:
    return load_json("barrage_interval_settings.json", _default_for("barrage_interval_settings.json"))


def save_barrage_interval_settings(data: dict) -> bool:
    return save_json("barrage_interval_settings.json", data)


def load_ai_barrage_settings() -> dict:
    return load_json(
        "ai_barrage_settings.json",
        _default_for("ai_barrage_settings.json"),
    )


def save_ai_barrage_settings(data: dict) -> bool:
    return save_json("ai_barrage_settings.json", data)


def load_barrages(name: str) -> list:
    data = load_json(name, {"barrages": []})
    if isinstance(data, list):
        return list(data)
    return list(data.get("barrages", []))


def save_barrages(name: str, barrages: list, max_items: int | None = None) -> bool:
    defaults = _default_for(name)
    # 这里不再额外读一遍旧文件：写盘时整体替换 barrages，其它字段用默认值。
    # 旧实现每次保存都要 load_json，弹幕一多就会卡 Qt 主线程。
    data = copy.deepcopy(defaults)
    if not isinstance(data, dict):
        data = {}
    data["barrages"] = list(barrages)
    if max_items is not None:
        data["max_items"] = int(max_items)
    return save_json(name, data)
