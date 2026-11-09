from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent

# 小智硬件 UDP 音频固定为 24kHz / 60ms 单声道 Opus。
SAMPLE_RATE = 24000
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


def _resolve_xiaozhi_home():
    configured = os.environ.get("XIAOZHI_HOME")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            APP_DIR.parent / "小智-1.0.0-x86_64",
            APP_DIR.parent / "旧版与资料" / "小智-1.0.0-x86_64",
            APP_DIR / "小智-1.0.0-x86_64",
        ]
    )
    for candidate in candidates:
        candidate = candidate.expanduser()
        if (candidate / "config" / "config.json").is_file() or (
            candidate / "src"
        ).is_dir():
            return candidate
    return candidates[0].expanduser() if candidates else APP_DIR


XIAOZHI_HOME = _resolve_xiaozhi_home()


def _bootstrap_opus():
    """让主程序复用小智自带的 opuslib/soxr 和 opus.dll。"""
    root = str(XIAOZHI_HOME)
    site_packages = XIAOZHI_HOME / ".venv" / "Lib" / "site-packages"
    opus_dir = XIAOZHI_HOME / "libs" / "libopus" / "win" / "x64"

    if not site_packages.exists():
        raise RuntimeError(f"找不到小智音频依赖目录: {site_packages}")
    if not (opus_dir / "opus.dll").exists():
        raise RuntimeError(f"找不到 Opus 动态库: {opus_dir / 'opus.dll'}")

    for path in (str(site_packages), root):
        if path not in sys.path:
            sys.path.insert(0, path)

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(opus_dir))
    os.environ["PATH"] = str(opus_dir) + os.pathsep + os.environ.get("PATH", "")

    original_find_library = ctypes.util.find_library

    def _find_library(name):
        if name == "opus":
            return str(opus_dir / "opus.dll")
        return original_find_library(name)

    ctypes.util.find_library = _find_library
    ctypes.CDLL(str(opus_dir / "opus.dll"))


