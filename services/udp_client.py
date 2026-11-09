from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any


class UdpClient:
    """线程安全的 ESP32 UDP 客户端。

    原版状态轮询与弹幕发送共用一个 socket，容易互相抢响应；这里用
    request_lock 串行化请求/响应，用 peer_lock 防止关闭时发送。
    """

    def __init__(self, host: str, port: int = 8888, timeout: float = 1.5):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(self.timeout)
        self._request_lock = threading.RLock()
        self._peer_lock = threading.RLock()
        self._closed = False
        self._cache: dict[str, float] = {}
        self._cache_cleanup_at = time.time()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def send_message(self, message_type: str, **kwargs) -> dict | None:
        with self._request_lock:
            with self._peer_lock:
                if self._closed:
                    return None
                message = {
                    "type": message_type,
                    "timestamp": int(time.time() * 1000),
                    **kwargs,
                }
                data = json.dumps(message, ensure_ascii=False).encode("utf-8")
                key = f"{message_type}:{kwargs.get('text', '')}:{message['timestamp']}"
                now = time.time()
                if now - self._cache_cleanup_at > 30:
                    self._cache = {
                        k: v for k, v in self._cache.items() if now - v <= 30
                    }
                    self._cache_cleanup_at = now
                try:
                    self._sock.sendto(data, (self.host, self.port))
                    response_data, _addr = self._sock.recvfrom(4096)
                    response = json.loads(response_data.decode("utf-8"))
                    if isinstance(response, dict):
                        return response
                    return None
                except socket.timeout:
                    return None
                except OSError:
                    return None
                except (ValueError, UnicodeDecodeError):
                    return None
                finally:
                    self._cache[key] = now

    def ping(self) -> dict | None:
        return self.send_message("ping")

    def get_status(self) -> dict | None:
        return self.send_message("status")

    def send_text(self, text: str) -> dict | None:
        return self.send_message("message", text=str(text))

    def send_audio_warmup(self, silent: bool = True) -> dict | None:
        return self.send_message("audio_warmup", silent=bool(silent))

    def wake_audio(self) -> dict | None:
        return self.send_message("wake", action="audio_init")

    def close(self):
        with self._peer_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._sock.close()
            except OSError:
                pass


def response_ok(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    return bool(
        response.get("ack") is True
        or response.get("ok") is True
        or str(response.get("type", "")).lower() in {"pong", "ack"}
        or response.get("status") in {"ok", "sent", "queued", "started"}
    )
