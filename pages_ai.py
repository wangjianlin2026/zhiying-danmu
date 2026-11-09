import json
import os
import socket
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QImage, QMovie, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from widgets import PageBase, make_btn, make_check, make_field, make_group, make_scroll
from services.ai_client import AiClient
from data_access import load_ai_barrage_settings, save_ai_barrage_settings


APP_DIR = Path(__file__).resolve().parent
AI_STATUS_LOG = APP_DIR / "data" / "ai_status_debug.log"


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
AI_CONFIG_PATH = XIAOZHI_HOME / "config" / "config.json"
AI_KEYWORDS_PATH = XIAOZHI_HOME / "models" / "keywords.txt"


def _load_json(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _nested(config, *keys, default=""):
    value = config
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _form_group(title):
    group = make_group(title)
    form = QFormLayout(group)
    form.setContentsMargins(12, 16, 12, 12)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(9)
    return group, form


def _scroll_page(content):
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    return scroll


def _placeholder_combo(text):
    combo = QComboBox()
    combo.setEditable(True)
    combo.lineEdit().setPlaceholderText(text)
    combo.setCurrentText("")
    return combo


class AiChatPage(PageBase):
    def __init__(self, window):
        super().__init__("小智对话", None, window)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(12)

        self.status_label = QLabel("状态: 未连接")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            "QLabel { background:#E3F2FD; color:#2196F3; border-radius:12px;"
            " font-weight:700; padding:8px; }"
        )
        body.addWidget(self.status_label)

        main_row = QHBoxLayout()
        main_row.setSpacing(16)
        body.addLayout(main_row, 1)

        emotion_group = make_group("小智表情")
        emotion_group.setFixedWidth(330)
        emotion_layout = QVBoxLayout(emotion_group)
        emotion_layout.setContentsMargins(16, 18, 16, 18)
        emotion_layout.addStretch(1)
        self.emotion_label = QLabel("😊")
        self.emotion_label.setAlignment(Qt.AlignCenter)
        self.emotion_label.setStyleSheet("QLabel { font-size:44pt; }")
        emotion_layout.addWidget(self.emotion_label)
        emotion_layout.addStretch(1)
        main_row.addWidget(emotion_group)

        chat_group = make_group("对话记录")
        chat_layout = QVBoxLayout(chat_group)
        chat_layout.setContentsMargins(14, 14, 14, 14)
        chat_layout.setSpacing(10)

        history_content = QWidget()
        self.history_layout = QVBoxLayout(history_content)
        self.history_layout.setContentsMargins(6, 4, 6, 4)
        self.history_layout.setSpacing(10)
        self.history_layout.addWidget(
            self._message_row("🤖", "你好，我是小智！", ai=True)
        )
        self.history_layout.addWidget(
            self._message_row("🤖", "小智的声线直接突破音障,连宇宙都开始共振了!", ai=True)
        )
        self.history_layout.addStretch(1)
        chat_scroll = QScrollArea()
        chat_scroll.setObjectName("pageScroll")
        chat_scroll.setWidgetResizable(True)
        chat_scroll.setWidget(history_content)
        self.chat_scroll = chat_scroll
        chat_layout.addWidget(chat_scroll, 1)
        main_row.addWidget(chat_group, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.manual_btn = make_btn("按住说话", primary=True)
        self.manual_btn.setMinimumHeight(40)
        self.manual_btn.setMaximumHeight(40)
        self.manual_btn.pressed.connect(self._start_manual_capture)
        self.manual_btn.released.connect(self._stop_manual_capture)
        button_row.addWidget(self.manual_btn)

        self.auto_btn = make_btn("开始对话", primary=True)
        self.auto_btn.setMinimumHeight(40)
        self.auto_btn.setMaximumHeight(40)
        self.auto_btn.clicked.connect(self._toggle_auto_capture)
        self.auto_btn.hide()
        button_row.addWidget(self.auto_btn)

        self.abort_btn = make_btn("打断对话", on_click=self.abort_conversation)
        button_row.addWidget(self.abort_btn)
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("输入文字...")
        self.text_input.setMinimumHeight(40)
        self.text_input.returnPressed.connect(self._send_message)
        button_row.addWidget(self.text_input, 1)
        self.send_btn = make_btn("发送", primary=True)
        self.send_btn.clicked.connect(self._send_message)
        button_row.addWidget(self.send_btn)

        self.mode_btn = make_btn("手动对话")
        self.mode_btn.clicked.connect(self._toggle_mode)
        button_row.addWidget(self.mode_btn)

        settings_btn = make_btn(
            "参数配置",
            on_click=lambda: self.win.open_backend_tab("参数配置")
            if self.win and hasattr(self.win, "open_backend_tab")
            else None,
        )
        button_row.addWidget(settings_btn)
        body.addLayout(button_row)

        self.root.addWidget(make_scroll(content), 1)
        self._auto_mode = False
        self._auto_active = False
        self._emotion_movie = None
        self._last_user_text = ""
        self._last_ai_connected = None

        self.ai_client = AiClient(AI_CONFIG_PATH, self)
        self.ai_client.status_changed.connect(self._on_ai_status)
        self.ai_client.stt_received.connect(self._on_ai_stt)
        self.ai_client.reply_received.connect(self._on_ai_reply)
        self.ai_client.reply_finished.connect(self._on_ai_finished)
        self.ai_client.emotion_changed.connect(self._on_emotion)
        self.ai_client.error_occurred.connect(self._on_ai_error)
        self.ai_client.listening_state_changed.connect(self._on_listening_state)
        self.ai_client.cooldown_changed.connect(self._on_ai_cooldown)
        # 硬件喇叭模式下电脑端小智必须完全退出，不能只隐藏界面。
        if getattr(window, "output_channel", "software") != "hardware":
            self.ai_client.start()
        else:
            self.status_label.setText("状态: 已关闭（硬件喇叭）")
        self._restore_conversation_mode()

    def _message_row(self, avatar_text, message, ai=True):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        avatar = QLabel(avatar_text)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(40, 40)
        if ai:
            avatar.setStyleSheet(
                "QLabel { background:#F0F0F0; border:1px solid #E5E6EB;"
                " border-radius:20px; font-size:12pt; }"
            )
            bubble = QLabel(message)
            bubble.setWordWrap(True)
            bubble.setStyleSheet(
                "QLabel { background:white; border:1px solid #E5E6EB; border-radius:8px;"
                " padding:9px 12px; color:#1D2129; }"
            )
            layout.addWidget(avatar, 0, Qt.AlignTop)
            layout.addWidget(bubble, 0, Qt.AlignTop)
            layout.addStretch(1)
        else:
            avatar.setStyleSheet(
                "QLabel { background:#95EC69; border:none;"
                " border-radius:20px; font-size:10pt; }"
            )
            bubble = QLabel(message)
            bubble.setWordWrap(True)
            bubble.setStyleSheet(
                "QLabel { background:#95EC69; border:none; border-radius:8px;"
                " padding:9px 12px; color:#1D2129; }"
            )
            layout.addStretch(1)
            layout.addWidget(bubble, 0, Qt.AlignTop)
            layout.addWidget(avatar, 0, Qt.AlignTop)
        return row

    def _send_message(self):
        text = self.text_input.text().strip()
        if not text:
            return
        if not self._software_channel_enabled():
            self._on_ai_error("当前是硬件喇叭，软件小智已关闭；请先切到软件语音")
            return
        self._append_message("我", text, ai=False)
        self._last_user_text = text
        self.text_input.clear()
        # 统一走主窗口发送队列：忙时排队，不静默丢弃。
        win = getattr(self, "win", None)
        if win is not None and hasattr(win, "send_text_async"):
            if not win.send_text_async(text, "手动消息", True, notify=True):
                self._on_ai_error("消息未发出，请稍后重试")
        elif not self.ai_client.send_text(text):
            self._on_ai_error("消息未发出，请稍后重试")

    def set_auto_status(self, text):
        """把读弹幕状态转到「弹幕设置」页的共用设置区。"""
        pages = getattr(self.win, "pages", {}) or {}
        categories = pages.get("categories")
        if categories is not None and hasattr(categories, "set_auto_status"):
            categories.set_auto_status(text)

    def _on_ai_cooldown(self, active, minutes, reason):
        if active:
            self._auto_active = False
            if hasattr(self, "auto_btn"):
                self.auto_btn.setText("开始对话")
            self.set_auto_status(
                f"⏸ 连续对话保护：暂停约 {int(minutes)} 分钟"
            )
            self._append_message(
                "系统",
                f"小智触发连续对话保护，已暂停往云端发送约 {int(minutes)} 分钟。",
                ai=True,
            )
        else:
            self.set_auto_status("连续对话保护已结束，可继续使用")

    def _append_message(self, avatar, text, ai=True):
        row = self._message_row(avatar, text, ai=ai)
        self.history_layout.insertWidget(self.history_layout.count() - 1, row)
        # 对话页控件不设上限的话，直播几小时后会越来越卡。
        max_rows = 200
        while self.history_layout.count() > max_rows + 1:
            item = self.history_layout.takeAt(1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        )

    def _on_ai_status(self, text, connected):
        text = str(text or "")
        connection_keywords = (
            "连接中", "正在连接", "正在自动重连", "断开", "中断",
            "失败", "错误", "异常",
        )
        if connected != self._last_ai_connected or any(
            keyword in text for keyword in connection_keywords
        ):
            self._log_ai_status(text, connected)
        self._last_ai_connected = bool(connected)
        self.status_label.setText(f"状态: {text}")
        if "正在" in text or "已发送" in text:
            color, background = "#FF7D00", "#FFF3E8"
        elif connected:
            color, background = "#00B42A", "#E8FFEA"
        else:
            color, background = "#F53F3F", "#FFECE8"
        # 网络晚就绪时补一次自动对话恢复，避免开机第一次没连上就永久停在手动态。
        if connected and self._auto_mode and not self._auto_active:
            try:
                settings = load_ai_barrage_settings()
            except Exception:
                settings = {}
            if settings.get("auto_active"):
                QTimer.singleShot(800, self._resume_saved_auto_dialog)
        self.status_label.setStyleSheet(
            f"QLabel {{ background:{background}; color:{color}; border-radius:12px;"
            " font-weight:700; padding:8px; }"
        )

    def _on_ai_stt(self, text):
        if text and text != self._last_user_text:
            self._append_message("我", text, ai=False)

    def _on_ai_reply(self, text):
        if text:
            self._append_message("🤖", text, ai=True)

    def _on_ai_finished(self):
        self.send_btn.setEnabled(True)

    def _on_ai_error(self, text):
        self._log_ai_status(f"ERROR: {text}", False)
        self.status_label.setText(f"状态: {text}")
        self.status_label.setStyleSheet(
            "QLabel { background:#FFECE8; color:#F53F3F; border-radius:12px;"
            " font-weight:700; padding:8px; }"
        )

    @staticmethod
    def _log_ai_status(text, connected):
        try:
            AI_STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with AI_STATUS_LOG.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"connected={int(bool(connected))} text={text}\n"
                )
        except Exception:
            pass

    def _on_emotion(self, emotion):
        mapping = {
            "happy": "😊", "laughing": "😆", "funny": "😄",
            "loving": "😍", "kissy": "😘", "winking": "😉",
            "sad": "😢", "crying": "😭", "angry": "😠",
            "surprised": "😮", "shocked": "😱", "confused": "😕",
            "thinking": "🤔", "sleepy": "😴", "relaxed": "😌",
            "confident": "😎", "cool": "😎", "embarrassed": "😳",
            "delicious": "😋", "silly": "😜", "neutral": "😐",
        }
        gif = XIAOZHI_HOME / "assets" / "emojis" / f"{emotion}.gif"
        if gif.exists():
            self._emotion_movie = QMovie(str(gif))
            self._emotion_movie.setScaledSize(QSize(180, 180))
            self.emotion_label.setText("")
            self.emotion_label.setMovie(self._emotion_movie)
            self._emotion_movie.start()
        else:
            self.emotion_label.setText(mapping.get(emotion, "😊"))

    def abort_conversation(self):
        self.ai_client.abort()

    def _software_channel_enabled(self):
        win = getattr(self, "win", None)
        return getattr(win, "output_channel", "software") != "hardware"

    def _start_manual_capture(self):
        if not self._software_channel_enabled():
            self._on_ai_error("当前是硬件喇叭，软件小智已关闭；请先切到软件语音")
            return
        self.manual_btn.setText("松开结束")
        if not self.ai_client.start_voice_capture():
            self.manual_btn.setText("按住说话")
            self._on_ai_error("麦克风启动失败")

    def _stop_manual_capture(self):
        self.manual_btn.setText("按住说话")
        self.ai_client.stop_voice_capture()

    def _toggle_auto_capture(self):
        if self._auto_active:
            self._auto_active = False
            self.auto_btn.setText("开始对话")
            self.ai_client.stop_voice_capture()
            self._save_conversation_state()
            return
        if not self._software_channel_enabled():
            self._on_ai_error("当前是硬件喇叭，软件小智已关闭；请先切到软件语音")
            return
        if self.ai_client.is_in_cooldown:
            self.set_auto_status(
                "⏸ 连续对话保护：暂停约 "
                f"{self.ai_client.cooldown_remaining_minutes} 分钟"
            )
            return
        if self.ai_client.start_voice_capture("auto"):
            self._auto_active = True
            self.auto_btn.setText("停止对话")
            self._save_conversation_state()
        else:
            self._on_ai_error("自动对话启动失败")

    def _on_listening_state(self, active, text):
        if not active and self._auto_active:
            self._auto_active = False
            self.auto_btn.setText("开始对话")
            # 这里不落盘：TTS 收尾等内部状态不应覆盖用户“要自动对话”的偏好。

    def shutdown(self):
        self._save_conversation_state()
        self.ai_client.stop()

    def _save_conversation_state(self):
        current = load_ai_barrage_settings()
        current["conversation_mode"] = "auto" if self._auto_mode else "manual"
        current["auto_active"] = bool(self._auto_active)
        save_ai_barrage_settings(current)

    def _restore_conversation_mode(self):
        settings = load_ai_barrage_settings()
        if self.ai_client.is_in_cooldown:
            self.set_auto_status(
                "⏸ 连续对话保护：暂停约 "
                f"{self.ai_client.cooldown_remaining_minutes} 分钟"
            )
        mode = str(settings.get("conversation_mode", "manual")).lower()
        self._auto_mode = mode == "auto"
        if self._auto_mode:
            self.manual_btn.hide()
            self.auto_btn.show()
            self.mode_btn.setText("自动对话")
            if bool(settings.get("auto_active", False)):
                QTimer.singleShot(1500, self._resume_saved_auto_dialog)
        else:
            self.auto_btn.hide()
            self.manual_btn.show()
            self.mode_btn.setText("手动对话")

    def _resume_saved_auto_dialog(self):
        if not self._auto_mode or self._auto_active:
            return
        if not self._software_channel_enabled():
            return
        if self.ai_client.is_in_cooldown:
            self.set_auto_status(
                "⏸ 连续对话保护：暂停约 "
                f"{self.ai_client.cooldown_remaining_minutes} 分钟"
            )
            return
        if self.ai_client.start_voice_capture("auto"):
            self._auto_active = True
            self.auto_btn.setText("停止对话")
            self._save_conversation_state()

    def _toggle_mode(self):
        # 切到自动对话时直接开麦，避免用户误以为“切了模式”就已经在听。
        if self._auto_active:
            self._auto_active = False
            self.auto_btn.setText("开始对话")
            self.ai_client.stop_voice_capture()
        self._auto_mode = not self._auto_mode
        if self._auto_mode:
            self.manual_btn.hide()
            self.auto_btn.show()
            self.mode_btn.setText("自动对话")
            self._toggle_auto_capture()
        else:
            self.auto_btn.hide()
            self.manual_btn.show()
            self.mode_btn.setText("手动对话")
        self._save_conversation_state()


