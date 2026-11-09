from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import websockets

CONNECTION_LOG = Path(__file__).resolve().parent.parent / "data" / "connection_debug.log"


def _log_connection(tag: str, text: str):
    """把连接状态落盘，便于事后统计掉线频率和原因。"""
    try:
        CONNECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CONNECTION_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} [{tag}] {text}\n"
            )
    except Exception:
        pass


class WsMonitor:
    """连接内置弹幕服务的本地 WebSocket，并把消息标准化后回调给上层。"""

    URI = "ws://127.0.0.1:8888"

    def __init__(self, callback: Callable[[str, object], None], uri: str | None = None):
        self.callback = callback
        self.uri = uri or self.URI
        self.running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._connected = False
        self._last_rx_at = 0.0
        self._started_at = 0.0
        self._id_seq = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._thread_main, name="barrage-ws", daemon=True
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        return bool(
            self.running
            and self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def is_connected(self) -> bool:
        return bool(self.running and self._connected)

    @property
    def silent_seconds(self) -> float:
        if not self._last_rx_at:
            return 0.0 if not self._started_at else time.time() - self._started_at
        return time.time() - self._last_rx_at

    def stop(self):
        self.running = False
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_current_task)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._ws = None

    def _cancel_current_task(self):
        if self._ws is not None:
            try:
                asyncio.create_task(self._ws.close())
            except Exception:
                pass

    def _thread_main(self):
        # 连接协程异常退出时必须自动重启，不能把整个监控线程带走。
        while self.running:
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._connect_loop())
            except BaseException as exc:
                if self.running:
                    self._emit(
                        "error",
                        f"WebSocket 线程异常: {type(exc).__name__}: {exc}；1 秒后重启",
                    )
            finally:
                try:
                    if self._loop is not None:
                        self._loop.close()
                except Exception:
                    pass
                self._loop = None
            if self.running:
                time.sleep(1.0)

    async def _connect_loop(self):
        while self.running:
            try:
                async with websockets.connect(
                    self.uri,
                    # 本地服务是标准 WebSocket，用协议层 ping 保活；
                    # 长时间没有弹幕不等于连接断开，不能靠“无数据”重建。
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=1.0,
                    open_timeout=3.0,
                    max_size=2 * 1024 * 1024,
                ) as websocket:
                    self._ws = websocket
                    self._connected = True
                    self._emit("info", "WebSocket 连接成功，开始接收弹幕")
                    while self.running:
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            self._last_rx_at = time.time()
                            self.process_message(raw)
                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            # 本地弹幕服务的正常断开会自动重连，不该在
                            # 界面刷红色错误；真正连不上时下面的异常分支
                            # 仍会报 error。
                            self._emit("info", "WebSocket 连接已断开，自动重连中")
                            break
            except ConnectionRefusedError:
                self._emit("error", "无法连接内置弹幕服务，3 秒后重试")
                await asyncio.sleep(3)
            except Exception as exc:
                if self.running:
                    self._emit("error", f"WebSocket 连接错误: {exc}")
                    await asyncio.sleep(3)
            finally:
                self._connected = False
                self._ws = None

    def process_message(self, raw: str):
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "Type" not in payload:
                return
            type_id = int(payload.get("Type", 0))
            handlers = {
                1: self._member,
                2: self._social,
                3: self._chat,
                4: self._like,
                5: self._gift,
                6: self._share,
                7: self._room_user_seq,
                8: self._control,
                9: self._fansclub,
                10: self._room_stats,
            }
            handler = handlers.get(type_id)
            if handler:
                handler(payload)
        except (ValueError, TypeError, KeyError):
            return

    def _base(self, username: str, content: str, msg_type: str, **extra):
        # 毫秒时间戳在同一毫秒内的多条弹幕会撞 id，导致去重/移入已发送
        # 时误删别的消息；追加进程内自增序号保证唯一。
        self._id_seq = (self._id_seq + 1) % 1000
        item = {
            "id": int(time.time() * 1000) * 1000 + self._id_seq,
            "username": username or "未知用户",
            "content": content,
            "msg_type": msg_type,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "formatted_content": f"{username or '未知用户'}: {content}",
        }
        item.update(extra)
        return item

    @staticmethod
    def _nickname(data: dict) -> str:
        user = data.get("User") or {}
        return str(user.get("NickName") or user.get("Nickname") or "未知用户")

    def _member(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("member", self._base(
            self._nickname(data), str(data.get("Content") or "进入了直播间"), "member"
        ))

    def _social(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("social", self._base(
            self._nickname(data), str(data.get("Content") or "关注了主播"), "social"
        ))

    def _chat(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("message", self._base(
            self._nickname(data), str(data.get("Content") or ""), "message"
        ))

    def _like(self, payload: dict):
        data = payload.get("Data") or {}
        content = str(data.get("Content") or "点赞了")
        self._emit("like", self._base(
            self._nickname(data), content, "like",
            count=data.get("Count", 1), total=data.get("Total", 0),
        ))

    def _gift(self, payload: dict):
        data = payload.get("Data") or {}
        content = str(data.get("Content") or "送出了礼物")
        self._emit("gift", self._base(
            self._nickname(data), content, "gift",
            gift_name=str(data.get("GiftName") or "礼物"),
            gift_count=data.get("GiftCount", 1),
            gift_value=data.get("DiamondCount", 0),
        ))

    def _share(self, payload: dict):
        data = payload.get("Data") or {}
        content = str(data.get("Content") or "分享了直播间")
        if "分享了直播间到" in content:
            content = content.split("分享了直播间到", 1)[0] + "分享了直播间"
        self._emit("share", self._base(
            self._nickname(data), content, "share",
            share_platform=str(data.get("ShareTarget") or "未知平台"),
        ))

    def _fansclub(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("fansclub", self._base(
            self._nickname(data), str(data.get("Content") or "加入了粉丝团"), "fansclub",
            fansclub_level=data.get("Level", 0),
            fansclub_name=str(data.get("FansClubName") or "粉丝团"),
        ))

    def _room_user_seq(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("roomUserSeq", self._base(
            "系统", str(data.get("Content") or "直播间统计信息"), "roomUserSeq"
        ))

    def _control(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("control", self._base(
            "系统", str(data.get("Content") or "直播间状态变更"), "control"
        ))

    def _room_stats(self, payload: dict):
        data = payload.get("Data") or {}
        self._emit("roomStats", self._base(
            "系统", str(data.get("Content") or "直播间状态信息"), "roomStats"
        ))

    def _emit(self, msg_type: str, data):
        if msg_type in {"info", "error"}:
            _log_connection("barrage-ws", f"{msg_type}: {data}")
        try:
            self.callback(msg_type, data)
        except Exception:
            pass