class AudioBridge:
    """小智音频编解码桥。

    复用原版小智 AudioCodec：麦克风 -> 16k Opus -> WebSocket，
    WebSocket Opus -> 24k PCM -> 扬声器。
    """

    def __init__(self, on_encoded):
        self._on_encoded = on_encoded
        self._codec = None
        self.capturing = False
        self._last_audio_at = 0.0
        # 硬件外送模式：小智云端 TTS 的 Opus 在本地解码成 PCM，
        # 再按硬件需要的 24kHz/60ms 重新编码，通过回调推给设备喇叭。
        self._forward_callback = None
        self._forward_playing = None
        self._fwd_decoder = None
        self._fwd_encoder = None
        self._fwd_source_rate = 24000
        self._fwd_source_frame = 1440
        self._fwd_resampler = None
        self._fwd_pcm = bytearray()
        self._fwd_last_at = 0.0

    @property
    def available(self) -> bool:
        return self._codec is not None

    def set_forward_callback(self, callback, is_playing=None):
        """设置硬件外送回调。

        callback(packet: bytes) 接收已转码好的 24kHz/60ms Opus 包；
        is_playing() 用于告诉小智客户端硬件端还有多少音频没播完。
        """
        self._forward_callback = callback
        self._forward_playing = is_playing
        if callback is None:
            self._fwd_pcm.clear()

    async def initialize(self):
        if self._codec is not None:
            return True
        _bootstrap_opus()
        from src.audio_codecs.audio_codec import AudioCodec

        codec = AudioCodec()
        await codec.initialize()
        codec.set_encoded_audio_callback(self._handle_encoded)
        self._codec = codec
        return True

    def _handle_encoded(self, data: bytes):
        if self.capturing and data:
            self._on_encoded(data)

    async def write_audio(self, data: bytes):
        if not data:
            return
        self._last_audio_at = time.time()
        if self._forward_callback is not None:
            try:
                self._forward_opus(data)
            except Exception:
                # 单帧转码失败不能影响整条小智连接
                pass
            return
        if self._codec is not None:
            await self._codec.write_audio(data)

    def _init_forward_codecs(self):
        if self._fwd_decoder is not None:
            return
        import opuslib
        import soxr
        from src.constants.constants import AudioConfig

        source_rate = int(AudioConfig.OUTPUT_SAMPLE_RATE)
        frame_ms = int(getattr(AudioConfig, "FRAME_DURATION", 60) or 60)
        self._fwd_source_rate = source_rate
        self._fwd_source_frame = max(1, int(source_rate * frame_ms / 1000))
        self._fwd_decoder = opuslib.Decoder(source_rate, AudioConfig.CHANNELS)
        self._fwd_encoder = opuslib.Encoder(
            SAMPLE_RATE, AudioConfig.CHANNELS, opuslib.APPLICATION_AUDIO
        )
        try:
            self._fwd_encoder.bitrate = 32000
        except Exception:
            pass
        if source_rate != SAMPLE_RATE:
            self._fwd_resampler = soxr.ResampleStream(
                source_rate, SAMPLE_RATE, AudioConfig.CHANNELS,
                dtype="int16", quality="QQ",
            )

    def _forward_opus(self, data: bytes):
        """云端 Opus -> PCM -> 24kHz/60ms Opus -> 硬件发送回调。"""
        self._init_forward_codecs()
        pcm = self._fwd_decoder.decode(data, self._fwd_source_frame)
        if not pcm:
            return
        if self._fwd_resampler is not None:
            import numpy as np

            pcm = self._fwd_resampler.resample_chunk(
                np.frombuffer(pcm, dtype=np.int16), last=False
            ).astype("int16").tobytes()
            if not pcm:
                return
        self._fwd_pcm.extend(pcm)
        frame_bytes = FRAME_SAMPLES * 2  # mono int16
        while len(self._fwd_pcm) >= frame_bytes:
            chunk = bytes(self._fwd_pcm[:frame_bytes])
            del self._fwd_pcm[:frame_bytes]
            packet = self._fwd_encoder.encode(chunk, FRAME_SAMPLES)
            if packet:
                self._fwd_last_at = time.time()
                self._forward_callback(packet)

    @property
    def last_audio_at(self) -> float:
        return self._last_audio_at

    @property
    def is_playing(self) -> bool:
        if self._forward_callback is not None:
            if self._forward_playing is not None:
                try:
                    if self._forward_playing():
                        return True
                except Exception:
                    pass
            return bool(self._fwd_pcm) or (time.time() - self._fwd_last_at < 0.6)
        codec = self._codec
        try:
            output_stream = getattr(codec, "output_stream", None) if codec else None
            stream_active = True
            if output_stream is not None:
                try:
                    stream_active = bool(output_stream.active)
                except Exception:
                    stream_active = True
            output_queue = (
                getattr(codec, "_output_buffer", None) if codec is not None else None
            )
            resample_buffer = (
                getattr(codec, "_resample_output_buffer", None)
                if codec is not None
                else None
            )
            if output_queue is not None and not output_queue.empty():
                # 输出流已停止时，队列里的数据不可能再出声，不能一直判忙。
                if not stream_active:
                    return False
                # 输出流卡住时队列会一直不排空；最后写入超过 1.5 秒仍在
                # “播放”说明是假忙，放行后续消息，避免话题永远排队。
                if self._last_audio_at and time.time() - self._last_audio_at > 1.5:
                    return False
                return True
            # 24k -> 设备采样率的重采样缓冲里也可能还有尾音，不能只看
            # _output_buffer，否则会在最后一个字播完前提前开麦。
            if resample_buffer:
                # 输出回调按整块取数；不足一个输出块时只会输出静音，
                # 这部分残留不会发声，绝不能因此永久判定为“正在播放”。
                need = 0
                if output_stream is not None:
                    try:
                        blocksize = int(getattr(output_stream, "blocksize", 0) or 0)
                        need = blocksize if blocksize > 0 else 0
                    except Exception:
                        need = 0
                if need and len(resample_buffer) >= need:
                    if self._last_audio_at and time.time() - self._last_audio_at > 1.5:
                        return False
                    return True
                if not need:
                    return True
            # 声卡回调还有一小段硬件缓冲，留 120ms 尾音余量。
            return bool(self._last_audio_at and time.time() - self._last_audio_at < 0.12)
        except Exception:
            return False

    async def clear_input(self):
        """只清输入侧残留，绝不动正在播放的输出音频。"""
        if self._forward_callback is not None:
            return
        codec = self._codec
        if codec is None:
            return
        try:
            wakeword_buffer = getattr(codec, "_wakeword_buffer", None)
            if wakeword_buffer is not None:
                while not wakeword_buffer.empty():
                    wakeword_buffer.get_nowait()
            resample_input_buffer = getattr(codec, "_resample_input_buffer", None)
            if resample_input_buffer is not None:
                resample_input_buffer.clear()
        except Exception:
            pass

    async def clear(self):
        if self._forward_callback is not None:
            self._fwd_pcm.clear()
        if self._codec is not None:
            await self._codec.clear_audio_queue()

    async def close(self):
        codec = self._codec
        self._codec = None
        self.capturing = False
        if codec is not None:
            try:
                await codec.close()
            except Exception:
                pass
