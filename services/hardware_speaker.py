"""小智硬件控制通道（UDP 8888）。

硬件模式下不再由电脑合成任何语音。电脑只把弹幕文本发给硬件，
由硬件自己连接小智云端、自己生成小智语音并从硬件喇叭播放。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

# 实测（2026-11-08 串口日志）：14 字正常，15 字云端报
# "Detect is only for wake words, do not send long texts"，设备屏幕红字，
# 随后整轮 TTS 作废、软件判失败并唤醒音频，表现为“每隔几分钟重启一次”。
# 因此留 1 字余量，整条文本（含“请说：”和标点）上限收到 13 字。
MAX_CHARS = 13
# 设备喇叭功放上电比音频流稍慢，首字容易被切掉。发送前加一个
# TTS 不发音的逗号停顿，既不改变语义，也给功放留出启动时间。
TTS_LEAD_PAD = "，"


class HardwareSpeaker:
    """通过 UDP 8888 控制小智硬件；语音完全由硬件自己生成。"""

    def __init__(self, host: str = "", port: int = 8888,
                 voice: str = "zh-CN-XiaoxiaoNeural",
                 log: Callable[[str], None] | None = None):
        self.host = host
        self.port = int(port)
        self.voice = voice
        self.log = log or (lambda _t: None)
        self._enabled = False
        self._message_seq = 0
        # 设备朗读时 UDP 状态轮询会暂时不应答；主程序据此避免误判掉线。
        self.last_send_ok_at = 0.0
        # 最近一次文本发送的结果，供界面给出“到底有没有送出去”的真实反馈。
        self.last_ack_at = 0.0
        self.last_error = ""
        # 最近一次等 TTS 激活失败时的现场信息，便于日志定位“有字无声”。
        self.last_active_detail = ""
        # 话题发送和弹幕欢迎可能来自不同线程；必须共用同一把发送锁，
        # 否则两条 UDP 文本会同时进设备，触发连续 Abort / 屏幕 ERROR。
        self._tx_lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_target(self, host: str, port: int | None = None) -> None:
        self.host = host or self.host
        if port:
            self.port = int(port)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled and not self.host:
            self.log("⚠️ 硬件语音已开启，但还没填设备 IP")
            return
        self._enabled = enabled
        self.log(f"🔊 语音通道: {'硬件（小智本体发声）' if enabled else '软件'}")

    def send_message(self, text: str, wait_ack: bool = True) -> bool:
        """把一条文本发给硬件，让硬件自己向小智云端发起语音回复。

        这是硬件模式唯一的发声入口：电脑不合成、不播放、不转发音频。
        wait_ack=True 时等设备回包并重试；设备重启或云端连接卡死时
        往往不回包，必须让调用方知道“这条其实没发出去”，不能日志报
        成功、实际没声音。
        """
        with self._tx_lock:
            text = (text or "").strip()
            if not text:
                return False
            if not self._enabled:
                self.log("⚠️ 硬件通道未启用，文本没有发送")
                return False
            if not self.host:
                self.log("⚠️ 硬件 IP 未配置，文本没有发送")
                return False
            original_len = len(text)
            text = self._with_tts_lead_pad(text)
            if original_len > MAX_CHARS:
                self.log(f"⚠️ 文本超过 {MAX_CHARS} 字，已截断后发送")

            attempts = 3 if wait_ack else 1
            last_error = ""
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if wait_ack:
                    sock.settimeout(1.5)
                for attempt in range(1, attempts + 1):
                    self._message_seq = (self._message_seq + 1) & 0x7FFFFFFF
                    payload = json.dumps(
                        {
                            "type": "message",
                            "text": text,
                            "id": self._message_seq,
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    try:
                        sock.sendto(payload, (self.host, self.port))
                        if not wait_ack:
                            self.last_send_ok_at = time.time()
                            self.last_error = ""
                            self.log(f"📨 设备已收到指令：{text[:24]}")
                            return True
                        data, _addr = sock.recvfrom(4096)
                        response = json.loads(data.decode("utf-8", "replace"))
                    except socket.timeout:
                        last_error = "设备未回 ACK"
                        if attempt < attempts:
                            time.sleep(0.6)
                        continue
                    except (OSError, ValueError, UnicodeDecodeError) as exc:
                        last_error = f"回包异常: {exc}"
                        if attempt < attempts:
                            time.sleep(0.6)
                        continue
                    ok = isinstance(response, dict) and (
                        response.get("ack") is True
                        or response.get("ok") is True
                        or str(response.get("type", "")).lower()
                        in {"ack", "pong"}
                    )
                    if ok:
                        self.last_ack_at = time.time()
                        self.last_send_ok_at = self.last_ack_at
                        self.last_error = ""
                        self.log(f"📨 设备已回 ACK：{text[:24]}")
                        return True
                    last_error = f"设备拒绝: {str(response)[:80]}"
                    if attempt < attempts:
                        time.sleep(0.6)
            finally:
                sock.close()
            self.last_error = last_error or "发送失败"
            self.log(f"⚠️ 硬件文本发送失败（{self.last_error}）：{text[:24]}")
            return False

    @staticmethod
    def _with_tts_lead_pad(text: str) -> str:
        """在正文前加一个不发音的短停顿，避免 TTS 首个音节被功放吃掉。"""
        body = str(text or "").strip()
        if not body:
            return ""
        prefix = ""
        for candidate in ("请说：", "请说:"):
            if body.startswith(candidate):
                prefix, body = candidate, body[len(candidate):]
                break
        # 重试时 text 可能已经带过前导停顿，避免叠成两个逗号。
        body = body.lstrip("，,")
        if not body:
            return prefix
        # 逗号只是防吞字的优化，放不下就优先保住 13 字上限。
        no_pad_room = max(0, MAX_CHARS - len(prefix))
        if len(body) < no_pad_room:
            room = no_pad_room - len(TTS_LEAD_PAD)
            return f"{prefix}{TTS_LEAD_PAD}{body[:room]}"
        return f"{prefix}{body[:no_pad_room]}"

    def _request_status(self, timeout: float = 1.0) -> dict | None:
        payload = json.dumps(
            {"type": "status", "timestamp": int(time.time() * 1000)}
        ).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(payload, (self.host, self.port))
            data, _addr = sock.recvfrom(4096)
            response = json.loads(data.decode("utf-8", "replace"))
            return response if isinstance(response, dict) else None
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        finally:
            sock.close()

    def request_status(self, timeout: float = 1.0) -> dict | None:
        """公开的状态查询入口，给统一调度器轮询播放进度用。"""
        return self._request_status(timeout)

    def wait_until_active(self, timeout: float = 4.0) -> bool:
        """确认设备收到文本后真的收到并播放本轮 TTS。

        设备 UDP 控制线程活着、但云端连接卡死时，会 ACK 文本却一直
        停在 idle；也可能状态已经是 speaking，但一个 TTS 音频包都没收到。
        新固件会在 status 里汇报本轮 TTS 音频包数，必须大于 0 才算成功。
        老固件没有该字段时退化为 connecting/speaking 判断。
        """
        self.last_active_detail = ""
        deadline = time.monotonic() + max(1.0, timeout)
        last_state = ""
        last_audio_rx = None
        saw_active = False
        while time.monotonic() < deadline:
            response = self._request_status(0.8)
            if not isinstance(response, dict):
                continue
            state = str(response.get("state", "") or "").lower()
            last_state = state or last_state
            has_audio_counter = "audio_utterance_rx" in response
            utterance_rx = None
            if has_audio_counter:
                try:
                    utterance_rx = int(response.get("audio_utterance_rx") or 0)
                except (TypeError, ValueError):
                    utterance_rx = 0
                last_audio_rx = utterance_rx
                if utterance_rx > 0:
                    self.last_active_detail = (
                        f"state={state or 'unknown'}, "
                        f"audio_utterance_rx={utterance_rx}"
                    )
                    return True
            if state in {"connecting", "speaking"}:
                saw_active = True
                # 老固件没有音频包计数，只能按状态判断。
                if not has_audio_counter:
                    self.last_active_detail = f"state={state}（旧固件无音频计数）"
                    return True
            time.sleep(0.25)
        self.last_active_detail = (
            f"state={last_state or '无回包'}, "
            f"audio_utterance_rx={last_audio_rx if last_audio_rx is not None else 'n/a'}, "
            f"saw_active={saw_active}"
        )
        return False

    def wait_until_finished(self, timeout: float = 30.0) -> bool:
        """等本轮朗读真正结束，避免下一条在上一条还在播时插进去。

        设备状态在 TTS 分句之间可能短暂回到 idle，因此要求连续稳定
        1.5 秒处于 idle/listening 才算结束。若中途设备 activating/
        booting/error，视为本轮已被设备接收，不再重发。
        """
        deadline = time.monotonic() + max(2.0, timeout)
        saw_active = False
        stable_since = 0.0
        while time.monotonic() < deadline:
            response = self._request_status(0.8)
            if not isinstance(response, dict):
                time.sleep(0.25)
                continue
            state = str(response.get("state", "") or "").lower()
            if state in {"speaking", "connecting"}:
                saw_active = True
                stable_since = 0.0
            elif state in {"idle", "listening"}:
                if saw_active:
                    if stable_since <= 0:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 1.5:
                        self.last_active_detail = (
                            f"播放结束 state={state}, "
                            f"elapsed={time.monotonic() - (deadline - max(2.0, timeout)):.1f}s"
                        )
                        return True
            elif state in {"activating", "booting", "error"}:
                self.last_active_detail = f"设备状态 {state}，本轮不再重发"
                return True
            time.sleep(0.25)
        if saw_active:
            # 超时但确实开口过：按已接收处理，绝不重发造成二次打断。
            self.last_active_detail = "朗读超时未回 idle，按已接收处理"
            return True
        return False

    def recover_audio(self, timeout: float = 0.8) -> bool:
        """设备 ACK 了却没开口时，唤醒一次音频链路再重发。

        实测设备偶发停在 idle：UDP 控制线程正常、文本也回 ACK，但云端
        TTS 没有启动。先发 audio_warmup + wake，再重发同一条文本，通常
        可以当场恢复，不必给设备断电。
        """
        if not self.host:
            return False
        payloads = (
            {
                "type": "audio_warmup",
                "silent": True,
                "timestamp": int(time.time() * 1000),
            },
            {
                "type": "wake",
                "action": "audio_init",
                "timestamp": int(time.time() * 1000),
            },
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            for payload in payloads:
                try:
                    sock.sendto(
                        json.dumps(payload).encode("utf-8"),
                        (self.host, self.port),
                    )
                    sock.recvfrom(4096)
                except (OSError, ValueError):
                    continue
            return True
        finally:
            sock.close()

    def send_and_confirm(self, text: str, timeout: float = 12.0) -> bool:
        """发送文本并确认设备真的开口；卡住时自动唤醒并重试一次。

        云端首包 TTS 有时要十几秒才到，原来 6 秒就重发会把设备打乱。
        这里先给足首个朗读窗口，只有确实没开口才唤醒重试。
        """
        with self._tx_lock:
            if not self.send_message(text, wait_ack=True):
                return False
            if not self.wait_until_active(timeout=timeout):
                self.log("🔄 设备已回 ACK 但没开口，自动唤醒音频后重试一次")
                self.recover_audio()
                time.sleep(0.8)
                if not self.send_message(text, wait_ack=True):
                    return False
                if not self.wait_until_active(timeout=timeout):
                    return False
            return self.wait_until_finished(timeout=30.0)

    def stop(self) -> None:
        """硬件模式下没有后台线程/队列，关闭时只需停用发送。"""
        self._enabled = False
