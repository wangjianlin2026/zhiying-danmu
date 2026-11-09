from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import ssl
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from services.audio_bridge import AudioBridge

try:
    import websockets
except ImportError:  # pragma: no cover - 运行环境缺依赖时给出明确状态
    websockets = None


DEFAULT_URL = "wss://api.tenclass.net/xiaozhi/v1/"
APP_DIR = Path(__file__).resolve().parent.parent
WS_DEBUG_LOG = APP_DIR / "data" / "ai_ws_debug.log"
COOLDOWN_PATH = APP_DIR / "data" / "ai_cooldown.json"

# 小智云端在长时间连续对话后会强制“休息一会儿”。检测到这些标志后，
# 本地主动暂停自动聆听和文本发送，避免每句话都把云端的冷却时间续上。
COOLDOWN_HINTS = ("连续聊了", "先停一停", "休息一会儿", "放下手机", "放下休息")
_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


class AiClient(QObject):
    """小智 WebSocket 文本客户端。

    所有网络操作都在独立线程的事件循环中执行，界面只接收 Qt 信号。
    """

    status_changed = pyqtSignal(str, bool)
    stt_received = pyqtSignal(str)
    reply_received = pyqtSignal(str)
    reply_finished = pyqtSignal()
    emotion_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    listening_state_changed = pyqtSignal(bool, str)
    cooldown_changed = pyqtSignal(bool, int, str)

    def __init__(self, config_path: Path, parent=None):
        super().__init__(parent)
        self.config_path = Path(config_path)
        self._thread = None
        self._loop = None
        self._ws = None
        self._session_id = ""
        self._connected = False
        self._running = False
        self._loop_ready = threading.Event()
        self._connect_lock = None
        self._receive_task = None
        self._connect_task = None
        self._reconnect_task = None
        self._active_tasks = set()
        self._last_sent_text = ""
        self._audio_bridge = None
        self._hardware_forward = None
        self._hardware_playing = None
        self._hardware_clear = None
        self._audio_capture = False
        self._listen_mode = "manual"
        self._auto_session = False
        # 弹幕/话题播报期间挂起自动聆听，避免小智一直抢着接直播间现场声音。
        self._auto_hold = False
        self._auto_resume_pending = False
        self._reply_busy = False
        self._reply_started_at = 0.0
        # 最后一次收到 TTS 事件的时间。服务端 tts stop 可能早于音频真正播完，
        # 用这个时间兜底判断“还在说话”，避免欢迎语/弹幕插进长句中间。
        self._last_tts_at = 0.0
        self._last_tts_text = ""
        self._finish_guard = False
        self._last_auto_stt = ("", 0.0)
        self._ws_debug = os.environ.get("XIAOZHI_WS_DEBUG", "") == "1"
        self._audio_send_queue = None
        self._audio_send_task = None
        self._auto_resume_task = None
        self._cooldown_until = 0.0
        self._cooldown_minutes = 0
        self._settings = {}
        self.reload_config()
        self._load_cooldown()

    # ---------- 配置 ----------
    def reload_config(self):
        data = {}
        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        network = (
            data.get("SYSTEM_OPTIONS", {}).get("NETWORK", {})
            if isinstance(data, dict)
            else {}
        )
        self._settings = {
            "url": str(network.get("WEBSOCKET_URL") or DEFAULT_URL),
            "token": str(network.get("WEBSOCKET_ACCESS_TOKEN") or ""),
            "device_id": str(
                (data.get("SYSTEM_OPTIONS") or {}).get("DEVICE_ID") or ""
            ),
            "client_id": str(
                (data.get("SYSTEM_OPTIONS") or {}).get("CLIENT_ID") or ""
            ),
        }

    # ---------- 连续对话保护 ----------
    @property
    def is_in_cooldown(self) -> bool:
        self._expire_cooldown_if_needed()
        return self._cooldown_until > time.time()

    @property
    def cooldown_remaining_minutes(self) -> int:
        self._expire_cooldown_if_needed()
        if self._cooldown_until <= time.time():
            return 0
        return max(1, int((self._cooldown_until - time.time()) / 60) + 1)

    def _load_cooldown(self):
        try:
            with COOLDOWN_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            until = float(data.get("until", 0) or 0)
        except (OSError, ValueError, TypeError):
            until = 0.0
        if until > time.time():
            self._cooldown_until = until
            self._cooldown_minutes = max(
                1, int((until - time.time()) / 60) + 1
            )

    def _save_cooldown(self, reason: str = ""):
        try:
            COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
            with COOLDOWN_PATH.open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "until": self._cooldown_until,
                        "minutes": self._cooldown_minutes,
                        "reason": str(reason or "")[:200],
                        "saved_at": time.time(),
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            pass

    def _expire_cooldown_if_needed(self):
        if not self._cooldown_until or time.time() < self._cooldown_until:
            return
        self._cooldown_until = 0.0
        self._cooldown_minutes = 0
        try:
            COOLDOWN_PATH.unlink()
        except OSError:
            pass
        self.cooldown_changed.emit(False, 0, "保护结束，可继续使用")
        self.status_changed.emit("小智连续对话保护已结束", True)

    @classmethod
    def _parse_cooldown_minutes(cls, text: str) -> int:
        text = str(text or "")
        match = re.search(r"(\d+)\s*分钟", text)
        if match:
            return max(5, min(180, int(match.group(1))))
        match = re.search(r"(\d+)\s*小时", text)
        if match:
            return max(5, min(180, int(match.group(1)) * 60))
        match = re.search(r"([零一二两三四五六七八九十]{1,3})\s*分钟", text)
        if match:
            return max(5, min(180, cls._cn_number(match.group(1))))
        match = re.search(r"([零一二两三四五六七八九十]{1,3})\s*小时", text)
        if match:
            return max(5, min(180, cls._cn_number(match.group(1)) * 60))
        # 云端有时只说“休息一会儿”，没给具体时长；按最常见的一轮冷却处理。
        return 50

    @staticmethod
    def _cn_number(text: str) -> int:
        text = str(text or "")
        if "十" in text:
            left, _, right = text.partition("十")
            tens = _CN_DIGITS.get(left, 1) if left else 1
            ones = _CN_DIGITS.get(right, 0) if right else 0
            return tens * 10 + ones
        value = 0
        for char in text:
            value = value * 10 + _CN_DIGITS.get(char, 0)
        return value or 50

    def _check_cooldown_text(self, text: str):
        if self.is_in_cooldown:
            return
        if not any(hint in text for hint in COOLDOWN_HINTS):
            return
        minutes = self._parse_cooldown_minutes(text)
        self._enter_cooldown(minutes, text)

    def _enter_cooldown(self, minutes: int, reason: str):
        minutes = max(5, min(180, int(minutes or 50)))
        self._cooldown_until = time.time() + minutes * 60
        self._cooldown_minutes = minutes
        self._save_cooldown(reason)
        self._auto_session = False
        self._audio_capture = False
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
        task = self._auto_resume_task
        if task is not None and not task.done():
            task.cancel()
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._cooldown_listen_stop_async(), loop
                )
            except Exception:
                pass
        self.status_changed.emit(
            f"小智连续对话保护：暂停约 {minutes} 分钟", False
        )
        self.listening_state_changed.emit(False, "连续对话保护中")
        self.cooldown_changed.emit(True, minutes, str(reason or ""))

    async def _cooldown_listen_stop_async(self):
        if self._ws is None or not self._connected:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {"session_id": self._session_id, "type": "listen", "state": "stop"}
                )
            )
        except Exception:
            pass

    # ---------- 生命周期 ----------
    @property
    def is_busy(self) -> bool:
        if self._reply_busy:
            bridge = self._audio_bridge
            now = time.time()
            if bridge is not None:
                last_activity = max(
                    self._reply_started_at,
                    bridge.last_audio_at,
                    self._last_tts_at,
                )
                if not bridge.is_playing and now - last_activity > 5.0:
                    # 服务端偶尔不发 tts stop；句间停顿不算结束，静默 5 秒才解除。
                    self._reply_busy = False
            elif self._reply_started_at and now - self._reply_started_at > 20.0:
                self._reply_busy = False
        return bool(self._reply_busy)

    def preheat_audio(self) -> bool:
        """提前初始化音频桥，避免第一次朗读时延迟或丢音频。"""
        loop = self._loop
        if self._audio_bridge is not None:
            return True
        if loop is None or not loop.is_running() or not self._connected:
            return False

        async def _task():
            return await self._ensure_audio_async()

        try:
            asyncio.run_coroutine_threadsafe(_task(), loop)
            return True
        except Exception:
            return False

    def set_hardware_forward(self, callback, is_playing=None, clear=None):
        """把云端 TTS 音频转推到小智硬件喇叭（None 表示恢复本机扬声器）。"""
        self._hardware_forward = callback
        self._hardware_playing = is_playing
        self._hardware_clear = clear
        bridge = self._audio_bridge
        if bridge is not None:
            bridge.set_forward_callback(callback, is_playing)

    def start(self):
        if self._running:
            return
        if websockets is None:
            self.error_occurred.emit("缺少 websockets 依赖，无法连接小智服务")
            return
        self._running = True
        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="xiaozhi-ai-client", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False
        loop = self._loop
        thread = self._thread
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._cancel_active_tasks)
            except Exception:
                pass
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(8.0)
        self._thread = None
        self._loop = None

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._connect_lock = asyncio.Lock()
        self._loop_ready.set()
        self._active_tasks = set()
        # 32 帧只有约 0.64 秒缓冲，上行网络稍有抖动就会丢词。
        # 128 帧约 2.56 秒，给重连/拥塞留出恢复时间。
        self._audio_send_queue = asyncio.Queue(maxsize=128)
        self._audio_send_task = loop.create_task(self._audio_send_loop())
        try:
            self._connect_task = loop.create_task(self._connect_async())
            loop.run_until_complete(self._connect_task)
            if self._running:
                self._reconnect_task = loop.create_task(self._reconnect_loop())
                loop.run_forever()
        except (asyncio.CancelledError, RuntimeError) as exc:
            if self._running and "Event loop stopped" not in str(exc):
                try:
                    self.error_occurred.emit(f"小智连接线程异常: {exc}")
                except RuntimeError:
                    pass
        except Exception as exc:
            if self._running:
                try:
                    self.error_occurred.emit(f"小智连接线程异常: {exc}")
                except RuntimeError:
                    pass
        finally:
            try:
                loop.run_until_complete(self._close_async())
            except Exception:
                pass
            self._connect_task = None
            self._reconnect_task = None
            loop.close()

    def _cancel_active_tasks(self):
        """在事件循环线程内取消连接、录音等未完成任务。"""
        tasks = set(self._active_tasks)
        tasks.add(self._connect_task)
        tasks.add(self._receive_task)
        tasks.add(self._reconnect_task)
        tasks.add(self._audio_send_task)
        tasks.add(self._auto_resume_task)
        for task in tasks:
            if task is None:
                continue
            if not task.done():
                task.cancel()

    async def _run_tracked(self, coro):
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
        try:
            return await coro
        finally:
            if task is not None:
                self._active_tasks.discard(task)

    # ---------- 连接 ----------
    def _header_kwargs(self, headers):
        params = inspect.signature(websockets.connect).parameters
        if "additional_headers" in params:
            return {"additional_headers": headers}
        return {"extra_headers": headers}

    async def _connect_async(self):
        if self._connected and self._ws is not None:
            return True
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self._connected and self._ws is not None:
                return True
            self.reload_config()
            url = self._settings.get("url") or DEFAULT_URL
            headers = {
                "Authorization": f"Bearer {self._settings.get('token', '')}",
                "Protocol-Version": "1",
                "Device-Id": self._settings.get("device_id", ""),
                "Client-Id": self._settings.get("client_id", ""),
            }
            self.status_changed.emit("正在连接小智服务...", False)
            try:
                connect_kwargs = {
                    "ping_interval": 15,
                    "ping_timeout": 30,
                    "close_timeout": 10,
                    "max_size": 10 * 1024 * 1024,
                    "compression": None,
                    **self._header_kwargs(headers),
                }
                if url.lower().startswith("wss://"):
                    connect_kwargs["ssl"] = ssl._create_unverified_context()
                self._ws = await websockets.connect(url, **connect_kwargs)
                await self._ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "version": 1,
                            "features": {"mcp": True},
                            "transport": "websocket",
                            "audio_params": {
                                "format": "opus",
                                "sample_rate": 16000,
                                "channels": 1,
                                "frame_duration": 20,
                            },
                        }
                    )
                )
                raw = await asyncio.wait_for(self._ws.recv(), timeout=12)
                hello = json.loads(raw) if isinstance(raw, str) else {}
                if hello.get("type") != "hello":
                    raise RuntimeError("服务端握手响应异常")
                self._session_id = str(hello.get("session_id") or "")
                self._connected = True
                self.status_changed.emit("已连接小智服务", True)
                self._receive_task = asyncio.create_task(self._receive_loop())
                return True
            except Exception as exc:
                self._connected = False
                self._ws = None
                self.status_changed.emit(f"连接失败: {self._friendly_error(exc)}", False)
                return False

    @staticmethod
    def _friendly_error(exc):
        text = str(exc).strip() or exc.__class__.__name__
        if "401" in text or "403" in text:
            return "认证失败，请检查 WebSocket Token"
        if "getaddrinfo" in text or "Name or service" in text:
            return "网络不可用或域名解析失败"
        return text[:120]

    async def _receive_loop(self):
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    if self._audio_bridge is not None:
                        try:
                            await self._audio_bridge.write_audio(raw)
                        except Exception:
                            pass
                    continue
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                # 单条消息处理失败不能当成连接断开，否则会触发不必要的重连，
                # 界面上就会反复出现“连接中...”。
                try:
                    self._handle_message(data)
                except Exception as exc:
                    self.error_occurred.emit(
                        f"处理小智消息异常: {self._friendly_error(exc)}"
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if self._running:
                self.error_occurred.emit(f"小智连接中断: {self._friendly_error(exc)}")
        finally:
            self._connected = False
            self._ws = None
            self._audio_capture = False
            if self._audio_bridge is not None:
                self._audio_bridge.capturing = False
            if self._running:
                self.status_changed.emit("连接已断开，正在自动重连...", False)

    async def _reconnect_loop(self):
        """断线后自动重连；自动对话会恢复开麦。"""
        delay = 2.0
        while self._running:
            await asyncio.sleep(1.0)
            if not self._running:
                return
            if self._connected and self._ws is not None:
                delay = 2.0
                continue
            self.status_changed.emit("正在自动重连小智服务...", False)
            try:
                ok = await self._connect_async()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                ok = False
                self.error_occurred.emit(f"自动重连失败: {self._friendly_error(exc)}")
            if ok:
                delay = 2.0
                if self._auto_session:
                    await self._resume_auto_listening_async()
            else:
                self.status_changed.emit(
                    f"连接已断开，{int(delay)} 秒后自动重连...", False
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 15.0)

    def _debug_ws(self, data):
        if not self._ws_debug:
            return
        try:
            extra = ""
            if str(data.get("type") or "") in {"alert", "error", "goodbye"}:
                raw = json.dumps(data, ensure_ascii=False)
                extra = f" raw={raw[:300]}"
            line = (
                f"{time.strftime('%H:%M:%S')} type={data.get('type')} "
                f"state={data.get('state')} "
                f"text={str(data.get('text') or '')[:80]}{extra}\n"
            )
            with WS_DEBUG_LOG.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            pass

    @staticmethod
    def _normalize(text):
        return "".join(ch for ch in str(text or "") if ch.isalnum())

    def _is_echo(self, text):
        """过滤自动对话时扬声器回声被麦克风再次收录。"""
        if time.time() - self._last_tts_at > 8.0:
            return False
        norm = self._normalize(text)
        if not norm:
            return False
        for ref in (self._last_sent_text, self._last_tts_text):
            ref_norm = self._normalize(ref)
            if ref_norm and (norm in ref_norm or ref_norm in norm):
                return True
        last = self._normalize(self._last_tts_text)
        if last:
            hits = sum(1 for ch in norm if ch in last)
            if hits / len(norm) >= 0.6:
                return True
        return False

    # 自动对话下，服务端 VAD 会把环境音/呼吸/回声识别成这些单字。
    # 每一个都触发一轮 LLM，听起来就是没完没了的“嗯嗯嗯嗯”。
    _AUTO_FILLER_WORDS = {
        "嗯", "嗯嗯", "恩", "恩恩", "啊", "啊啊", "哦", "哦哦",
        "呃", "呃呃", "唔", "唔唔", "对", "对对", "是", "是是",
        "好", "好好", "行", "行行", "没", "没有", "在",
    }
    _AUTO_WAKE_WORDS = ("小智", "小志", "小知")
    _AUTO_SHORT_ALLOW = {
        "你好", "您好", "在吗", "继续", "停下", "停", "等等",
        "等一下", "别说了", "小智", "小志",
    }

    def _should_ignore_auto_stt(self, text: str) -> bool:
        norm = self._normalize(text)
        if not norm:
            return True
        if any(word in norm for word in self._AUTO_WAKE_WORDS):
            self._last_auto_stt = (norm, time.time())
            return False
        if norm in self._AUTO_SHORT_ALLOW:
            self._last_auto_stt = (norm, time.time())
            return False
        now = time.time()
        last_norm, last_at = self._last_auto_stt
        # 纯语气词/应答词，不当作有效提问。
        if norm in self._AUTO_FILLER_WORDS:
            self._last_auto_stt = (norm, now)
            return True
        # 连续两轮都是 1~2 个字的短应答，直接静音，避免来回“嗯嗯”。
        if len(norm) <= 2 and now - last_at < 8.0 and len(last_norm) <= 2:
            self._last_auto_stt = (norm, now)
            return True
        self._last_auto_stt = (norm, now)
        return False

    def _schedule_reply_finished(self):
        """等本地音频队列播完再收尾，避免句间停顿被误判成回复结束。"""
        if self._finish_guard:
            return
        self._finish_guard = True
        started_at = self._reply_started_at

        async def _waiter():
            try:
                deadline = time.time() + 20.0
                while time.time() < deadline:
                    bridge = self._audio_bridge
                    if bridge is None or not getattr(bridge, "is_playing", False):
                        break
                    await asyncio.sleep(0.2)
                # is_playing 已包含重采样缓冲和声卡尾音余量，这里只留
                # 一个很短的播放回调余量，尽快把麦克风还给用户。
                await asyncio.sleep(0.10)
            finally:
                self._finish_guard = False
            self._finish_reply(started_at)

        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(_waiter(), loop)
                return
            except Exception:
                self._finish_guard = False
        self._finish_reply(started_at)

    def _finish_reply(self, started_at: float = 0.0):
        # 等待期间若已开始新一轮回复，就不要把新一轮误收尾。
        if started_at and self._reply_started_at and self._reply_started_at != started_at:
            return
        self._reply_busy = False
        self._reply_started_at = 0.0
        self._audio_capture = False
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
        self.reply_finished.emit()
        self.status_changed.emit("已连接小智服务", True)
        if self._auto_session:
            # 自动对话：一轮 TTS 播完后继续开麦，无需用户再次点击。
            self._schedule_auto_listening_resume(0.15)
        else:
            self.listening_state_changed.emit(False, "小智回复已结束")

    def _handle_message(self, data):
        self._debug_ws(data)
        msg_type = str(data.get("type") or "")
        if msg_type == "stt":
            text = str(data.get("text") or "").strip()
            if text and text != self._last_sent_text and not self._is_echo(text):
                if self._auto_session:
                    if self._auto_hold:
                        # 播报挂起期间麦克风已关，服务端可能仍回送缓冲的 STT；
                        # 这时不能把它当成人声，否则忙状态又会被挂住。
                        self._debug_ws({
                            "type": "stt_skipped",
                            "state": "auto_hold",
                            "text": text,
                        })
                        return
                    if self._should_ignore_auto_stt(text):
                        self._debug_ws({
                            "type": "stt_filtered",
                            "state": "auto_filler",
                            "text": text,
                        })
                        return
                    # 自动对话是服务端直接识别并回复，本地没有 send_text，
                    # 必须在识别到人声时就标记忙碌，否则弹幕会插进这段回复里。
                    self._reply_busy = True
                    self._reply_started_at = time.time()
                self.stt_received.emit(text)
        elif msg_type == "tts":
            state = str(data.get("state") or "")
            text = str(data.get("text") or "").strip()
            self._last_tts_at = time.time()
            if text and state == "sentence_start":
                self._last_tts_text = text
                self._check_cooldown_text(text)
                self.reply_received.emit(text)
            if state == "stop":
                # 服务端 tts stop 往往早于本地播放队列排空，延后到真正没声音再收尾。
                self._schedule_reply_finished()
            elif state == "start":
                self._reply_busy = True
                self._reply_started_at = time.time()
                self._audio_capture = False
                if self._auto_session:
                    # TTS 播放期间暂停上行，避免把扬声器声音再次送进模型；
                    # 不发出 listening=False，否则界面会误判自动对话已结束。
                    if self._audio_bridge is not None:
                        self._audio_bridge.capturing = False
                    self.status_changed.emit("小智正在回复...", True)
                else:
                    self.status_changed.emit("小智正在回复...", True)
        elif msg_type == "llm":
            emotion = str(data.get("emotion") or "").strip()
            if emotion:
                self.emotion_changed.emit(emotion)
        elif msg_type == "alert":
            if str(data.get("status") or "").upper() == "ERROR":
                self._reply_busy = False
                self._reply_started_at = 0.0
                message = str(data.get("message") or "小智服务返回错误")
                self.error_occurred.emit(f"小智服务: {message}")
        elif msg_type in {"error", "goodbye"}:
            self._reply_busy = False
            message = str(data.get("message") or data.get("reason") or msg_type)
            self.error_occurred.emit(f"小智服务: {message}")
            if msg_type == "goodbye" and self._ws is not None:
                asyncio.create_task(self._close_after_goodbye_async())

    async def _close_after_goodbye_async(self):
        """服务端说 goodbye 时主动收盘，让接收循环进入自动重连。"""
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            pass

    # ---------- 发送 ----------
    async def _ensure_audio_async(self):
        if self._audio_bridge is not None:
            return True
        try:
            self.status_changed.emit("正在初始化麦克风...", True)
            bridge = AudioBridge(self._queue_audio_threadsafe)
            await bridge.initialize()
            if self._hardware_forward is not None:
                bridge.set_forward_callback(
                    self._hardware_forward, self._hardware_playing
                )
            self._audio_bridge = bridge
            self.status_changed.emit("麦克风已就绪", True)
            return True
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            self.error_occurred.emit(f"音频初始化失败: {message[:140]}")
            return False

    def _queue_audio_threadsafe(self, data: bytes):
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._enqueue_audio, data)
            except Exception:
                pass

    def _enqueue_audio(self, data: bytes):
        if not self._audio_capture or self._audio_send_queue is None:
            return
        try:
            self._audio_send_queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                self._audio_send_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._audio_send_queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def _audio_send_loop(self):
        while self._running:
            try:
                data = await self._audio_send_queue.get()
            except asyncio.CancelledError:
                return
            if data is None:
                return
            if self._ws is None or not self._connected:
                continue
            try:
                await self._ws.send(data)
            except Exception:
                if self._running:
                    self.error_occurred.emit("语音发送中断，将自动重连")
                await asyncio.sleep(0.1)

    def start_voice_capture(self, mode: str = "manual"):
        if self.is_in_cooldown:
            self.status_changed.emit(
                f"小智连续对话保护中，剩余约 {self.cooldown_remaining_minutes} 分钟",
                False,
            )
            return False
        loop = self._loop
        if loop is None or not loop.is_running():
            self.start()
            self._loop_ready.wait(2.0)
            loop = self._loop
        if loop is None:
            return False
        asyncio.run_coroutine_threadsafe(
            self._run_tracked(self._start_voice_capture_async(mode)), loop
        )
        return True

    async def _start_voice_capture_async(self, mode: str = "manual"):
        if self.is_in_cooldown:
            return
        if not self._running:
            return
        if not await self._connect_async():
            self.error_occurred.emit("麦克风启动失败: 小智服务未连接")
            return
        if not self._running:
            return
        if not await self._ensure_audio_async():
            return
        if not self._running or self._ws is None or not self._connected:
            return
        clear_input = getattr(self._audio_bridge, "clear_input", None)
        if clear_input is not None:
            await clear_input()
        while True:
            try:
                self._audio_send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._audio_capture = True
        self._audio_bridge.capturing = True
        self._listen_mode = "auto" if mode == "auto" else "manual"
        self._auto_session = self._listen_mode == "auto"
        # 用户手动点了“开始聆听”，优先级最高，解除播报挂起。
        self._auto_hold = False
        self._auto_resume_pending = False
        await self._ws.send(
            json.dumps(
                {
                    "session_id": self._session_id,
                    "type": "listen",
                    "state": "start",
                    "mode": self._listen_mode,
                }
            )
        )
        if self._listen_mode == "auto":
            self.status_changed.emit("自动对话进行中...", True)
            self.listening_state_changed.emit(True, "正在自动聆听")
        else:
            self.status_changed.emit("正在聆听，松开结束...", True)
            self.listening_state_changed.emit(True, "正在按住说话")

    def stop_voice_capture(self):
        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        asyncio.run_coroutine_threadsafe(
            self._run_tracked(self._stop_voice_capture_async()), loop
        )
        return True

    def pause_listening_for_text(self) -> bool:
        """临时停麦以便发送文本，保留自动对话状态，TTS 结束后会自动续听。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        self._audio_capture = False
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
        asyncio.run_coroutine_threadsafe(
            self._pause_listening_for_text_async(), loop
        )
        return True

    async def _pause_listening_for_text_async(self):
        self._audio_capture = False
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
        if self._ws is None or not self._connected:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {"session_id": self._session_id, "type": "listen", "state": "stop"}
                )
            )
        except Exception as exc:
            self.error_occurred.emit(f"暂停聆听失败: {self._friendly_error(exc)}")

    async def _stop_voice_capture_async(self):
        self._audio_capture = False
        self._auto_session = False
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
        if self._ws is None or not self._connected:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {"session_id": self._session_id, "type": "listen", "state": "stop"}
                )
            )
            self.status_changed.emit("录音结束，等待识别...", True)
            self.listening_state_changed.emit(False, "录音结束")
        except Exception as exc:
            self.error_occurred.emit(f"结束录音失败: {self._friendly_error(exc)}")

    async def _resume_auto_listening_async(self):
        """自动对话中，在 TTS 结束后重新开启麦克风上行。"""
        if self._auto_hold:
            self._auto_resume_pending = True
            return
        if self.is_in_cooldown:
            self._auto_session = False
            return
        if not (
            self._running
            and self._auto_session
            and self._connected
            and self._ws is not None
            and self._audio_bridge is not None
        ):
            return
        try:
            # 只清输入侧积压，不能清输出缓冲，否则会截断正在收尾的 TTS。
            clear_input = getattr(self._audio_bridge, "clear_input", None)
            if clear_input is not None:
                await clear_input()
            if self._audio_send_queue is not None:
                while True:
                    try:
                        self._audio_send_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            self._audio_capture = True
            self._audio_bridge.capturing = True
            await self._ws.send(
                json.dumps(
                    {
                        "session_id": self._session_id,
                        "type": "listen",
                        "state": "start",
                        "mode": "auto",
                    }
                )
            )
            self.status_changed.emit("自动对话进行中...", True)
            self.listening_state_changed.emit(True, "正在自动聆听")
        except Exception as exc:
            self._audio_capture = False
            if self._audio_bridge is not None:
                self._audio_bridge.capturing = False
            self.error_occurred.emit(f"自动对话续听失败: {self._friendly_error(exc)}")

    def _schedule_auto_listening_resume(self, delay: float = 0.15):
        """合并多次续听请求，避免打断/tts stop 同时触发重复开麦。"""
        if self._auto_hold:
            self._auto_resume_pending = True
            return
        if self.is_in_cooldown:
            self._auto_session = False
            return
        if not self._auto_session or not self._running:
            return
        previous = self._auto_resume_task
        if previous is not None and not previous.done():
            previous.cancel()

        async def _delayed_resume():
            try:
                await asyncio.sleep(delay)
                if self._auto_session:
                    await self._resume_auto_listening_async()
            except asyncio.CancelledError:
                return

        # release_auto_listening 可能从 Qt 定时器线程调用，此时当前线程
        # 没有 running loop；必须把任务投递回音频客户端的 asyncio 线程。
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is not None and running_loop.is_running():
            self._auto_resume_task = running_loop.create_task(_delayed_resume())
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                self._auto_resume_task = asyncio.run_coroutine_threadsafe(
                    _delayed_resume(), loop
                )
                return
            except Exception:
                pass
        try:
            self._auto_resume_task = asyncio.create_task(_delayed_resume())
        except RuntimeError:
            self._auto_resume_task = None

    def hold_auto_listening(self):
        """播报期间暂停自动续听，防止小智把现场人声当成提问一直回复。"""
        self._auto_hold = True
        self._auto_resume_pending = False
        self._audio_capture = False
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
        previous = self._auto_resume_task
        if previous is not None and not previous.done():
            previous.cancel()

    def release_auto_listening(self):
        """播报队列空闲后恢复自动对话收听的正常节奏。"""
        if not self._auto_hold:
            return
        if self.is_busy:
            # 话题/欢迎语还在播，等它真正结束再开麦，避免抢音。
            return
        self._auto_hold = False
        if self._auto_session and self._running:
            self._auto_resume_pending = False
            self._schedule_auto_listening_resume(0.15)

    def preempt_auto_for_broadcast(self):
        """弹幕/定时话题要出声时，结束自动对话并暂时不再自动开麦。"""
        self._reply_busy = False
        self._reply_started_at = 0.0
        self._finish_guard = False
        self.hold_auto_listening()
        try:
            self.abort()
        except Exception:
            pass

    def send_text(self, text: str):
        text = str(text or "").strip()
        if not text:
            return False
        if self.is_in_cooldown:
            self.status_changed.emit(
                f"小智连续对话保护中，剩余约 {self.cooldown_remaining_minutes} 分钟",
                False,
            )
            return False
        # 先在调用线程里占住“回复中”位置，避免话题/弹幕/逼单在
        # WebSocket 任务真正开始前同时挤进来。
        if self._reply_busy:
            return False
        # 小智协议的 detect 通道只接受唤醒词级短句，实测超过约 16 字会被拒绝。
        if len(text) > 15:
            text = text[:15]
        loop = self._loop
        if loop is None or not loop.is_running():
            self.start()
            self._loop_ready.wait(2.0)
            loop = self._loop
            if loop is None:
                return False
        self._last_sent_text = text
        self._debug_ws({"type": "send_text", "text": text})
        started_at = time.time()
        self._reply_busy = True
        self._reply_started_at = started_at

        def _rollback():
            if self._reply_started_at == started_at:
                self._reply_busy = False
                self._reply_started_at = 0.0

        async def _task():
            try:
                if not await self._connect_async():
                    _rollback()
                    return False
                # 文本对话也要初始化音频桥，否则服务端返回的 TTS 音频会被丢掉。
                if self._audio_bridge is None:
                    await self._ensure_audio_async()
                if self._hardware_clear is not None:
                    try:
                        self._hardware_clear()
                    except Exception:
                        pass
                payload = {
                    "session_id": self._session_id,
                    "type": "listen",
                    "state": "detect",
                    "text": text,
                }
                await self._ws.send(json.dumps(payload, ensure_ascii=False))
                self.status_changed.emit("已发送，等待小智回复...", True)
                return True
            except Exception:
                _rollback()
                raise

        try:
            future = asyncio.run_coroutine_threadsafe(_task(), loop)
        except Exception as exc:
            _rollback()
            self.error_occurred.emit(f"发送失败: {self._friendly_error(exc)}")
            return False

        def _done(result):
            try:
                ok = bool(result.result())
            except Exception as exc:
                _rollback()
                self.error_occurred.emit(f"发送失败: {self._friendly_error(exc)}")
                return
            if not ok:
                _rollback()
                self.error_occurred.emit("发送失败: 小智服务未连接")

        future.add_done_callback(_done)
        return True

    def abort(self):
        loop = self._loop
        if loop is None or not loop.is_running() or not self._connected:
            return False

        async def _task():
            was_auto = self._auto_session
            self._audio_capture = False
            if self._audio_bridge is not None:
                self._audio_bridge.capturing = False
                await self._audio_bridge.clear()
            await self._ws.send(
                json.dumps({"session_id": self._session_id, "type": "abort"})
            )
            self.status_changed.emit("已打断小智回复", True)
            if was_auto and not self.is_in_cooldown:
                self._auto_session = True
                self.status_changed.emit("已打断，正在恢复自动聆听...", True)
                self._schedule_auto_listening_resume(0.5)
            else:
                self._auto_session = False

        try:
            asyncio.run_coroutine_threadsafe(self._run_tracked(_task()), loop)
            return True
        except Exception as exc:
            self.error_occurred.emit(f"打断失败: {exc}")
            return False

    async def _close_async(self):
        self._audio_capture = False
        self._auto_session = False
        current = asyncio.current_task()
        tasks = set(self._active_tasks)
        tasks.update(
            task for task in (
                self._connect_task,
                self._receive_task,
                self._reconnect_task,
                self._audio_send_task,
                self._auto_resume_task,
            )
            if task is not None
        )
        pending = [
            task for task in tasks
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._receive_task = None
        self._audio_send_task = None
        if self._audio_bridge is not None:
            self._audio_bridge.capturing = False
            try:
                await self._audio_bridge.close()
            except Exception:
                pass
            self._audio_bridge = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._connected = False
        # websockets 内部还会创建 keepalive 等任务；关闭连接后再统一取消并
        # gather 一次，避免事件循环关闭时出现 "Task was destroyed" 警告。
        await self._drain_pending_tasks()

    async def _drain_pending_tasks(self):
        current = asyncio.current_task()
        pending = [
            task for task in asyncio.all_tasks()
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