class AiSettingsPage(PageBase):
    def __init__(self, window=None):
        super().__init__("参数设置", None, window)
        self.win = window
        self.config = _load_json(AI_CONFIG_PATH)

        self.root.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        self.tabs.addTab(self._system_options_tab(), "系统选项")
        self.tabs.addTab(self._wake_word_tab(), "唤醒词")
        self.tabs.addTab(self._camera_tab(), "摄像头")
        self.tabs.addTab(self._audio_tab(), "音频设备")
        self.tabs.addTab(self._shortcuts_tab(), "快捷键")
        self.tabs.addTab(QWidget(), "设备绑定")
        self._last_tab_index = self.tabs.currentIndex()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        reset_btn = make_btn("重置")
        reset_btn.clicked.connect(self._reset_settings)
        footer.addWidget(reset_btn)
        save_btn = make_btn("保存", primary=True)
        save_btn.clicked.connect(self._save_settings)
        footer.addWidget(save_btn)
        cancel_btn = make_btn("取消")
        cancel_btn.clicked.connect(self._cancel_settings)
        footer.addWidget(cancel_btn)
        self.root.addLayout(footer)

        # 页面创建后自动枚举音频设备，避免用户首次进入时看到空列表。
        QTimer.singleShot(0, self._scan_audio_devices)

    def _on_tab_changed(self, index):
        """“设备绑定”不是真页面：点它就弹小窗口，然后切回原来的标签。"""
        if self.tabs.tabText(index) != "设备绑定":
            self._last_tab_index = index
            return
        previous = self._last_tab_index
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(previous if previous != index else 0)
        self.tabs.blockSignals(False)
        QTimer.singleShot(0, lambda: open_device_activation_dialog(self.win, self))

    def _system_options_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)

        system = self.config.get("SYSTEM_OPTIONS", {})
        network = system.get("NETWORK", {})
        mqtt = network.get("MQTT_INFO", {})

        self.client_id_edit = make_field(str(system.get("CLIENT_ID", "")))
        form.addRow("客户端ID:", self.client_id_edit)
        self.device_id_edit = make_field(str(system.get("DEVICE_ID", "")))
        form.addRow("设备ID:", self.device_id_edit)
        self.ota_url_edit = make_field(str(network.get("OTA_VERSION_URL", "")))
        form.addRow("OTA版本URL:", self.ota_url_edit)
        self.ws_url_edit = make_field(str(network.get("WEBSOCKET_URL", "")))
        form.addRow("WebSocket URL:", self.ws_url_edit)
        self.ws_token_edit = make_field(str(network.get("WEBSOCKET_ACCESS_TOKEN", "")))
        self.ws_token_edit.setEchoMode(QLineEdit.Password)
        form.addRow("WebSocket Token:", self.ws_token_edit)
        self.auth_url_edit = make_field(str(network.get("AUTHORIZATION_URL", "")))
        form.addRow("授权URL:", self.auth_url_edit)

        self.activation_combo = QComboBox()
        self.activation_combo.addItems(["v1", "v2"])
        self.activation_combo.setCurrentText(
            str(network.get("ACTIVATION_VERSION", "v1"))
        )
        form.addRow("激活版本:", self.activation_combo)

        self.aec_check = make_check(
            "启用",
            bool(_nested(self.config, "AEC_OPTIONS", "ENABLED", default=False)),
        )
        form.addRow("启用AEC回声消除:", self.aec_check)

        self.window_combo = QComboBox()
        self.window_combo.addItems(["默认", "75%", "100%"])
        window_map = {"default": "默认", "75%": "75%", "100%": "100%"}
        self.window_combo.setCurrentText(
            window_map.get(str(system.get("WINDOW_SIZE_MODE", "default")), "默认")
        )
        form.addRow("主界面宽高:", self.window_combo)
        layout.addLayout(form)

        mqtt_group = make_group("MQTT配置")
        mqtt_form = QFormLayout(mqtt_group)
        mqtt_form.setContentsMargins(12, 16, 12, 12)
        mqtt_form.setHorizontalSpacing(12)
        mqtt_form.setVerticalSpacing(9)
        self.mqtt_endpoint_edit = make_field(str(mqtt.get("endpoint", "")))
        mqtt_form.addRow("端点:", self.mqtt_endpoint_edit)
        self.mqtt_client_id_edit = make_field(str(mqtt.get("client_id", "")))
        mqtt_form.addRow("客户端ID:", self.mqtt_client_id_edit)
        self.mqtt_username_edit = make_field(str(mqtt.get("username", "")))
        mqtt_form.addRow("用户名:", self.mqtt_username_edit)
        self.mqtt_password_edit = make_field(str(mqtt.get("password", "")))
        self.mqtt_password_edit.setEchoMode(QLineEdit.Password)
        mqtt_form.addRow("密码:", self.mqtt_password_edit)
        self.mqtt_publish_edit = make_field(str(mqtt.get("publish_topic", "")))
        mqtt_form.addRow("发布主题:", self.mqtt_publish_edit)
        self.mqtt_subscribe_edit = make_field(str(mqtt.get("subscribe_topic", "")))
        mqtt_form.addRow("订阅主题:", self.mqtt_subscribe_edit)
        layout.addWidget(mqtt_group)
        layout.addStretch(1)
        return _scroll_page(content)

    def _wake_word_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        wake = self.config.get("WAKE_WORD_OPTIONS", {})
        self.wake_enabled_check = make_check(
            "启用唤醒词检测", bool(wake.get("USE_WAKE_WORD", False))
        )
        layout.addWidget(self.wake_enabled_check)

        model_group, model_form = _form_group("模型配置")
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.wake_model_edit = make_field(str(wake.get("MODEL_PATH", "")))
        model_row.addWidget(self.wake_model_edit, 1)
        browse_btn = make_btn("浏览...")
        browse_btn.clicked.connect(self._browse_wake_model)
        model_row.addWidget(browse_btn)
        model_form.addRow("模型路径:", model_row)
        layout.addWidget(model_group)

        keyword_group = make_group("唤醒词配置")
        keyword_layout = QVBoxLayout(keyword_group)
        keyword_layout.setContentsMargins(12, 16, 12, 12)
        keyword_layout.setSpacing(9)
        help_label = QLabel(
            "请输入中文唤醒词，保存时会自动转换为拼音格式\n"
            "例如：小米小米（将自动转换为：x iǎo m ǐ x iǎo m ǐ @小米小米）"
        )
        help_label.setStyleSheet("QLabel { color:#666666; font-size:9pt; }")
        keyword_layout.addWidget(help_label)
        self.wake_words_edit = QTextEdit()
        self.wake_words_edit.setMinimumHeight(200)
        self.wake_words_edit.setPlaceholderText(
            "请输入中文唤醒词，每行一个（如：小米小米）"
        )
        try:
            text = AI_KEYWORDS_PATH.read_text(encoding="utf-8-sig")
            if text.strip():
                self.wake_words_edit.setPlainText(text)
        except (OSError, UnicodeError):
            pass
        keyword_layout.addWidget(self.wake_words_edit)
        layout.addWidget(keyword_group)
        layout.addStretch(1)
        return _scroll_page(content)

    def _camera_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        camera = self.config.get("CAMERA", {})

        basic_group, basic_form = _form_group("摄像头基本配置")
        index_row = QHBoxLayout()
        index_row.setSpacing(8)
        self.camera_index_spin = QSpinBox()
        self.camera_index_spin.setRange(0, 10)
        self.camera_index_spin.setValue(int(camera.get("camera_index", 0)))
        index_row.addWidget(self.camera_index_spin)
        scan_camera_btn = make_btn("扫描摄像头")
        scan_camera_btn.clicked.connect(self._scan_cameras)
        index_row.addWidget(scan_camera_btn)
        index_row.addStretch(1)
        basic_form.addRow("摄像头索引:", index_row)

        self.camera_width_spin = QSpinBox()
        self.camera_width_spin.setRange(320, 3840)
        self.camera_width_spin.setSingleStep(160)
        self.camera_width_spin.setValue(int(camera.get("frame_width", 640)))
        basic_form.addRow("帧宽度:", self.camera_width_spin)

        self.camera_height_spin = QSpinBox()
        self.camera_height_spin.setRange(240, 2160)
        self.camera_height_spin.setSingleStep(120)
        self.camera_height_spin.setValue(int(camera.get("frame_height", 480)))
        basic_form.addRow("帧高度:", self.camera_height_spin)

        self.camera_fps_spin = QSpinBox()
        self.camera_fps_spin.setRange(1, 60)
        self.camera_fps_spin.setValue(int(camera.get("fps", 30)))
        basic_form.addRow("帧率(FPS):", self.camera_fps_spin)
        layout.addWidget(basic_group)

        vl_group, vl_form = _form_group("视觉语言模型配置")
        self.vl_url_edit = make_field(str(camera.get("Local_VL_url", "")))
        self.vl_url_edit.setPlaceholderText(
            "例如: https://open.bigmodel.cn/api/paas/v4/"
        )
        vl_form.addRow("本地VL URL:", self.vl_url_edit)
        self.vl_api_key_edit = make_field(str(camera.get("VLapi_key", "")))
        self.vl_api_key_edit.setEchoMode(QLineEdit.Password)
        self.vl_api_key_edit.setPlaceholderText("请输入您的API密钥")
        vl_form.addRow("VL API Key:", self.vl_api_key_edit)
        self.vl_model_edit = make_field(str(camera.get("models", "")))
        self.vl_model_edit.setPlaceholderText("例如: glm-4v-plus")
        vl_form.addRow("模型名称:", self.vl_model_edit)
        layout.addWidget(vl_group)

        preview_group = make_group("摄像头预览")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(12, 16, 12, 12)
        preview_layout.setSpacing(9)
        self.camera_preview = QLabel("摄像头预览区域\n点击刷新预览或更改摄像头索引")
        self.camera_preview.setAlignment(Qt.AlignCenter)
        self.camera_preview.setMinimumSize(320, 240)
        self.camera_preview.setMaximumHeight(300)
        self.camera_preview.setStyleSheet(
            "QLabel { border:2px solid #CCCCCC; background-color:#000000; color:white; }"
        )
        preview_layout.addWidget(self.camera_preview, 0, Qt.AlignHCenter)
        preview_buttons = QHBoxLayout()
        start_preview = make_btn("开始预览")
        start_preview.clicked.connect(self._start_camera_preview)
        preview_buttons.addWidget(start_preview)
        self.stop_preview_btn = make_btn("停止预览")
        self.stop_preview_btn.setEnabled(False)
        self.stop_preview_btn.clicked.connect(self._stop_camera_preview)
        preview_buttons.addWidget(self.stop_preview_btn)
        preview_buttons.addStretch(1)
        preview_layout.addLayout(preview_buttons)
        layout.addWidget(preview_group)
        layout.addStretch(1)
        self._camera_capture = None
        self._camera_timer = QTimer(self)
        self._camera_timer.timeout.connect(self._read_camera_frame)
        return _scroll_page(content)

    def _audio_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        input_group = make_group("麦克风输入")
        input_form = QFormLayout(input_group)
        input_form.setContentsMargins(12, 16, 12, 12)
        input_form.setHorizontalSpacing(12)
        input_form.setVerticalSpacing(9)
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input_device_combo = _placeholder_combo("请选择麦克风设备...")
        input_row.addWidget(self.input_device_combo, 1)
        test_record_btn = make_btn("测试录音")
        test_record_btn.clicked.connect(self._test_record)
        input_row.addWidget(test_record_btn)
        input_form.addRow("输入设备:", input_row)
        self.input_device_info = QLabel("未选择设备")
        input_form.addRow("设备信息:", self.input_device_info)
        layout.addWidget(input_group)

        output_group = make_group("扬声器输出")
        output_form = QFormLayout(output_group)
        output_form.setContentsMargins(12, 16, 12, 12)
        output_form.setHorizontalSpacing(12)
        output_form.setVerticalSpacing(9)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_device_combo = _placeholder_combo("请选择扬声器设备...")
        output_row.addWidget(self.output_device_combo, 1)
        test_play_btn = make_btn("测试播放")
        test_play_btn.clicked.connect(self._test_play)
        output_row.addWidget(test_play_btn)
        output_form.addRow("输出设备:", output_row)
        self.output_device_info = QLabel("未选择设备")
        output_form.addRow("设备信息:", self.output_device_info)
        layout.addWidget(output_group)

        all_group = make_group("音频设备总览")
        all_layout = QVBoxLayout(all_group)
        all_layout.setContentsMargins(12, 16, 12, 12)
        self.all_devices_text = QTextEdit()
        self.all_devices_text.setReadOnly(True)
        self.all_devices_text.setMinimumHeight(180)
        self.all_devices_text.setMaximumHeight(260)
        self.all_devices_text.setStyleSheet(
            "QTextEdit { font-family: Consolas, Microsoft YaHei UI; }"
        )
        all_layout.addWidget(self.all_devices_text)
        layout.addWidget(all_group)

        scan_row = QHBoxLayout()
        scan_btn = make_btn("扫描设备")
        scan_btn.clicked.connect(self._scan_audio_devices)
        scan_row.addWidget(scan_btn)
        scan_row.addStretch(1)
        layout.addLayout(scan_row)

        status_group = make_group("设备信息")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(12, 16, 12, 12)
        self.audio_status_text = QTextEdit()
        self.audio_status_text.setReadOnly(True)
        self.audio_status_text.setMaximumHeight(120)
        self.audio_status_text.setPlaceholderText("设备信息和测试状态将显示在这里...")
        status_layout.addWidget(self.audio_status_text)
        layout.addWidget(status_group)
        layout.addStretch(1)
        self.input_device_combo.currentIndexChanged.connect(
            self._on_input_device_changed
        )
        self.output_device_combo.currentIndexChanged.connect(
            self._on_output_device_changed
        )
        return _scroll_page(content)

    def _shortcuts_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        shortcuts = self.config.get("SHORTCUTS", {})
        self.shortcuts_enabled_check = make_check(
            "启用全局快捷键", bool(shortcuts.get("ENABLED", True))
        )
        layout.addWidget(self.shortcuts_enabled_check)

        group = make_group("快捷键配置")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 16, 12, 12)
        group_layout.setSpacing(9)

        shortcut_rows = [
            ("按住说话", "MANUAL_PRESS", "j"),
            ("自动对话", "AUTO_TOGGLE", "k"),
            ("中断对话", "ABORT", "q"),
            ("模式切换", "MODE_TOGGLE", "m"),
            ("窗口显示/隐藏", "WINDOW_TOGGLE", "w"),
        ]
        self.shortcut_widgets = {}
        for title, key, default_key in shortcut_rows:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(QLabel(f"{title}:"))
            modifier = QComboBox()
            modifier.addItems(["Ctrl", "Alt", "Shift"])
            stored_modifier = str(
                _nested(shortcuts, key, "modifier", default="ctrl")
            ).title()
            modifier.setCurrentText(stored_modifier)
            key_combo = QComboBox()
            key_combo.addItems([chr(value) for value in range(ord("a"), ord("z") + 1)])
            key_combo.setCurrentText(
                str(_nested(shortcuts, key, "key", default=default_key)).lower()
            )
            row.addWidget(modifier)
            row.addWidget(key_combo)
            row.addStretch(1)
            group_layout.addLayout(row)
            self.shortcut_widgets[key] = (modifier, key_combo)

        layout.addWidget(group)
        shortcut_buttons = QHBoxLayout()
        reset_btn = make_btn("恢复默认")
        reset_btn.clicked.connect(self._reset_shortcuts)
        shortcut_buttons.addWidget(reset_btn)
        apply_btn = make_btn("应用")
        apply_btn.clicked.connect(self._apply_shortcuts)
        shortcut_buttons.addWidget(apply_btn)
        shortcut_buttons.addStretch(1)
        layout.addLayout(shortcut_buttons)
        layout.addStretch(1)
        return _scroll_page(content)

    def _reset_shortcuts(self):
        defaults = {
            "MANUAL_PRESS": "j",
            "AUTO_TOGGLE": "k",
            "ABORT": "q",
            "MODE_TOGGLE": "m",
            "WINDOW_TOGGLE": "w",
        }
        for key, default_key in defaults.items():
            modifier, key_combo = self.shortcut_widgets[key]
            modifier.setCurrentText("Ctrl")
            key_combo.setCurrentText(default_key)

    # ---------- 保存 / 重置 / 取消 ----------
    def _window_mode_value(self):
        return {
            "默认": "default", "75%": "75%", "100%": "100%",
        }.get(self.window_combo.currentText(), "default")

    def _build_config(self):
        data = json.loads(json.dumps(self.config, ensure_ascii=False))
        system = data.setdefault("SYSTEM_OPTIONS", {})
        system["CLIENT_ID"] = self.client_id_edit.text().strip()
        system["DEVICE_ID"] = self.device_id_edit.text().strip()
        system["WINDOW_SIZE_MODE"] = self._window_mode_value()
        network = system.setdefault("NETWORK", {})
        network["OTA_VERSION_URL"] = self.ota_url_edit.text().strip()
        network["WEBSOCKET_URL"] = self.ws_url_edit.text().strip()
        network["WEBSOCKET_ACCESS_TOKEN"] = self.ws_token_edit.text().strip()
        network["AUTHORIZATION_URL"] = self.auth_url_edit.text().strip()
        network["ACTIVATION_VERSION"] = self.activation_combo.currentText()
        mqtt = network.setdefault("MQTT_INFO", {})
        mqtt["endpoint"] = self.mqtt_endpoint_edit.text().strip()
        mqtt["client_id"] = self.mqtt_client_id_edit.text().strip()
        mqtt["username"] = self.mqtt_username_edit.text().strip()
        mqtt["password"] = self.mqtt_password_edit.text()
        mqtt["publish_topic"] = self.mqtt_publish_edit.text().strip()
        mqtt["subscribe_topic"] = self.mqtt_subscribe_edit.text().strip()

        wake = data.setdefault("WAKE_WORD_OPTIONS", {})
        wake["USE_WAKE_WORD"] = self.wake_enabled_check.isChecked()
        wake["MODEL_PATH"] = self.wake_model_edit.text().strip()

        camera = data.setdefault("CAMERA", {})
        camera["camera_index"] = self.camera_index_spin.value()
        camera["frame_width"] = self.camera_width_spin.value()
        camera["frame_height"] = self.camera_height_spin.value()
        camera["fps"] = self.camera_fps_spin.value()
        camera["Local_VL_url"] = self.vl_url_edit.text().strip()
        camera["VLapi_key"] = self.vl_api_key_edit.text().strip()
        camera["models"] = self.vl_model_edit.text().strip()

        audio = data.setdefault("AUDIO_DEVICES", {})
        input_index = self.input_device_combo.currentIndex()
        if input_index >= 0:
            audio["input_device_id"] = self.input_device_combo.itemData(input_index)
            audio["input_device_name"] = (
                self.input_device_combo.itemData(input_index, Qt.UserRole + 1)
                or self.input_device_combo.currentText()
            )
        output_index = self.output_device_combo.currentIndex()
        if output_index >= 0:
            audio["output_device_id"] = self.output_device_combo.itemData(output_index)
            audio["output_device_name"] = (
                self.output_device_combo.itemData(output_index, Qt.UserRole + 1)
                or self.output_device_combo.currentText()
            )

        data.setdefault("AEC_OPTIONS", {})["ENABLED"] = self.aec_check.isChecked()

        shortcuts = data.setdefault("SHORTCUTS", {})
        shortcuts["ENABLED"] = self.shortcuts_enabled_check.isChecked()
        for key, (modifier, key_combo) in self.shortcut_widgets.items():
            shortcuts[key] = {
                "modifier": modifier.currentText().lower(),
                "key": key_combo.currentText().lower(),
                "description": shortcuts.get(key, {}).get("description", ""),
            }
        return data

    def _save_settings(self):
        try:
            data = self._build_config()
            AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp = AI_CONFIG_PATH.with_name(AI_CONFIG_PATH.name + ".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(temp, AI_CONFIG_PATH)
            AI_KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            AI_KEYWORDS_PATH.write_text(
                self.wake_words_edit.toPlainText().strip() + "\n",
                encoding="utf-8",
            )
            self.config = data
            ai_chat = self.win.pages.get("ai_chat") if self.win else None
            if ai_chat is not None and hasattr(ai_chat, "ai_client"):
                ai_chat.ai_client.reload_config()
            self.notify("参数配置已保存到小智配置文件")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"写入配置失败：{exc}")

    def _load_from_disk(self):
        self.config = _load_json(AI_CONFIG_PATH)
        system = self.config.get("SYSTEM_OPTIONS", {})
        network = system.get("NETWORK", {})
        mqtt = network.get("MQTT_INFO", {})
        self.client_id_edit.setText(str(system.get("CLIENT_ID", "")))
        self.device_id_edit.setText(str(system.get("DEVICE_ID", "")))
        self.ota_url_edit.setText(str(network.get("OTA_VERSION_URL", "")))
        self.ws_url_edit.setText(str(network.get("WEBSOCKET_URL", "")))
        self.ws_token_edit.setText(str(network.get("WEBSOCKET_ACCESS_TOKEN", "")))
        self.auth_url_edit.setText(str(network.get("AUTHORIZATION_URL", "")))
        self.activation_combo.setCurrentText(
            str(network.get("ACTIVATION_VERSION", "v1"))
        )
        self.mqtt_endpoint_edit.setText(str(mqtt.get("endpoint", "")))
        self.mqtt_client_id_edit.setText(str(mqtt.get("client_id", "")))
        self.mqtt_username_edit.setText(str(mqtt.get("username", "")))
        self.mqtt_password_edit.setText(str(mqtt.get("password", "")))
        self.mqtt_publish_edit.setText(str(mqtt.get("publish_topic", "")))
        self.mqtt_subscribe_edit.setText(str(mqtt.get("subscribe_topic", "")))
        self.aec_check.setChecked(
            bool(_nested(self.config, "AEC_OPTIONS", "ENABLED", default=False))
        )
        wake = self.config.get("WAKE_WORD_OPTIONS", {})
        self.wake_enabled_check.setChecked(bool(wake.get("USE_WAKE_WORD", False)))
        self.wake_model_edit.setText(str(wake.get("MODEL_PATH", "")))
        try:
            self.wake_words_edit.setPlainText(
                AI_KEYWORDS_PATH.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError):
            self.wake_words_edit.clear()
        camera = self.config.get("CAMERA", {})
        self.camera_index_spin.setValue(int(camera.get("camera_index", 0)))
        self.camera_width_spin.setValue(int(camera.get("frame_width", 640)))
        self.camera_height_spin.setValue(int(camera.get("frame_height", 480)))
        self.camera_fps_spin.setValue(int(camera.get("fps", 30)))
        self.vl_url_edit.setText(str(camera.get("Local_VL_url", "")))
        self.vl_api_key_edit.setText(str(camera.get("VLapi_key", "")))
        self.vl_model_edit.setText(str(camera.get("models", "")))
        shortcuts = self.config.get("SHORTCUTS", {})
        self.shortcuts_enabled_check.setChecked(
            bool(shortcuts.get("ENABLED", True))
        )
        for key, (modifier, key_combo) in self.shortcut_widgets.items():
            item = shortcuts.get(key, {})
            modifier.setCurrentText(str(item.get("modifier", "ctrl")).title())
            if item.get("key"):
                key_combo.setCurrentText(str(item["key"]).lower())
        self._scan_audio_devices()

    def _reset_settings(self):
        self._load_from_disk()
        self.notify("已恢复为已保存的配置（尚未写入）")

    def _cancel_settings(self):
        self._load_from_disk()
        self.notify("已取消未保存的修改")

    # ---------- 唤醒词 / 摄像头 ----------
    def _browse_wake_model(self):
        from PyQt5.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(
            self, "选择唤醒词模型目录", str(XIAOZHI_HOME / "models")
        )
        if path:
            self.wake_model_edit.setText(path)

    def _scan_cameras(self):
        try:
            import cv2
        except ImportError:
            QMessageBox.warning(self, "提示", "未安装 opencv，无法扫描摄像头")
            return
        found = []
        for index in range(10):
            capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if capture.isOpened():
                ok, _frame = capture.read()
                if ok:
                    found.append(index)
            capture.release()
        if found:
            self.camera_index_spin.setValue(found[0])
            self.notify(f"发现摄像头索引: {', '.join(map(str, found))}")
        else:
            QMessageBox.warning(self, "提示", "未发现可用摄像头")

    def _start_camera_preview(self):
        try:
            import cv2
        except ImportError:
            QMessageBox.warning(self, "提示", "未安装 opencv，无法预览摄像头")
            return
        self._stop_camera_preview()
        capture = cv2.VideoCapture(
            self.camera_index_spin.value(), cv2.CAP_DSHOW
        )
        if not capture.isOpened():
            QMessageBox.warning(self, "提示", "摄像头打开失败")
            return
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera_width_spin.value())
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera_height_spin.value())
        self._camera_capture = capture
        self.stop_preview_btn.setEnabled(True)
        self._camera_timer.start(max(30, int(1000 / max(1, self.camera_fps_spin.value()))))

    def _read_camera_frame(self):
        if self._camera_capture is None:
            return
        ok, frame = self._camera_capture.read()
        if not ok or frame is None:
            return
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(
            rgb.data, width, height, channels * width, QImage.Format_RGB888
        ).copy()
        self.camera_preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.camera_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def _stop_camera_preview(self):
        self._camera_timer.stop()
        if self._camera_capture is not None:
            self._camera_capture.release()
            self._camera_capture = None
        self.stop_preview_btn.setEnabled(False)
        self.camera_preview.setText("摄像头预览区域\n点击刷新预览或更改摄像头索引")

    # ---------- 音频设备 ----------
    def _scan_audio_devices(self):
        try:
            import sounddevice as sd
        except ImportError:
            QMessageBox.warning(self, "提示", "未安装 sounddevice，无法扫描音频设备")
            return
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception as exc:
            QMessageBox.critical(self, "扫描失败", f"读取音频设备失败：{exc}")
            return
        self.input_device_combo.blockSignals(True)
        self.output_device_combo.blockSignals(True)
        self.input_device_combo.clear()
        self.output_device_combo.clear()
        overview = [
            f"系统共发现 {len(devices)} 个音频端点；"
            "同一硬件会因 MME / DirectSound / WASAPI / WDM-KS 重复出现。",
            "",
        ]
        for index, device in enumerate(devices):
            name = str(device.get("name", f"设备 {index}"))
            inputs = int(device.get("max_input_channels", 0))
            outputs = int(device.get("max_output_channels", 0))
            host_index = int(device.get("hostapi", 0))
            host_name = (
                str(hostapis[host_index].get("name", f"HostAPI {host_index}"))
                if 0 <= host_index < len(hostapis)
                else f"HostAPI {host_index}"
            )
            channels = []
            if inputs:
                channels.append(f"输入 {inputs}ch")
            if outputs:
                channels.append(f"输出 {outputs}ch")
            direction = " / ".join(channels)
            label = f"ID {index} · [{host_name}] {name} · {direction}"
            if inputs > 0:
                self.input_device_combo.addItem(label, index)
                self.input_device_combo.setItemData(
                    self.input_device_combo.count() - 1, name, Qt.UserRole + 1
                )
            if outputs > 0:
                self.output_device_combo.addItem(label, index)
                self.output_device_combo.setItemData(
                    self.output_device_combo.count() - 1, name, Qt.UserRole + 1
                )
            overview.append(f"{index:>2}  {direction:<14} [{host_name}] {name}")
        self.input_device_combo.setEditable(False)
        self.output_device_combo.setEditable(False)
        self.input_device_combo.blockSignals(False)
        self.output_device_combo.blockSignals(False)
        audio = self.config.get("AUDIO_DEVICES", {})
        input_id = audio.get("input_device_id")
        output_id = audio.get("output_device_id")
        for combo, wanted in (
            (self.input_device_combo, input_id),
            (self.output_device_combo, output_id),
        ):
            for index in range(combo.count()):
                if combo.itemData(index) == wanted:
                    combo.setCurrentIndex(index)
                    break
        self._on_input_device_changed(self.input_device_combo.currentIndex())
        self._on_output_device_changed(self.output_device_combo.currentIndex())
        self.audio_status_text.setPlainText(
            f"发现 {self.input_device_combo.count()} 个输入设备、"
            f"{self.output_device_combo.count()} 个输出设备\n"
            f"当前输入：{self.input_device_combo.currentText() or '未选择'}\n"
            f"当前输出：{self.output_device_combo.currentText() or '未选择'}"
        )
        self.all_devices_text.setPlainText("\n".join(overview))

    def _on_input_device_changed(self, index):
        if index < 0:
            self.input_device_info.setText("未选择设备")
            return
        self.input_device_info.setText(self.input_device_combo.currentText())

    def _on_output_device_changed(self, index):
        if index < 0:
            self.output_device_info.setText("未选择设备")
            return
        self.output_device_info.setText(self.output_device_combo.currentText())

    def _test_record(self):
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            QMessageBox.warning(self, "提示", "缺少 numpy/sounddevice，无法测试录音")
            return
        index = self.input_device_combo.currentIndex()
        if index < 0:
            QMessageBox.warning(self, "提示", "请先扫描并选择麦克风")
            return
        device_id = self.input_device_combo.itemData(index)
        try:
            self.audio_status_text.setPlainText("正在录音 2 秒...")
            QApplication.processEvents()
            recording = sd.rec(
                int(2 * 16000), samplerate=16000, channels=1,
                dtype="int16", device=device_id,
            )
            sd.wait()
            peak = int(np.abs(recording).max()) if recording.size else 0
            self.audio_status_text.setPlainText(
                f"录音完成，峰值 {peak}（{'有声音' if peak > 100 else '几乎静音'}）"
            )
        except Exception as exc:
            QMessageBox.critical(self, "录音失败", str(exc))

    def _test_play(self):
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            QMessageBox.warning(self, "提示", "缺少 numpy/sounddevice，无法测试播放")
            return
        index = self.output_device_combo.currentIndex()
        if index < 0:
            QMessageBox.warning(self, "提示", "请先扫描并选择扬声器")
            return
        device_id = self.output_device_combo.itemData(index)
        try:
            sample_rate = 16000
            duration = 1.0
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            tone = (0.25 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
            sd.play(tone, sample_rate, device=device_id)
            sd.wait()
            self.audio_status_text.setPlainText("测试音播放完成")
        except Exception as exc:
            QMessageBox.critical(self, "播放失败", str(exc))

    def _apply_shortcuts(self):
        try:
            data = self._build_config()
            shortcuts = data.setdefault("SHORTCUTS", {})
            for key, (modifier, key_combo) in self.shortcut_widgets.items():
                shortcuts[key] = {
                    "modifier": modifier.currentText().lower(),
                    "key": key_combo.currentText().lower(),
                    "description": shortcuts.get(key, {}).get("description", ""),
                }
            AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with AI_CONFIG_PATH.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            self.config = data
            self.notify("快捷键配置已应用（窗口内生效，重启后仍保留）")
        except Exception as exc:
            QMessageBox.critical(self, "应用失败", str(exc))


class AiActivationPage(PageBase):
    def __init__(self, window=None, embedded=False):
        super().__init__("软件激活", None, window, show_header=not embedded)
        self.setObjectName("activationPage")
        self.win = window
        if embedded:
            self.root.setContentsMargins(18, 18, 18, 18)

        self.status_label = QLabel("●  正在检查授权")
        self.status_label.setStyleSheet("QLabel { color:#86909C; }")
        if embedded:
            self.status_label.setAlignment(Qt.AlignCenter)
            self.root.addWidget(self.status_label)
        else:
            self.add_action(self.status_label)

        device_group = make_group("设备信息")
        device_grid = QGridLayout(device_group)
        device_grid.setContentsMargins(14, 18, 14, 14)
        device_grid.setHorizontalSpacing(24)
        device_grid.setVerticalSpacing(8)
        device_grid.addWidget(QLabel("机器码"), 0, 0)
        device_grid.addWidget(QLabel("授权状态"), 0, 1)
        self.machine_value = QLabel(self._machine_code())
        self.machine_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.machine_value.setStyleSheet(
            "QLabel { font-family:Consolas, Microsoft YaHei UI; }"
        )
        self.license_value = QLabel("检查中")
        self.license_value.setStyleSheet(
            "QLabel { font-family:Consolas, Microsoft YaHei UI; }"
        )
        device_grid.addWidget(self.machine_value, 1, 0)
        device_grid.addWidget(self.license_value, 1, 1)
        copy_machine = make_btn("复制机器码", small=True)
        copy_machine.clicked.connect(self._copy_machine_code)
        device_grid.addWidget(copy_machine, 2, 0, 1, 2)
        self.root.addWidget(device_group)

        code_group = make_group("激活验证码")
        code_row = QHBoxLayout(code_group)
        code_row.setContentsMargins(14, 18, 14, 14)
        code_row.setSpacing(12)
        self.code_edit = make_field("")
        self.code_edit.setPlaceholderText("粘贴激活验证码")
        self.code_edit.setAlignment(Qt.AlignCenter)
        self.code_edit.setStyleSheet(
            "QLineEdit { font-family:Consolas, Microsoft YaHei UI;"
            " font-size:11pt; letter-spacing:1px; }"
        )
        code_row.addWidget(self.code_edit, 1)
        activate_btn = make_btn("激活", primary=True)
        activate_btn.clicked.connect(self._activate)
        code_row.addWidget(activate_btn)
        self.root.addWidget(code_group)

        hint = QLabel("授权码由销售商提供，只用于激活本软件，与小智设备激活无关。")
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel { color:#86909C; font-size:9pt; }")
        self.root.addWidget(hint)
        self.root.addStretch(1)
        self.refresh()

    def _machine_code(self):
        value = getattr(self.win, "machine_code", "") if self.win is not None else ""
        return str(value or "当前环境不可用")

    def refresh(self):
        if self.win is None or not hasattr(self.win, "license_status"):
            return
        try:
            status = self.win.license_status()
        except Exception as exc:
            self._set_status(False, f"授权检查失败: {exc}")
            return
        activated = bool(status.get("activated"))
        self.license_value.setText(status.get("message", "未知状态"))
        self.machine_value.setText(self._machine_code())
        self._set_status(activated, status.get("message", "未知状态"))

    def _set_status(self, activated, message):
        if activated:
            self.status_label.setText("●  已激活")
            self.status_label.setStyleSheet("QLabel { color:#00B42A; }")
        else:
            self.status_label.setText("●  未激活")
            self.status_label.setStyleSheet("QLabel { color:#F53F3F; }")
        self.status_label.setToolTip(message)

    def _copy_machine_code(self):
        QApplication.clipboard().setText(self._machine_code())
        QMessageBox.information(self, "已复制", "机器码已复制到剪贴板。")

    def _activate(self):
        if self.win is None or not hasattr(self.win, "activate_license"):
            QMessageBox.warning(self, "无法激活", "当前窗口未初始化授权服务。")
            return
        code = self.code_edit.text().strip()
        if not code:
            QMessageBox.warning(self, "请输入激活码", "激活验证码不能为空。")
            return
        result = self.win.activate_license(code)
        if result.get("valid"):
            self.code_edit.clear()
            QMessageBox.information(
                self, "激活成功", result.get("message", "激活成功")
            )
            self.refresh()
        else:
            QMessageBox.critical(
                self, "激活失败", result.get("message", "激活失败")
            )


def open_license_dialog(window, parent=None):
    """软件激活：机器码 + 授权码，和硬件/小智设备激活完全分开。"""
    page = AiActivationPage(window, embedded=True)
    page.refresh()

    dlg = QDialog(parent)
    dlg.setObjectName("licenseDialog")
    dlg.setWindowTitle("软件激活")
    dlg.setModal(True)
    dlg.resize(520, 400)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(page)
    footer = QHBoxLayout()
    footer.setContentsMargins(18, 4, 18, 14)
    footer.addStretch(1)
    close_btn = make_btn("关闭")
    close_btn.clicked.connect(dlg.accept)
    footer.addWidget(close_btn)
    layout.addLayout(footer)
    dlg.exec_()
    return dlg


class AiDeviceActivationPage(PageBase):
    """设备绑定：沿用原版界面项（序列号/MAC/验证码+复制/跳转），状态以服务器为准。"""

    bind_result = pyqtSignal(object)

    def __init__(self, window=None, embedded=False):
        super().__init__("设备绑定", None, window, show_header=not embedded)
        self.setObjectName("deviceActivationPage")
        self.win = window
        self._querying = False
        self._auth_url = "https://xiaozhi.me/"
        self.bind_result.connect(self._on_bind_result)
        if embedded:
            self.root.setContentsMargins(18, 12, 18, 12)

        self.status_label = QLabel("●  检查中...")
        self.status_label.setStyleSheet("QLabel { color:#86909C; }")
        if embedded:
            self.status_label.setAlignment(Qt.AlignCenter)
            self.root.addWidget(self.status_label)
        else:
            self.add_action(self.status_label)

        # 原版界面项 1：设备序列号 + MAC 地址
        info_group = make_group("设备信息")
        info_grid = QGridLayout(info_group)
        info_grid.setContentsMargins(14, 18, 14, 14)
        info_grid.setHorizontalSpacing(18)
        info_grid.setVerticalSpacing(8)
        self.serial_value = self._info_label()
        self.mac_value = self._info_label()
        info_grid.addWidget(QLabel("设备序列号"), 0, 0)
        info_grid.addWidget(self.serial_value, 0, 1)
        info_grid.addWidget(QLabel("MAC地址"), 1, 0)
        info_grid.addWidget(self.mac_value, 1, 1)
        info_grid.setColumnStretch(1, 1)
        self.root.addWidget(info_group)

        # 原版界面项 2：验证码大字显示 + 复制
        code_group = make_group("绑定验证码")
        code_row = QHBoxLayout(code_group)
        code_row.setContentsMargins(14, 18, 14, 14)
        code_row.setSpacing(12)
        self.code_value = QLabel("—")
        self.code_value.setAlignment(Qt.AlignCenter)
        self.code_value.setMinimumHeight(38)
        self.code_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_value.setStyleSheet(
            "QLabel { font-family:Consolas, Microsoft YaHei UI; font-size:16pt;"
            " font-weight:600; letter-spacing:2px; color:#F53F3F;"
            " background:#FFFFFF; border:1px solid #E5E6EB; border-radius:3px; }"
        )
        code_row.addWidget(self.code_value, 1)
        copy_btn = make_btn("复制", small=True)
        copy_btn.clicked.connect(self._copy_code)
        code_row.addWidget(copy_btn)
        self.root.addWidget(code_group)

        # 原版界面项 3：跳转 xiaozhi.me 绑定
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.refresh_btn = make_btn("刷新状态", small=True)
        self.refresh_btn.clicked.connect(self.refresh)
        action_row.addWidget(self.refresh_btn)
        self.jump_btn = make_btn("跳转 xiaozhi.me 绑定", primary=True)
        self.jump_btn.clicked.connect(self._open_binding_site)
        action_row.addWidget(self.jump_btn, 1)
        self.root.addLayout(action_row)

        hint = QLabel(
            "验证码由小智服务器下发，在 xiaozhi.me 登录后输入即可绑定；"
            "官网解绑后点“刷新状态”，这里会重新显示验证码。\n"
            "本页的序列号与「软件激活」里的机器码、授权码无关。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel { color:#86909C; font-size:9pt; }")
        self.root.addWidget(hint)
        self.root.addStretch(1)
        self.refresh()

    @staticmethod
    def _info_label():
        label = QLabel("—")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(
            "QLabel { font-family:Consolas, Microsoft YaHei UI; color:#1F2937; }"
        )
        return label

    def _config(self):
        return _load_json(AI_CONFIG_PATH)

    def _efuse(self):
        return _load_json(XIAOZHI_HOME / "config" / "efuse.json")

    def _set_status(self, text, color, tooltip=""):
        self.status_label.setText(f"●  {text}")
        self.status_label.setStyleSheet(f"QLabel {{ color:{color}; }}")
        self.status_label.setToolTip(tooltip or text)

    def refresh(self):
        config = self._config()
        efuse = self._efuse()
        system = config.get("SYSTEM_OPTIONS", {}) if isinstance(config, dict) else {}
        network = system.get("NETWORK", {}) if isinstance(system, dict) else {}
        self.serial_value.setText(str(efuse.get("serial_number") or "未生成"))
        self.mac_value.setText(
            str(system.get("DEVICE_ID") or efuse.get("mac_address") or "未配置")
        )
        self._auth_url = str(
            network.get("AUTHORIZATION_URL") or "https://xiaozhi.me/"
        )
        self._set_status("检查中...", "#86909C", "正在查询小智服务器的绑定状态")
        if not self._querying:
            QTimer.singleShot(0, self._query_server)

    def _query_server(self):
        if self._querying:
            return
        self._querying = True
        self.refresh_btn.setEnabled(False)
        self._set_status("正在查询服务器绑定状态...", "#86909C")
        threading.Thread(target=self._query_worker, daemon=True).start()

    def _query_worker(self):
        try:
            result = self._fetch_binding_status()
        except Exception as exc:
            result = {"ok": False, "message": f"查询失败：{exc}"}
        self.bind_result.emit(result)

    def _fetch_binding_status(self):
        """按原版 ota.py 的请求格式查询：有 activation.code = 未绑定，无 activation = 已绑定。"""
        config = self._config()
        system = config.get("SYSTEM_OPTIONS", {}) if isinstance(config, dict) else {}
        network = system.get("NETWORK", {}) if isinstance(system, dict) else {}
        mac = str(system.get("DEVICE_ID") or "")
        client_id = str(system.get("CLIENT_ID") or "")
        ota_url = str(network.get("OTA_VERSION_URL") or "").strip()
        if not ota_url:
            raise ValueError("未配置 OTA_VERSION_URL")
        efuse = self._efuse()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                local_ip = sock.getsockname()[0]
        except OSError:
            local_ip = "127.0.0.1"
        payload = {
            "application": {
                "version": "2.0.0",
                "elf_sha256": str(efuse.get("hmac_key") or "unknown"),
            },
            "board": {
                "type": "bread-compact-wifi",
                "name": "py-xiaozhi",
                "ip": local_ip,
                "mac": mac,
            },
        }
        headers = {
            "Device-Id": mac,
            "Client-Id": client_id,
            "Content-Type": "application/json",
            "User-Agent": "bread-compact-wifi/py-xiaozhi-2.0.0",
            "Accept-Language": "zh-CN",
        }
        if str(network.get("ACTIVATION_VERSION") or "") == "v2":
            headers["Activation-Version"] = "2.0.0"

        import requests
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        response = requests.post(
            ota_url, headers=headers, json=payload, timeout=10, verify=False
        )
        if response.status_code != 200:
            raise ValueError(f"OTA 服务器返回 HTTP {response.status_code}")
        data = response.json()
        activation = data.get("activation") if isinstance(data, dict) else None
        if isinstance(activation, dict) and activation.get("code"):
            return {"ok": True, "bound": False, "code": str(activation["code"])}
        return {"ok": True, "bound": True, "code": ""}

    def _on_bind_result(self, result):
        self._querying = False
        self.refresh_btn.setEnabled(True)
        if not isinstance(result, dict) or not result.get("ok"):
            message = str(result.get("message") or "未知错误") if isinstance(result, dict) else "未知错误"
            self.code_value.setText("—")
            self._set_status("查询失败", "#FF7D00", message)
            return
        bound = bool(result.get("bound"))
        code = str(result.get("code") or "").strip()
        self._save_local_status(bound)
        if bound:
            self.code_value.setText("—")
            self._set_status("已绑定", "#00B42A", "服务器显示该设备已绑定到账号")
        else:
            self.code_value.setText(code or "—")
            self._set_status(
                "未绑定", "#F53F3F",
                f"请到 xiaozhi.me 输入验证码 {code}" if code else "请在官网查看验证码",
            )

    def _save_local_status(self, bound):
        """服务器是准绳：官网解绑后同步本地 efuse，避免界面继续显示已绑定。"""
        path = XIAOZHI_HOME / "config" / "efuse.json"
        data = self._efuse()
        if not isinstance(data, dict) or not data:
            return
        if bool(data.get("activation_status")) == bool(bound):
            return
        data["activation_status"] = bool(bound)
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)
        except OSError:
            pass

    def _copy_code(self):
        value = self.code_value.text().strip()
        if value in {"", "—"}:
            QMessageBox.information(
                self, "没有验证码", "当前没有验证码：设备可能已绑定，或服务器查询失败。"
            )
            return
        QApplication.clipboard().setText(value)
        QMessageBox.information(self, "已复制", "绑定验证码已复制到剪贴板。")

    def _open_binding_site(self):
        QDesktopServices.openUrl(QUrl(getattr(self, "_auth_url", "https://xiaozhi.me/")))


def open_device_activation_dialog(window, parent=None):
    """设备绑定小弹窗：沿用原版界面项，只处理小智设备绑定，不碰软件授权。"""
    page = AiDeviceActivationPage(window, embedded=True)
    page.refresh()

    dlg = QDialog(parent)
    dlg.setObjectName("deviceActivationDialog")
    dlg.setWindowTitle("设备绑定")
    dlg.setModal(True)
    dlg.resize(600, 420)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(page)
    footer = QHBoxLayout()
    footer.setContentsMargins(18, 2, 18, 14)
    footer.addStretch(1)
    close_btn = make_btn("关闭")
    close_btn.clicked.connect(dlg.accept)
    footer.addWidget(close_btn)
    layout.addLayout(footer)
    dlg.exec_()
    return dlg

