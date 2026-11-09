from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

import requests
import websockets

from .douyin_live.live_man import DouyinLiveWebFetcher


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)

CONNECTION_LOG = Path(__file__).resolve().parent.parent / "data" / "connection_debug.log"


def _log_connection(tag: str, text: str):
    """记录抖音采集连接事件，方便统计掉线和重连频率。"""
    try:
        CONNECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CONNECTION_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{tag}] {text}\n")
    except Exception:
        pass


class LiveRoomResolver:
    """把直播间分享链接或 LiveId 统一解析成抖音 webRid。"""

    @staticmethod
    def _find_web_rid(text: str) -> str:
        patterns = (
            r'webRid\\?"\s*:\s*\\?"(\d+)',
            r'"webRid"\s*:\s*"(\d+)"',
            r'web_rid\\?"\s*:\s*\\?"(\d+)',
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _find_room_id(text: str) -> str:
        patterns = (
            r'roomId\\?"\s*:\s*\\?"(\d+)',
            r'"roomId"\s*:\s*"(\d+)"',
            r'room_id\\?"\s*:\s*\\?"(\d+)',
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _room_id_to_web_rid(cls, room_id: str) -> str:
        url = f"https://webcast.amemv.com/douyin/webcast/reflow/{room_id}"
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            allow_redirects=True,
        )
        response.raise_for_status()
        web_rid = cls._find_web_rid(response.text)
        if web_rid:
            return web_rid
        raise ValueError("直播间链接里没有找到 webRid，请换一条分享链接")

    @classmethod
    def resolve(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("LiveId 不能为空")

        url_match = re.search(r"https?://[^\s]+", text)
        if url_match:
            url = url_match.group(0).rstrip(".,;)")
            live_match = re.search(r"live\.douyin\.com/(\d+)", url)
            if live_match:
                return live_match.group(1)

            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=20,
                allow_redirects=True,
            )
            response.raise_for_status()
            web_rid = cls._find_web_rid(response.text)
            if web_rid:
                return web_rid

            room_id = cls._find_room_id(response.text)
            if room_id:
                return cls._room_id_to_web_rid(room_id)

            final_match = re.search(r"live\.douyin\.com/(\d+)", response.url)
            if final_match:
                return final_match.group(1)
            raise ValueError("没有从分享链接里解析到直播间号")

        digits = re.sub(r"\D", "", text)
        if not digits:
            raise ValueError("LiveId 格式不正确")
        if len(digits) >= 18:
            return cls._room_id_to_web_rid(digits)
        return digits


class NativeDouyinFetcher(DouyinLiveWebFetcher):
    def __init__(self, live_id, on_event=None, on_status=None):
        super().__init__(live_id, on_event=on_event)
        self._on_status = on_status

    def _wsOnOpen(self, ws):
        super()._wsOnOpen(ws)
        if self._on_status:
            self._on_status("open")

    def _wsOnMessage(self, ws, message):
        # 任何一帧（弹幕、统计、ack）都证明连接还活着；
        # 不能只看“有没有人发弹幕”，否则安静直播间会被误判为掉线。
        if self._on_status:
            self._on_status("alive")
        super()._wsOnMessage(ws, message)

    def _wsOnError(self, ws, error):
        if self._on_status:
            self._on_status(f"error: {error}")
        super()._wsOnError(ws, error)

    def _wsOnClose(self, ws, *args):
        if self._on_status:
            self._on_status("closed")
        super()._wsOnClose(ws, *args)


class NativeBarrageService:
    """内置抖音弹幕采集器，同时提供兼容现有前端协议的本地 WebSocket 服务。"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8888):
        self.host = host
        self.port = port
        self._lock = threading.RLock()
        self._running = False
        self._live_input = ""
        self._web_rid = ""
        self._last_error = ""
        self._open_event = threading.Event()
        # 抖音推送活性检测：长时间没有消息说明连接已经半死，需要自动重连
        self._last_event_at = 0.0
        self._last_connect_at = 0.0
        self._last_reconnect_at = 0.0
        self._started_at = 0.0

        self._server_thread: threading.Thread | None = None
        self._server_loop: asyncio.AbstractEventLoop | None = None
        self._server_stop_future = None
        self._server_ready = threading.Event()
        self._clients = set()

        self._fetcher: NativeDouyinFetcher | None = None
        self._fetcher_thread: threading.Thread | None = None

    def status(self) -> dict:
        running = bool(
            self._running
            and self._fetcher_thread is not None
            and self._fetcher_thread.is_alive()
        )
        return {
            "exe": "内置抖音采集器",
            "process_running": running,
            "websocket_ready": self._server_ready.is_set(),
            "douyin_connected": self._open_event.is_set(),
            "last_event_age": (
                round(time.time() - self._last_event_at, 1)
                if self._last_event_at
                else None
            ),
            "live_room": self._web_rid,
            "last_error": self._last_error,
        }

    def _server_thread_main(self):
        loop = asyncio.new_event_loop()
        self._server_loop = loop
        asyncio.set_event_loop(loop)

        async def runner():
            try:
                async with websockets.serve(
                    self._handle_client,
                    self.host,
                    self.port,
                    ping_interval=None,
                    max_size=2 * 1024 * 1024,
                ):
                    self._server_stop_future = loop.create_future()
                    self._server_ready.set()
                    await self._server_stop_future
            except Exception as exc:
                self._last_error = f"本地弹幕服务启动失败: {exc}"
                self._server_ready.clear()

        try:
            loop.run_until_complete(runner())
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._server_loop = None
            self._server_stop_future = None
            self._server_ready.clear()

    async def _handle_client(self, websocket):
        self._clients.add(websocket)
        try:
            async for _ in websocket:
                pass
        except Exception:
            pass
        finally:
            self._clients.discard(websocket)

    def _start_server(self) -> bool:
        if self._server_ready.is_set():
            return True
        self._server_ready.clear()
        self._server_thread = threading.Thread(
            target=self._server_thread_main, name="douyin-native-server", daemon=True
        )
        self._server_thread.start()
        return self._server_ready.wait(6)

    def _stop_server(self):
        loop = self._server_loop
        future = self._server_stop_future
        if loop is not None and future is not None and not future.done():
            try:
                loop.call_soon_threadsafe(future.set_result, None)
            except Exception:
                pass
        if self._server_thread is not None:
            self._server_thread.join(timeout=4)
        self._server_thread = None
        self._server_ready.clear()

    def _broadcast(self, payload: dict):
        loop = self._server_loop
        if loop is None or not self._server_ready.is_set():
            return
        data = json.dumps(payload, ensure_ascii=False)

        async def send_all():
            for client in list(self._clients):
                try:
                    await client.send(data)
                except Exception:
                    self._clients.discard(client)

        try:
            asyncio.run_coroutine_threadsafe(send_all(), loop)
        except Exception:
            pass

    def _on_event(self, payload: dict):
        self._last_event_at = time.time()
        if isinstance(payload, dict) and "Type" in payload:
            self._broadcast(payload)

    def _on_status(self, status: str):
        if status == "open":
            self._open_event.set()
            self._last_connect_at = time.time()
            # 刚连上时先给一个宽限期，避免还没收到首条消息就被判超时
            self._last_event_at = max(self._last_event_at, time.time())
            _log_connection("douyin", f"open room={self._web_rid}")
        elif status == "alive":
            self._last_event_at = time.time()
        elif status == "closed":
            self._open_event.clear()
            _log_connection("douyin", "closed")
        elif status.startswith("error"):
            self._last_error = status
            _log_connection("douyin", status)

    def _fetcher_worker(self):
        failures = 0
        while self._running:
            try:
                self._fetcher = NativeDouyinFetcher(
                    self._web_rid,
                    on_event=self._on_event,
                    on_status=self._on_status,
                )
                self._fetcher.start()
                failures = 0
            except Exception as exc:
                failures += 1
                self._last_error = f"抖音弹幕连接失败: {exc}"
                _log_connection("douyin-fetcher", f"连接失败({failures}): {exc}")
            if not self._running:
                break
            # 停播或触发验证时不要每 4 秒猛重试，指数退避到最多 60 秒。
            delay = min(4 * (2 ** max(0, failures - 1)), 60) if failures else 4
            time.sleep(delay)

    def start(self, live_id: str = "") -> tuple[bool, str]:
        with self._lock:
            if self._running:
                return True, "内置采集器已在运行"
            if not self._start_server():
                return False, self._last_error or "本地弹幕服务启动失败，端口 8888 可能被占用"

        try:
            web_rid = LiveRoomResolver.resolve(live_id)
        except Exception as exc:
            self._stop_server()
            return False, f"解析直播间失败: {exc}"

        with self._lock:
            self._live_input = live_id
            self._web_rid = web_rid
            self._last_error = ""
            self._open_event.clear()
            self._last_event_at = 0.0
            self._last_connect_at = 0.0
            self._last_reconnect_at = 0.0
            self._started_at = time.time()
            self._running = True
            self._fetcher_thread = threading.Thread(
                target=self._fetcher_worker, name="douyin-native-fetcher", daemon=True
            )
            self._fetcher_thread.start()

        if self._open_event.wait(10):
            return True, f"内置采集已连接直播间 {web_rid}"
        return True, f"内置采集已启动，正在连接直播间 {web_rid}..."

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            self._running = False
            fetcher = self._fetcher
            self._fetcher = None
        if fetcher is not None:
            try:
                fetcher.stop()
            except Exception:
                pass
        if self._fetcher_thread is not None:
            self._fetcher_thread.join(timeout=4)
        self._fetcher_thread = None
        self._open_event.clear()
        self._stop_server()
        return True, "内置采集器已停止"

    def restart(self, live_id: str = "") -> tuple[bool, str]:
        self.stop()
        time.sleep(0.5)
        return self.start(live_id)
