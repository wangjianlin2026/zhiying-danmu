import math
import re
import time
from datetime import datetime

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dialogs import (
    DeleteDeviceDialog,
    DeviceDialog,
    TextEditDialog,
    TopicEditDialog,
)
from data_access import (
    load_ai_barrage_settings,
    load_barrage_categories,
    load_barrage_interval_settings,
    load_barrages,
    load_category_settings,
    load_connection_settings,
    load_devices,
    load_filter_keywords,
    load_filter_states,
    load_keyboard_settings,
    load_like_dedup_settings,
    load_message_interval_settings,
    load_number_patterns,
    load_pressure_menu_data,
    load_priority_settings,
    load_topics_data,
    save_ai_barrage_settings,
    save_barrage_interval_settings,
    save_category_settings,
    save_connection_settings,
    save_devices,
    save_filter_keywords,
    save_filter_states,
    save_keyboard_settings,
    save_like_dedup_settings,
    save_message_interval_settings,
    save_number_patterns,
    save_pressure_menu_data,
    save_priority_settings,
    save_topics_data,
)
from services.category_manager import (
    CATEGORY_FOR_TYPE,
    MAX_CATEGORY_BODY_CHARS,
    category_message_fits,
    rendered_category_length,
)
from services.pressure_manager import MAX_PRESSURE_CHARS
from services.topic_manager import (
    ALL_TOPIC_CATEGORIES,
    DEFAULT_TOPIC_CATEGORY,
    MAX_TOPIC_CHARS,
    TOPIC_CATEGORIES,
)
from widgets import (
    PageBase,
    label,
    make_btn,
    make_check,
    make_combo,
    make_field,
    make_group,
    make_list,
    make_scroll,
    make_table,
    make_text_edit,
    row,
)


TYPE_LABELS = {
    "message": "互动",
    "like": "点赞",
    "social": "关注",
    "follow": "关注",
    "gift": "礼物",
    "fansclub": "粉丝团",
    "share": "分享",
    "member": "进入直播间",
    "enter": "进入直播间",
    "manual": "手动",
    "keyboard_input": "键盘输入",
}
STATE_LABELS = {
    "offline": "离线",
    "idle": "空闲",
    "listening": "监听中",
    "speaking": "说话中",
    "waiting_ack": "等待确认",
    "error": "错误",
}
STATE_COLORS = {
    "offline": "#94A3B8",
    "idle": "#15803D",
    "listening": "#15803D",
    "speaking": "#2563EB",
    "waiting_ack": "#D97706",
    "error": "#DC2626",
}


def _form_row(*items):
    return row(*items, spacing=8)


def _add_group(layout, title, stretch=0):
    group = make_group(title)
    layout.addWidget(group, stretch)
    return group


def _set_group_layout(group):
    body = QVBoxLayout(group)
    body.setContentsMargins(12, 12, 12, 12)
    body.setSpacing(8)
    return body


def _group_title_label(title):
    widget = QLabel(title)
    widget.setStyleSheet(
        "QLabel { background:#111827; color:#FFFFFF; font-size:10.5pt;"
        " font-weight:700; border-radius:5px; padding:3px 10px; }"
    )
    widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return widget


def _make_group_header(title):
    """组外标题行：标题在左，开关等操作由调用方追加到右侧。"""
    wrapper = QWidget()
    outer = QVBoxLayout(wrapper)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(4)
    header = QHBoxLayout()
    header.setContentsMargins(2, 0, 2, 0)
    header.setSpacing(10)
    header.addWidget(_group_title_label(title))
    header.addStretch(1)
    outer.addLayout(header)
    return wrapper, outer, header


def _wrap_group_with_header(group, title, controls):
    wrapper, outer, header = _make_group_header(title)
    for control in controls:
        header.addWidget(control)
    group.setTitle("")
    group.setStyleSheet("QGroupBox#sourceGroup { margin-top: 0px; }")
    outer.addWidget(group, 1)
    return wrapper


def _strip_list_text(text):
    text = re.sub(r"^\s*\d+\.\s*", "", str(text))
    if text.endswith(" ← 下一个"):
        text = text[: -len(" ← 下一个")]
    return text.strip()


def _int_value(text, default=0, minimum=0):
    try:
        return max(minimum, int(float(str(text).strip())))
    except (TypeError, ValueError):
        return default


def _float_value(text, default=0.0, minimum=0.0):
    try:
        return max(minimum, float(str(text).strip()))
    except (TypeError, ValueError):
        return default


class MainPage(PageBase):
    def __init__(self, window):
        super().__init__("直播总览", None, window)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(16)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left_column = QVBoxLayout()
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(16)
        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(16)
        body.addLayout(columns, 1)

        license_group = make_group("授权状态")
        license_layout = QGridLayout(license_group)
        license_layout.setContentsMargins(12, 14, 12, 12)
        license_layout.setHorizontalSpacing(10)
        license_layout.setVerticalSpacing(9)
        license_layout.addWidget(QLabel("机器码:"), 0, 0)
        self.machine_value = QLabel("正在获取...")
        self.machine_value.setStyleSheet(
            "QLabel { background:#F8FAFC; border:1px solid #E2E8F0; padding:5px 8px; }"
        )
        self.machine_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        license_layout.addWidget(self.machine_value, 0, 1)
        license_layout.addWidget(
            make_btn("复制机器码", on_click=self._copy_machine_code), 0, 2
        )
        license_layout.addWidget(QLabel("授权状态:"), 1, 0)
        self.license_status = QLabel("未授权 - 程序无法使用-联系: 销售商")
        self.license_status.setStyleSheet("QLabel { color:#DC2626; }")
        license_layout.addWidget(self.license_status, 1, 1, 1, 2)
        license_layout.addWidget(QLabel("激活信息:"), 2, 0)
        self.activation_info = QLabel("未激活")
        self.activation_info.setStyleSheet("QLabel { color:#64748B; }")
        license_layout.addWidget(self.activation_info, 2, 1, 1, 2)
        license_layout.addWidget(QLabel("授权码:"), 3, 0)
        self.license_edit = make_field()
        license_layout.addWidget(self.license_edit, 3, 1)
        license_layout.addWidget(
            make_btn("激活授权", on_click=self._activate_license), 3, 2
        )
        license_layout.setColumnStretch(1, 1)
        left_column.addWidget(license_group)

        device_list_group = make_group("设备列表")
        device_list_layout = QVBoxLayout(device_list_group)
        device_list_layout.setContentsMargins(12, 14, 12, 12)
        device_list_layout.setSpacing(8)
        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        self.scan_btn = make_btn(
            "扫描设备", primary=True, on_click=self._scan_devices
        )
        scan_row.addWidget(self.scan_btn)
        self.scan_result = QLabel("点击“扫描设备”查找局域网内的小智")
        self.scan_result.setStyleSheet("QLabel { color:#2563EB; }")
        self.scan_result.setWordWrap(True)
        scan_row.addWidget(self.scan_result, 1)
        device_list_layout.addLayout(scan_row)
        self.device_table = QTableWidget(0, 5)
        self.device_table.setHorizontalHeaderLabels(
            ["设备", "IP地址", "MAC", "状态", "操作"]
        )
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.device_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.device_table.setSelectionMode(QTableWidget.SingleSelection)
        self.device_table.setShowGrid(False)
        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.device_table.setColumnWidth(0, 120)
        self.device_table.setColumnWidth(3, 68)
        self.device_table.setColumnWidth(4, 64)
        self.device_table.verticalHeader().setDefaultSectionSize(34)
        device_list_layout.addWidget(self.device_table, 1)
        right_column.addWidget(device_list_group, 1)

        status_group = QFrame()
        status_group.setObjectName("sourceGroup")
        status_group.setStyleSheet(
            "QFrame#sourceGroup { background:#FFFFFF;"
            " border:1px solid #E3E8EF; border-radius:10px; }"
        )
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(26, 12, 26, 12)
        status_layout.setSpacing(8)
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.state_dot = QLabel("●")
        self.state_dot.setStyleSheet("QLabel { color:#94A3B8; }")
        state_row.addWidget(self.state_dot)
        self.state_text = QLabel("离线")
        state_row.addWidget(self.state_text)
        state_row.addStretch(1)
        self.latency_label = QLabel("延迟: -- ms")
        state_row.addWidget(self.latency_label)
        send_row = QHBoxLayout()
        send_row.setSpacing(8)
        send_row.addWidget(QLabel("发送:"))
        self.send_edit = make_field()
        send_row.addWidget(self.send_edit, 1)
        self.send_btn = make_btn("发送", on_click=self._send_text)
        self.send_btn.setEnabled(False)
        send_row.addWidget(self.send_btn)
        status_layout.addLayout(send_row)

        title_label = QLabel("给小智发语音")
        title_label.setStyleSheet(
            "QLabel { background:#111827; color:#FFFFFF;"
            " font-size:10.5pt; font-weight:700; border-radius:5px;"
            " padding:3px 10px; }"
        )
        title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        status_area = QWidget()
        status_area_layout = QVBoxLayout(status_area)
        status_area_layout.setContentsMargins(0, 0, 0, 0)
        status_area_layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        title_row.addLayout(state_row)
        status_area_layout.addLayout(title_row)
        status_area_layout.addWidget(status_group)
        left_column.addWidget(status_area)

        self.topic_control_group = self._build_topic_control_group()
        self.pressure_control_group = self._build_pressure_control_group()
        self.test_send_group = self._build_test_send_group()
        left_column.addWidget(self.test_send_group)

        merged_status_group = make_group("设备状态")
        merged_status_layout = QVBoxLayout(merged_status_group)
        merged_status_layout.setContentsMargins(12, 14, 12, 12)
        merged_status_layout.setSpacing(8)

        device_head = QHBoxLayout()
        device_head.setSpacing(8)
        self.device_state_label = QLabel("未连接")
        device_head.addWidget(self.device_state_label)
        self.device_icon = QLabel("📴")
        device_head.addWidget(self.device_icon)
        device_head.addSpacing(12)
        self.status_count = QLabel("")
        self.status_count.setMinimumHeight(20)
        device_head.addWidget(self.status_count)
        device_head.addSpacing(12)
        self.connection_time = QLabel("")
        self.connection_time.setMinimumHeight(20)
        device_head.addWidget(self.connection_time)

        self.device_wait_label = QLabel("等待连接...")

        channel_status_row = QHBoxLayout()
        channel_status_row.setSpacing(16)
        self.hw_dot, self.hw_status_label = self._make_channel_item(
            channel_status_row, "硬件"
        )
        self.sw_dot, self.sw_status_label = self._make_channel_item(
            channel_status_row, "软件"
        )
        self.barrage_dot, self.barrage_status_label = self._make_channel_item(
            channel_status_row, "弹幕"
        )
        channel_status_row.addStretch(1)
        merged_status_layout.addLayout(channel_status_row)

        channel_row = QHBoxLayout()
        channel_row.setSpacing(8)
        channel_row.addWidget(label("语音通道:", "muted"))
        self.hw_voice_btn = make_btn(
            "🔊 硬件喇叭", small=True,
            on_click=lambda: self._set_output_channel("hardware"),
        )
        self.sw_voice_btn = make_btn(
            "💻 软件语音", small=True,
            on_click=lambda: self._set_output_channel("software"),
        )
        channel_row.addWidget(self.hw_voice_btn)
        channel_row.addWidget(self.sw_voice_btn)
        self.hw_voice_btn.setToolTip("切到硬件喇叭，并断开软件小智连接")
        self.sw_voice_btn.setToolTip("切到软件语音，并连接软件小智")
        channel_row.addStretch(1)
        channel_row.addWidget(self.device_wait_label)
        channel_row.addSpacing(12)
        self.last_update = QLabel("")
        self.last_update.setMinimumHeight(20)
        channel_row.addWidget(self.last_update)
        merged_status_layout.addLayout(channel_row)

        monitor_overview_group = make_group("弹幕监听")
        monitor_overview_layout = QVBoxLayout(monitor_overview_group)
        monitor_overview_layout.setContentsMargins(12, 14, 12, 12)
        monitor_overview_layout.setSpacing(9)
        monitor_overview_row = QHBoxLayout()
        monitor_overview_row.setSpacing(8)
        self.overview_start_monitor_btn = make_btn(
            "开始监控", on_click=self._start_overview_monitor
        )
        self.overview_stop_monitor_btn = make_btn(
            "停止监控", on_click=self._stop_overview_monitor
        )
        self.overview_stop_monitor_btn.setEnabled(False)
        monitor_overview_row.addWidget(self.overview_start_monitor_btn)
        monitor_overview_row.addWidget(self.overview_stop_monitor_btn)
        monitor_overview_row.addStretch(1)
        monitor_overview_layout.addLayout(monitor_overview_row)
        self.overview_monitor_label = QLabel("未开始监控")
        self.overview_monitor_label.setStyleSheet("QLabel { color:#64748B; }")
        monitor_overview_layout.addWidget(self.overview_monitor_label)

        collector_overview_group = make_group("本地服务")
        collector_overview_layout = QVBoxLayout(collector_overview_group)
        collector_overview_layout.setContentsMargins(12, 14, 12, 12)
        collector_overview_layout.setSpacing(9)
        location_overview_row = QHBoxLayout()
        location_overview_row.setSpacing(8)
        location_overview_row.addWidget(QLabel("监听地址："))
        location_overview = QLabel("ws://0.0.0.0:8888")
        location_overview.setStyleSheet("QLabel { color:#DC2626; }")
        location_overview_row.addWidget(location_overview)
        location_overview_row.addSpacing(24)
        thanks_overview = QLabel("* 感谢点小心心的你，好人一生平安~")
        thanks_overview.setStyleSheet("QLabel { color:#C026D3; }")
        location_overview_row.addWidget(thanks_overview)
        location_overview_row.addStretch(1)
        collector_overview_layout.addLayout(location_overview_row)
        status_overview_row = QHBoxLayout()
        status_overview_row.setSpacing(8)
        status_overview_row.addWidget(QLabel("服务状态："))
        self.overview_service_status = QLabel("未启动")
        self.overview_service_status.setStyleSheet("QLabel { color:#64748B; }")
        status_overview_row.addWidget(self.overview_service_status)
        status_overview_row.addSpacing(12)
        self.overview_restart_btn = make_btn(
            "重启", on_click=self._restart_collector
        )
        self.overview_restart_btn.setEnabled(False)
        status_overview_row.addWidget(self.overview_restart_btn)
        status_overview_row.addStretch(1)
        collector_overview_layout.addLayout(status_overview_row)

        left_column.addWidget(self.pressure_control_group)
        left_column.addWidget(self.topic_control_group, 1)

        device_status_area = QWidget()
        device_status_area_layout = QVBoxLayout(device_status_area)
        device_status_area_layout.setContentsMargins(0, 0, 0, 0)
        device_status_area_layout.setSpacing(4)
        status_header = QHBoxLayout()
        status_header.setContentsMargins(2, 0, 2, 0)
        status_header.setSpacing(10)
        status_header.addWidget(_group_title_label("设备状态"))
        status_header.addStretch(1)
        status_header.addLayout(device_head)
        device_status_area_layout.addLayout(status_header)
        merged_status_group.setTitle("")
        merged_status_group.setStyleSheet(
            "QGroupBox#sourceGroup { margin-top: 0px; }"
        )
        device_status_area_layout.addWidget(merged_status_group)
        right_column.addWidget(device_status_area)
        right_column.addWidget(collector_overview_group)
        right_column.addWidget(monitor_overview_group)
        columns.addLayout(left_column, 1)
        columns.addLayout(right_column, 1)

        self._collector_running = False
        self._collector_ws_ready = False
        self._barrage_monitor_running = False
        self._barrage_ws_connected = False
        self._connected_host = ""
        self._refresh_barrage_channel()


        monitoring = bool(getattr(window, "monitoring", False))
        self.on_monitor_status(
            monitoring,
            "弹幕监控运行中" if monitoring else "未开始监控",
        )
        self.root.addWidget(make_scroll(content), 1)
        self._reload_devices()
        self.refresh()
        self._control_status_timer = QTimer(self)
        self._control_status_timer.setInterval(1000)
        self._control_status_timer.timeout.connect(
            self._update_topic_control_status
        )
        self._control_status_timer.timeout.connect(
            self._update_pressure_control_status
        )
        self._control_status_timer.start()

    def _collector_page(self):
        return getattr(self.win, "pages", {}).get("collector") if self.win else None

    def _build_topic_control_group(self):
        data = load_topics_data()
        interval_value = _int_value(data.get("interval", 30), 30, 1)
        send_mode = str(data.get("send_mode", "sequential"))
        no_repeat_value = _int_value(
            data.get("no_repeat_seconds", 300), 300, 1
        )
        categories = list(data.get("categories", TOPIC_CATEGORIES))
        active_category = str(
            data.get("active_category", ALL_TOPIC_CATEGORIES)
            or ALL_TOPIC_CATEGORIES
        )
        if active_category not in [ALL_TOPIC_CATEGORIES] + categories:
            active_category = ALL_TOPIC_CATEGORIES

        group = make_group("话题设置")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.topic_enable_check = make_check("启用话题定时发送")
        self.topic_enable_check.toggled.connect(self._toggle_topic_timer)
        layout.addWidget(QLabel("发送间隔(秒):"), 0, 0)
        self.topic_interval_edit = make_field(str(interval_value), 90)
        layout.addWidget(self.topic_interval_edit, 0, 1)
        layout.addWidget(
            make_btn("修改", on_click=self._apply_topic_interval), 0, 2
        )
        layout.addWidget(QLabel("发送模式:"), 0, 3)
        self.topic_mode_combo = make_combo(
            ["按顺序", "随机"],
            "按顺序" if send_mode == "sequential" else "随机",
            120,
        )
        layout.addWidget(self.topic_mode_combo, 0, 4)
        self.topic_mode_desc = QLabel(
            "(按顺序)" if send_mode == "sequential" else "(随机)"
        )
        self.topic_mode_desc.setStyleSheet("QLabel { color:#2563EB; }")
        layout.addWidget(self.topic_mode_desc, 0, 5)
        self.audio_label = QLabel("音频: 检测中...")
        layout.addWidget(self.audio_label, 0, 6)

        self.topic_status_label = QLabel("未启用 | 加载中...")
        self.topic_status_label.setStyleSheet("QLabel { color:#64748B; }")

        layout.addWidget(QLabel("当前话题分类:"), 1, 0)
        self.topic_category_combo = make_combo(
            [("全部分类", ALL_TOPIC_CATEGORIES)]
            + [(category, category) for category in categories],
            active_category,
            150,
        )
        layout.addWidget(self.topic_category_combo, 1, 1, 1, 2)
        self.topic_category_desc = QLabel("")
        self.topic_category_desc.setStyleSheet("QLabel { color:#64748B; }")
        layout.addWidget(self.topic_category_desc, 1, 3, 1, 4)

        layout.addWidget(QLabel("手动发送话题:"), 2, 0)
        self.manual_topic_combo = make_combo([])
        self.manual_topic_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.manual_topic_combo.setMinimumContentsLength(12)
        self.manual_topic_combo.setMinimumWidth(0)
        self.manual_topic_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        layout.addWidget(self.manual_topic_combo, 2, 1, 1, 5)
        self.manual_topic_send_btn = make_btn(
            "发送", on_click=self._send_manual_topic
        )
        layout.addWidget(self.manual_topic_send_btn, 2, 6)

        layout.addWidget(QLabel("随机不重复(秒):"), 3, 0)
        self.topic_no_repeat_edit = make_field(str(no_repeat_value), 90)
        layout.addWidget(self.topic_no_repeat_edit, 3, 1)
        layout.addWidget(
            make_btn("应用", on_click=self._apply_topic_no_repeat), 3, 2
        )
        no_repeat_hint = QLabel(
            "随机模式下，这段时间内不会重复同一个话题"
        )
        no_repeat_hint.setStyleSheet("QLabel { color:#64748B; }")
        layout.addWidget(no_repeat_hint, 3, 3, 1, 4)
        layout.setColumnStretch(1, 1)

        self.topic_mode_combo.currentTextChanged.connect(
            self._on_topic_mode_changed
        )
        self.topic_category_combo.currentTextChanged.connect(
            self._on_topic_category_changed
        )
        self._fill_manual_topic_combo()
        self._sync_topic_category_desc()
        self._update_topic_control_status()
        return _wrap_group_with_header(
            group,
            "话题设置",
            [self.topic_enable_check, self.topic_status_label],
        )

    def _build_pressure_control_group(self):
        data = load_pressure_menu_data()
        interval_value = _int_value(data.get("interval", 60), 60, 1)
        send_mode = str(data.get("send_mode", "sequential"))

        group = make_group("逼单设置")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.pressure_enable_check = make_check("启用逼单定时发送")
        self.pressure_enable_check.toggled.connect(
            self._toggle_pressure_timer
        )
        layout.addWidget(QLabel("发送间隔(秒):"), 0, 0)
        self.pressure_interval_edit = make_field(str(interval_value), 90)
        layout.addWidget(self.pressure_interval_edit, 0, 1)
        layout.addWidget(
            make_btn("修改", on_click=self._apply_pressure_interval), 0, 2
        )
        layout.addWidget(QLabel("发送模式:"), 0, 3)
        self.pressure_mode_combo = make_combo(
            ["按顺序", "随机"],
            "按顺序" if send_mode == "sequential" else "随机",
            120,
        )
        layout.addWidget(self.pressure_mode_combo, 0, 4)
        self.pressure_mode_desc = QLabel(
            "(按顺序)" if send_mode == "sequential" else "(随机)"
        )
        self.pressure_mode_desc.setStyleSheet("QLabel { color:#2563EB; }")
        layout.addWidget(self.pressure_mode_desc, 0, 5)
        self.pressure_status_label = QLabel("未启用")

        layout.addWidget(QLabel("手动发送逼单消息:"), 1, 0)
        self.manual_pressure_combo = make_combo([])
        self.manual_pressure_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.manual_pressure_combo.setMinimumContentsLength(12)
        self.manual_pressure_combo.setMinimumWidth(0)
        self.manual_pressure_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        layout.addWidget(self.manual_pressure_combo, 1, 1, 1, 5)
        self.manual_pressure_send_btn = make_btn(
            "发送", on_click=self._send_manual_pressure
        )
        layout.addWidget(self.manual_pressure_send_btn, 1, 6)
        layout.setColumnStretch(1, 1)

        self.pressure_mode_combo.currentTextChanged.connect(
            self._on_pressure_mode_changed
        )
        self._fill_manual_pressure_combo()
        self._update_pressure_control_status()
        return _wrap_group_with_header(
            group,
            "逼单设置",
            [self.pressure_enable_check, self.pressure_status_label],
        )

    def _build_test_send_group(self):
        settings = load_category_settings()
        manager = getattr(self.win, "category_manager", None)
        names = manager.category_names() if manager is not None else {}
        choices = [
            (str(name), str(key))
            for key, name in names.items()
            if key in CATEGORY_FOR_TYPE
        ]
        if not choices:
            choices = [
                ("点赞", "like"),
                ("关注", "follow"),
                ("礼物", "gift"),
                ("粉丝团", "fansclub"),
                ("分享", "share"),
                ("进入直播间", "enter"),
                ("互动", "message"),
            ]

        group = make_group("测试发送")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(8)

        layout.addWidget(QLabel("消息分类:"), 0, 0)
        self.test_category_combo = make_combo(
            choices, str(settings.get("test_category", "like")), 110
        )
        layout.addWidget(self.test_category_combo, 0, 1)
        layout.addWidget(QLabel("用户名:"), 0, 2)
        self.test_username = make_field(
            str(settings.get("test_username", "测试用户")), 100
        )
        layout.addWidget(self.test_username, 0, 3)
        layout.addWidget(QLabel("数量:"), 0, 4)
        self.test_count = make_field(
            str(settings.get("test_count", "1")), 55
        )
        layout.addWidget(self.test_count, 0, 5)
        layout.addWidget(QLabel("礼物名:"), 0, 6)
        self.test_gift = make_field(
            str(settings.get("test_gift", "爱心")), 80
        )
        layout.addWidget(self.test_gift, 0, 7)
        layout.addWidget(
            make_btn("发送测试", on_click=self._send_test), 0, 8
        )
        layout.setColumnStretch(1, 1)
        return group

    def _topic_manager(self):
        return getattr(self.win, "topic_manager", None)

    def _pressure_manager(self):
        return getattr(self.win, "pressure_manager", None)

    def _category_manager(self):
        return getattr(self.win, "category_manager", None)

    def _topic_selected_mode(self):
        return (
            "sequential"
            if self.topic_mode_combo.currentText() == "按顺序"
            else "random"
        )

    def _pressure_selected_mode(self):
        return (
            "sequential"
            if self.pressure_mode_combo.currentText() == "按顺序"
            else "random"
        )

    def _fill_choice_combo(self, combo, items, empty_text):
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for item in items:
            combo.addItem(str(item), str(item))
        if not items:
            combo.addItem(empty_text, "")
        if current:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)
        return bool(items)

    def _fill_manual_topic_combo(self):
        topics, mapping, selected, _categories = self._topic_selection_snapshot()
        scope = (
            str(self.topic_category_combo.currentData() or "")
            if hasattr(self, "topic_category_combo")
            else ""
        )
        if scope and scope != ALL_TOPIC_CATEGORIES:
            topics = [
                topic
                for topic in selected
                if mapping.get(topic, DEFAULT_TOPIC_CATEGORY) == scope
            ]
        else:
            topics = list(selected)
        ready = self._fill_choice_combo(
            self.manual_topic_combo,
            topics,
            "素材库为空，请先在消息管理勾选并确认",
        )
        self.manual_topic_send_btn.setEnabled(ready)

    def _topic_selection_snapshot(self):
        manager = self._topic_manager()
        if manager is not None:
            topics = list(manager.topics)
            mapping = dict(manager.topic_categories)
            selected = [
                topic
                for topic in manager.selected_topics
                if topic in topics
            ]
        else:
            data = load_topics_data()
            topics = list(data.get("topics", []))
            mapping = dict(data.get("topic_categories", {}))
            selected = [
                topic
                for topic in data.get("selected_topics", [])
                if topic in topics
            ]
        categories = []
        for topic in selected:
            category = mapping.get(topic, DEFAULT_TOPIC_CATEGORY)
            if category not in categories:
                categories.append(category)
        return topics, mapping, selected, categories

    def _sync_topic_category_desc(self):
        topics, mapping, selected, categories = self._topic_selection_snapshot()
        selected_count = len(selected)
        if hasattr(self, "topic_category_combo"):
            previous = str(self.topic_category_combo.currentData() or "")
            self.topic_category_combo.blockSignals(True)
            self.topic_category_combo.clear()
            if categories:
                show_all = len(categories) > 1
                if show_all:
                    self.topic_category_combo.addItem(
                        "全部已选", ALL_TOPIC_CATEGORIES
                    )
                for category in categories:
                    count = sum(
                        1
                        for topic in selected
                        if mapping.get(topic, DEFAULT_TOPIC_CATEGORY)
                        == category
                    )
                    self.topic_category_combo.addItem(
                        f"{category} ({count})", category
                    )
                valid = list(categories)
                if show_all:
                    valid.append(ALL_TOPIC_CATEGORIES)
                if previous in valid:
                    target = previous
                elif show_all:
                    target = ALL_TOPIC_CATEGORIES
                else:
                    target = categories[0]
                index = self.topic_category_combo.findData(target)
                self.topic_category_combo.setCurrentIndex(max(0, index))
                manager = self._topic_manager()
                if manager is not None and manager.active_category != target:
                    # 下拉框只展示“已确认话题”所属分类；若旧分类已经
                    # 没有确认话题，就把当前分类同步回有效值，避免
                    # 界面显示 A、定时器却按空分类 B 轮播。
                    manager.set_active_category(target)
            else:
                self.topic_category_combo.addItem("未选择话题", "")
                self.topic_category_combo.setCurrentIndex(0)
            self.topic_category_combo.blockSignals(False)
        if hasattr(self, "topic_category_desc"):
            if selected_count:
                scope = str(
                    self.topic_category_combo.currentData() or ""
                )
                label = "全部已选" if scope == ALL_TOPIC_CATEGORIES else scope
                current = (
                    selected_count
                    if scope == ALL_TOPIC_CATEGORIES
                    else sum(
                        1
                        for topic in selected
                        if mapping.get(topic, DEFAULT_TOPIC_CATEGORY) == scope
                    )
                )
                self.topic_category_desc.setText(
                    f"当前: {label} | 可用 {current} 条 / "
                    f"已确认 {selected_count} 条"
                )
            else:
                self.topic_category_desc.setText(
                    "素材库为空，请先在消息管理勾选并点“确认使用”"
                )

    def _on_topic_category_changed(self, text):
        category = str(
            self.topic_category_combo.currentData()
            or ""
        )
        if not category:
            self._fill_manual_topic_combo()
            self._update_topic_control_status()
            return
        manager = self._topic_manager()
        if manager is not None:
            manager.set_active_category(category)
        else:
            data = load_topics_data()
            data["active_category"] = category
            save_topics_data(data)
        self._sync_topic_category_desc()
        self._fill_manual_topic_combo()
        self._update_topic_control_status()
        page = self.win.pages.get("messages") if hasattr(self.win, "pages") else None
        if page is not None and hasattr(page, "refresh"):
            page.refresh()

    def _fill_manual_pressure_combo(self):
        manager = self._pressure_manager()
        if manager is not None:
            messages = list(manager.messages)
        else:
            messages = list(load_pressure_menu_data().get("messages", []))
        ready = self._fill_choice_combo(
            self.manual_pressure_combo,
            messages,
            "暂无逼单消息，请先在消息管理中添加",
        )
        self.manual_pressure_send_btn.setEnabled(ready)

    def sync_manual_send_options(self):
        self._sync_topic_category_desc()
        self._fill_manual_topic_combo()
        self._fill_manual_pressure_combo()

    def _on_topic_mode_changed(self, _text):
        manager = self._topic_manager()
        mode = self._topic_selected_mode()
        if manager is not None:
            manager.set_mode(mode)
        self.topic_mode_desc.setText(
            "(按顺序)" if mode == "sequential" else "(随机)"
        )
        self._update_topic_control_status()

    def _on_pressure_mode_changed(self, _text):
        manager = self._pressure_manager()
        mode = self._pressure_selected_mode()
        if manager is not None:
            manager.set_mode(mode)
        self.pressure_mode_desc.setText(
            "(按顺序)" if mode == "sequential" else "(随机)"
        )
        self._update_pressure_control_status()

    def _apply_topic_interval(self):
        value = _int_value(self.topic_interval_edit.text(), 30, 1)
        self.topic_interval_edit.setText(str(value))
        data = load_topics_data()
        data["interval"] = value
        save_topics_data(data)
        if self.topic_enable_check.isChecked():
            self.win.start_topic_timer(value, self._topic_selected_mode())
        self._update_topic_control_status()
        self.notify(f"话题间隔已设为 {value} 秒")

    def _apply_topic_no_repeat(self):
        value = _int_value(self.topic_no_repeat_edit.text(), 300, 1)
        self.topic_no_repeat_edit.setText(str(value))
        manager = self._topic_manager()
        if manager is not None:
            manager.set_no_repeat_seconds(value)
        else:
            data = load_topics_data()
            data["no_repeat_seconds"] = value
            save_topics_data(data)
        self._update_topic_control_status()
        self.notify(f"随机话题 {value} 秒内不重复")

    def _toggle_topic_timer(self, checked):
        if checked:
            try:
                ai_ready = bool(self.win._ai_ready())
            except Exception:
                ai_ready = False
            if not getattr(self.win, "connected", False) and not ai_ready:
                QMessageBox.warning(self, "提示", "请先连接设备或小智服务")
                self.topic_enable_check.blockSignals(True)
                self.topic_enable_check.setChecked(False)
                self.topic_enable_check.blockSignals(False)
                return
            manager = self._topic_manager()
            if manager is not None:
                manager.set_mode(self._topic_selected_mode())
            self.win.start_topic_timer(
                _int_value(
                    self.topic_interval_edit.text(),
                    _int_value(
                        load_topics_data().get("interval", 30), 30, 1
                    ),
                    1,
                ),
                self._topic_selected_mode(),
            )
        else:
            self.win.stop_topic_timer()
        data = load_topics_data()
        data["auto_start"] = bool(checked)
        save_topics_data(data)
        self._update_topic_control_status()

    def _apply_pressure_interval(self):
        value = _int_value(self.pressure_interval_edit.text(), 60, 1)
        self.pressure_interval_edit.setText(str(value))
        manager = self._pressure_manager()
        if manager is not None:
            manager.set_interval(value)
        if self.pressure_enable_check.isChecked():
            self.win.start_pressure_timer(
                value, self._pressure_selected_mode()
            )
        self._update_pressure_control_status()
        self.notify(f"逼单间隔已设为 {value} 秒")

    def _toggle_pressure_timer(self, checked):
        if checked:
            if not getattr(self.win, "connected", False):
                QMessageBox.warning(self, "提示", "请先连接设备")
                self.pressure_enable_check.blockSignals(True)
                self.pressure_enable_check.setChecked(False)
                self.pressure_enable_check.blockSignals(False)
                return
            self.win.start_pressure_timer(
                _int_value(self.pressure_interval_edit.text(), 60, 1),
                self._pressure_selected_mode(),
            )
        else:
            self.win.stop_pressure_timer()
        # 记住用户最后一次开关状态，重启后由主窗口自动恢复，
        # 避免“界面显示开过、重启后定时器却不再跑”。
        data = load_pressure_menu_data()
        data["auto_start"] = bool(checked)
        save_pressure_menu_data(data)
        self._update_pressure_control_status()

    def _send_manual_topic(self):
        text = str(self.manual_topic_combo.currentData() or "").strip()
        if text:
            self.win.send_text_async(text, "手动话题", True, notify=True)

    def _send_manual_pressure(self):
        text = str(self.manual_pressure_combo.currentData() or "").strip()
        if text:
            self.win.send_text_async(text, "手动逼单消息", True, notify=True)

    def _send_test(self):
        key = str(self.test_category_combo.currentData() or "like")
        manager = self._category_manager()
        if manager is None:
            QMessageBox.warning(self, "提示", "消息分类管理器未就绪")
            return
        msg_type = CATEGORY_FOR_TYPE.get(key, key)
        context = {
            "username": self.test_username.text().strip() or "测试用户",
            "content": "测试互动内容",
            "count": _int_value(self.test_count.text(), 1, 1),
            "gift_count": _int_value(self.test_count.text(), 1, 1),
            "gift_name": self.test_gift.text().strip() or "爱心",
            "gift_value": 1,
            "total": 0,
            "level": 1,
            "fansclub_name": "粉丝团",
            "share_platform": "抖音",
        }
        text = manager.get_next_message(key, context)
        if not text:
            QMessageBox.warning(self, "提示", "当前分类没有可发送的消息")
            return
        data = load_category_settings()
        data["test_category"] = key
        data["test_username"] = (
            self.test_username.text().strip() or "测试用户"
        )
        data["test_count"] = self.test_count.text().strip() or "1"
        data["test_gift"] = self.test_gift.text().strip() or "爱心"
        save_category_settings(data)
        self.win.send_text_async(text, f"测试{msg_type}", True, notify=True)

    def _update_topic_control_status(self):
        manager = self._topic_manager()
        enabled = bool(getattr(self.win, "_topic_enabled", False))
        if hasattr(self, "topic_enable_check"):
            self.topic_enable_check.blockSignals(True)
            self.topic_enable_check.setChecked(enabled)
            self.topic_enable_check.blockSignals(False)
        if manager is None:
            self.topic_status_label.setText("未启用 | 暂无话题")
        else:
            mode_text = (
                "按顺序" if manager.send_mode == "sequential" else "随机"
            )
            prefix = "已启用" if enabled else "未启用"
            effective = list(manager.runtime_topics)
            selected_count = len(getattr(manager, "selected_topics", []))
            if effective:
                scope_text = (
                    f"当前分类素材 {len(effective)} 条 / "
                    f"已确认 {selected_count} 条"
                )
                if manager.send_mode == "sequential":
                    next_index = (
                        manager.current_index % len(effective)
                    ) + 1
                    self.topic_status_label.setText(
                        f"{prefix} | {scope_text} | "
                        f"{mode_text} | 下一个: {next_index}"
                    )
                else:
                    self.topic_status_label.setText(
                        f"{prefix} | {scope_text} | "
                        f"{mode_text} | {manager.no_repeat_seconds} 秒内不重复"
                    )
            elif selected_count:
                self.topic_status_label.setText(
                    f"{prefix} | 当前分类没有已确认话题"
                )
            else:
                self.topic_status_label.setText(
                    f"{prefix} | 未选择话题（素材库为空）"
                )

        try:
            ai_ready = bool(self.win._ai_ready())
        except Exception:
            ai_ready = False
        client = None
        try:
            client = self.win._ai_client()
        except Exception:
            client = None
        audio_ready = bool(
            client is not None
            and getattr(client, "_audio_bridge", None) is not None
        )
        if ai_ready and audio_ready:
            self.audio_label.setText("音频: 小智语音就绪")
            self.audio_label.setStyleSheet("QLabel { color:#15803D; }")
        elif ai_ready:
            self.audio_label.setText("音频: 小智语音预热中…")
            self.audio_label.setStyleSheet("QLabel { color:#B45309; }")
        elif getattr(self.win, "connected", False):
            self.audio_label.setText("音频: 硬件音频就绪")
            self.audio_label.setStyleSheet("QLabel { color:#15803D; }")
        else:
            self.audio_label.setText("音频: 未就绪（未连接）")
            self.audio_label.setStyleSheet("QLabel { color:#64748B; }")

    def _update_pressure_control_status(self):
        enabled = bool(getattr(self.win, "_pressure_enabled", False))
        self.pressure_enable_check.blockSignals(True)
        self.pressure_enable_check.setChecked(enabled)
        self.pressure_enable_check.blockSignals(False)
        self.pressure_status_label.setText("已启用" if enabled else "未启用")

    def _restart_collector(self):
        page = self._collector_page()
        if page is not None and hasattr(page, "_restart"):
            page._restart()

    def _start_overview_monitor(self):
        if self.win is not None:
            self.win.start_monitoring()

    def _stop_overview_monitor(self):
        if self.win is not None:
            self.win.stop_monitoring()

    def on_monitor_status(self, running, text):
        self._barrage_monitor_running = bool(running)
        self.overview_monitor_label.setText(str(text or "未开始监控"))
        self.overview_monitor_label.setStyleSheet(
            "QLabel { color:%s; }" % ("#15803D" if running else "#64748B")
        )
        self.overview_start_monitor_btn.setEnabled(not running)
        self.overview_stop_monitor_btn.setEnabled(running)
        self._refresh_barrage_channel()


    def _make_channel_item(self, parent_layout, title):
        item = QWidget()
        layout = QHBoxLayout(item)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        dot = QLabel("●")
        dot.setStyleSheet("QLabel { color:#94A3B8; font-size:14pt; }")
        text = QLabel(f"{title}: 未连接")
        text.setStyleSheet("QLabel { color:#334155; font-weight:600; }")
        layout.addWidget(dot)
        layout.addWidget(text)
        item.setMinimumWidth(115)
        parent_layout.addWidget(item)
        return dot, text

    def _set_channel_state(self, dot, text_label, title, status, color):
        dot.setStyleSheet(f"QLabel {{ color:{color}; font-size:14pt; }}")
        text_label.setText(f"{title}: {status}")

    def on_hardware_status(self, connected, detail=""):
        status = f"已连接 {detail}".strip() if connected else "未连接"
        color = "#15803D" if connected else "#94A3B8"
        self._set_channel_state(
            self.hw_dot, self.hw_status_label, "硬件", status, color
        )

    def _set_output_channel(self, mode):
        win = getattr(self, "win", None)
        if win is not None and hasattr(win, "set_output_channel"):
            win.set_output_channel(mode)
        else:
            self.on_output_channel(mode)

    def on_output_channel(self, mode):
        """语音通道开关：硬件=小智喇叭，软件=小智电脑语音。"""
        mode = "hardware" if str(mode).lower() == "hardware" else "software"
        active = (
            "QPushButton { background:#15803D; color:#FFFFFF;"
            " border-radius:6px; padding:2px 10px; }"
        )
        idle = (
            "QPushButton { background:#E2E8F0; color:#334155;"
            " border-radius:6px; padding:2px 10px; }"
        )
        for btn, name in ((getattr(self, "hw_voice_btn", None), "hardware"),
                          (getattr(self, "sw_voice_btn", None), "software")):
            if btn is not None:
                btn.setStyleSheet(active if mode == name else idle)

    def on_software_status(self, connected, text=""):
        if connected:
            status, color = "已连接", "#15803D"
        elif "已断开" in text and "失败" not in text:
            status, color = "已断开", "#94A3B8"
        elif "失败" in text:
            status, color = "连接失败", "#DC2626"
        elif "断开" in text:
            status, color = "连接失败 / 已断开", "#DC2626"
        elif "正在" in text or "重连" in text:
            status, color = "连接中...", "#D97706"
        else:
            status, color = "未连接", "#94A3B8"
        self._set_channel_state(
            self.sw_dot, self.sw_status_label, "软件", status, color
        )

    def on_collector_status(self, running, websocket_ready, status_text=None):
        self._collector_running = bool(running)
        self._collector_ws_ready = bool(websocket_ready)
        if not self._collector_running:
            self._barrage_ws_connected = False
        if status_text:
            self.overview_service_status.setText(str(status_text))
        elif self._collector_running:
            self.overview_service_status.setText(
                "监听中 (本地服务就绪)" if self._collector_ws_ready
                else "采集器运行中，WebSocket 未就绪"
            )
        else:
            self.overview_service_status.setText("未启动")
        if self._collector_ws_ready:
            color = "#15803D"
        elif self._collector_running:
            color = "#D97706"
        else:
            color = "#64748B"
        self.overview_service_status.setStyleSheet(f"QLabel {{ color:{color}; }}")
        self.overview_restart_btn.setEnabled(self._collector_running)
        self._refresh_barrage_channel()

    def on_barrage_connection(self, connected):
        self._barrage_ws_connected = bool(connected)
        self._refresh_barrage_channel()

    def _refresh_barrage_channel(self):
        if self._barrage_ws_connected:
            status, color = "已连接", "#15803D"
        elif self._collector_ws_ready:
            status, color = "服务已就绪", "#15803D"
        elif self._collector_running:
            status, color = "服务启动中...", "#D97706"
        elif self._barrage_monitor_running:
            status, color = "等待采集服务...", "#D97706"
        else:
            status, color = "未启动", "#94A3B8"
        self._set_channel_state(
            self.barrage_dot,
            self.barrage_status_label,
            "弹幕",
            status,
            color,
        )

    # ---------- 设备列表 ----------
    def _reload_devices(self):
        data = load_devices()
        self.devices = list(data.get("devices", []))
        self._refresh_device_table()

    def _save_devices(self):
        current_index = 0
        for index, device in enumerate(self.devices):
            if str(device.get("host", "")) == self._connected_host:
                current_index = index
                break
        save_devices({
            "devices": self.devices,
            "current_index": current_index,
        })
        self._refresh_device_table()

    def _refresh_device_table(self):
        table = getattr(self, "device_table", None)
        if table is None:
            return
        table.setRowCount(len(self.devices))
        for row_index, device in enumerate(self.devices):
            host = str(device.get("host", ""))
            mac = str(device.get("mac", ""))
            name = str(device.get("name") or f"小智 {row_index + 1}")
            connected = host == self._connected_host
            values = [
                name,
                host,
                mac or "--",
                "已连接" if connected else "未连接",
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                if col_index == 3:
                    item.setForeground(
                        QColor("#15803D" if connected else "#64748B")
                    )
                table.setItem(row_index, col_index, item)
            action = make_btn(
                "断开" if connected else "连接",
                small=True,
                on_click=lambda checked=False, index=row_index: self._toggle_device_connection(index),
            )
            action.setFixedSize(54, 24)
            action.setStyleSheet(
                "QPushButton {"
                f" color:{'#DC2626' if connected else '#2563EB'};"
                " background:transparent; border:none; padding:0;"
                " min-height:24px; max-height:24px; font-size:9pt;"
                " font-weight:600;"
                "}"
                "QPushButton:hover { color:#1D4ED8; text-decoration:underline; }"
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(0)
            holder_layout.setAlignment(Qt.AlignCenter)
            holder_layout.addWidget(action)
            table.setCellWidget(row_index, 4, holder)
            table.setRowHeight(row_index, 34)
        if not self.devices:
            self.scan_result.setText("未发现设备，点击“扫描设备”查找小智")

    def _device_index(self, host):
        for index, device in enumerate(self.devices):
            if str(device.get("host", "")) == host:
                return index
        return 0

    def _toggle_device_connection(self, index):
        if index < 0 or index >= len(self.devices):
            return
        win = getattr(self, "win", None)
        if win is None:
            return
        device = self.devices[index]
        host = str(device.get("host", ""))
        port = int(device.get("port", 8888) or 8888)
        mac = str(device.get("mac", ""))
        if host == self._connected_host:
            win._auto_connect_enabled = False
            win.disconnect_device()
            return
        # 点“连接”即代表要用硬件：自动切到硬件喇叭，软件小智随之断开。
        if str(getattr(win, "output_channel", "software")) != "hardware":
            if hasattr(win, "set_output_channel"):
                win.set_output_channel("hardware")
        if getattr(win, "connected", False):
            win.disconnect_device()
        save_connection_settings({
            "host": host,
            "port": str(port),
            "mac": mac,
        })
        win._auto_connect_enabled = True
        self.scan_result.setText(f"正在连接 {host}:{port} ...")
        win.connect_device(host, port)

    # ---------- 授权 ----------
    def _copy_machine_code(self):
        QApplication.clipboard().setText(self.win.machine_code)
        self.notify("机器码已复制")

    def _activate_license(self):
        code = self.license_edit.text().strip()
        if not code:
            QMessageBox.warning(self, "提示", "请先输入授权码")
            return
        result = self.win.activate_license(code)
        if result.get("valid"):
            self.license_edit.clear()
            QMessageBox.information(self, "授权成功", result.get("message", "激活成功"))
        else:
            QMessageBox.warning(
                self, "授权失败", result.get("message", "授权码无效")
            )
        self.refresh()

    def refresh(self):
        if not hasattr(self, "machine_value"):
            return
        self.machine_value.setText(str(getattr(self.win, "machine_code", "")))
        status = {}
        try:
            status = self.win.license_status()
        except Exception as exc:
            status = {"activated": False, "message": str(exc)}
        if status.get("activated"):
            self.license_status.setText("已授权 - " + str(status.get("message", "授权有效")))
            self.license_status.setStyleSheet("QLabel { color:#15803D; }")
            expiry = status.get("expiry_date", "")
            days = status.get("remaining_days", 0)
            info = f"到期: {expiry or '长期'}"
            if days != 999999:
                info += f" | 剩余 {days} 天"
            self.activation_info.setText(info)
            self.activation_info.setStyleSheet("QLabel { color:#15803D; }")
            if hasattr(self.win, "set_global_license"):
                self.win.set_global_license(
                    info.replace("到期:", "授权到期:", 1), True
                )
        else:
            self.license_status.setText(
                "未授权 - 程序无法使用-联系: 销售商 | " + str(status.get("message", ""))
            )
            self.license_status.setStyleSheet("QLabel { color:#DC2626; }")
            self.activation_info.setText("未激活")
            self.activation_info.setStyleSheet("QLabel { color:#64748B; }")
            if hasattr(self.win, "set_global_license"):
                self.win.set_global_license("授权: 未激活", False)
        self.update_counts()
        self.sync_manual_send_options()

    # ---------- 扫描与连接 ----------
    def _scan_devices(self):
        win = getattr(self, "win", None)
        if win is not None and str(
            getattr(win, "output_channel", "software")
        ) != "hardware":
            self.scan_result.setText(
                "当前是软件语音模式，硬件已关闭；请先点“🔊 硬件喇叭”再扫描"
            )
            self.scan_btn.setEnabled(True)
            return
        self.scan_result.setText("正在扫描局域网，请稍候...")
        self.scan_btn.setEnabled(False)
        self.win.scan_devices_async()

    def on_scan_blocked(self):
        """主窗口拒绝扫描时恢复按钮状态，避免一直转圈。"""
        self.scan_btn.setEnabled(True)
        self.scan_result.setText(
            "当前是软件语音模式，硬件已关闭；请先点“🔊 硬件喇叭”再扫描"
        )

    def on_connected(self, info):
        self._connected_host = str(info.get("host", ""))
        if self._connected_host and not any(
            str(device.get("host", "")) == self._connected_host
            for device in self.devices
        ):
            self.devices.append({
                "name": f"小智 {len(self.devices) + 1}",
                "host": self._connected_host,
                "port": int(info.get("port", 8888) or 8888),
                "mac": str(info.get("response", {}).get("mac", "")),
                "description": "自动连接保存的设备",
            })
            self._save_devices()
        else:
            self._refresh_device_table()
        self.send_btn.setEnabled(True)
        if hasattr(self.win, "set_global_device_status"):
            self.win.set_global_device_status(True)
        self.state_dot.setStyleSheet("QLabel { color:#15803D; }")
        self.state_text.setText("已连接")
        self.device_state_label.setText("已连接")
        self.device_icon.setText("🟢")
        self.device_wait_label.setText(f"{info.get('host', '')}:{info.get('port', '')}")
        self.scan_result.setText(
            f"已连接 {info.get('host', '')}:{info.get('port', '')}"
        )
        self.on_hardware_status(
            True, f"{info.get('host', '')}:{info.get('port', '')}"
        )
        self.last_update.setText(
            "最后更新: " + datetime.now().strftime("%H:%M:%S")
        )

    def on_disconnected(self):
        self._connected_host = ""
        self._refresh_device_table()
        self.send_btn.setEnabled(False)
        if hasattr(self.win, "set_global_device_status"):
            self.win.set_global_device_status(False)
        self.state_dot.setStyleSheet("QLabel { color:#94A3B8; }")
        self.state_text.setText("离线")
        self.device_state_label.setText("未连接")
        self.device_icon.setText("📴")
        self.device_wait_label.setText("等待连接...")
        self.connection_time.setText("")
        self.latency_label.setText("延迟: -- ms")
        if self.devices:
            self.scan_result.setText("已断开，点设备后的“连接”即可切换")
        self.on_hardware_status(False)
        self._barrage_monitor_running = False
        self._barrage_ws_connected = False
        self._refresh_barrage_channel()

    def on_hardware_link_off(self):
        """软件语音通道激活：硬件链路已整体关闭。"""
        self._connected_host = ""
        self._refresh_device_table()
        self.send_btn.setEnabled(False)
        if hasattr(self.win, "set_global_device_status"):
            self.win.set_global_device_status(False)
        self.state_dot.setStyleSheet("QLabel { color:#94A3B8; }")
        self.state_text.setText("硬件已关闭")
        self.device_state_label.setText("硬件已关闭")
        self.device_icon.setText("📴")
        self.device_wait_label.setText("软件语音模式，硬件已关闭")
        self.connection_time.setText("")
        self.latency_label.setText("延迟: -- ms")
        self.on_hardware_status(False, "已关闭（软件语音）")

    def on_device_status(self, response):
        if not isinstance(response, dict):
            return
        state = str(response.get("state", "")).lower()
        text = STATE_LABELS.get(state, state or "未知")
        color = STATE_COLORS.get(state, "#64748B")
        self.state_dot.setStyleSheet(f"QLabel {{ color:{color}; }}")
        self.state_text.setText(text)
        self.device_state_label.setText(text)
        self.device_icon.setText("🟢" if state in {"idle", "listening"} else "🟡")
        self.device_wait_label.setText(
            f"状态已更新 | {response.get('message', response.get('state_text', ''))}"
        )
        latency = response.get("latency", response.get("rtt"))
        if latency is not None:
            try:
                self.latency_label.setText(f"延迟: {float(latency):.0f} ms")
            except (TypeError, ValueError):
                pass
        self.last_update.setText(
            "最后更新: " + datetime.now().strftime("%H:%M:%S")
        )

    def on_scan_done(self, found):
        self.scan_btn.setEnabled(True)
        if not found:
            self.scan_result.setText("未发现设备，请检查设备电源和局域网连接")
            self.devices = []
            self._save_devices()
            return
        previous = {}
        for device in self.devices:
            key = str(device.get("mac", "")).strip().lower() or str(
                device.get("host", "")
            )
            previous[key] = device
        devices = []
        seen = set()
        for item in found:
            host = str(item.get("host", "")).strip()
            mac = str(item.get("mac", "")).strip()
            if not host:
                continue
            key = mac.lower() or host
            if key in seen:
                continue
            seen.add(key)
            old = previous.get(key, {})
            devices.append({
                "name": str(old.get("name") or f"小智 {len(devices) + 1}"),
                "host": host,
                "port": int(item.get("port", 8888) or 8888),
                "mac": mac,
                "description": "扫描发现",
            })
        self.devices = devices
        self._save_devices()
        pieces = [
            f"{item.get('host', '')}:{item.get('port', '')}"
            for item in found[:6]
        ]
        self.scan_result.setText(
            f"发现 {len(devices)} 台设备: " + "，".join(pieces)
        )
        if (
            devices
            and not getattr(self.win, "connected", False)
            and str(getattr(self.win, "output_channel", "software")) == "hardware"
        ):
            self._toggle_device_connection(0)

    def _send_text(self):
        self.win.send_text_async(self.send_edit.text(), notify=True)

    def _ping(self):
        self.win.ping_async()

    def _query_status(self):
        self.win.query_status_async()

    # ---------- 状态展示 ----------
    def append_log(self, line):
        page = self.win.pages.get("logs") if hasattr(self.win, "pages") else None
        if page is not None and page is not self:
            page.append_log(line)

    def clear_log(self):
        page = self.win.pages.get("logs") if hasattr(self.win, "pages") else None
        if page is not None and page is not self:
            page.clear_log()

    def clear_send_input(self):
        self.send_edit.clear()

    def update_connection_time(self, started_at):
        if not started_at:
            self.connection_time.setText("")
            return
        seconds = max(0, int(time.time() - started_at))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        self.connection_time.setText(f"已连接 {hours:02d}:{minutes:02d}:{secs:02d}")

    def update_counts(self):
        counts = {"total": 0, "unsent": 0, "sent": 0}
        manager = getattr(self.win, "barrage_manager", None)
        try:
            if manager is not None:
                counts = manager.get_counts()
            else:
                sent = len(load_barrages("sent_barrages.json"))
                unsent = (
                    len(load_barrages("priority_barrages.json"))
                    + len(load_barrages("received_barrages.json"))
                )
                counts = {"total": sent + unsent, "unsent": unsent, "sent": sent}
        except Exception:
            pass
        if hasattr(self, "status_count"):
            self.status_count.setText(
                f"弹幕: 总 {counts['total']} | 未发 {counts['unsent']} | 已发 {counts['sent']}"
            )


class CommunicationLogPage(PageBase):
    def __init__(self, window):
        super().__init__("通信日志", None, window)

        log_group = QFrame()
        log_group.setObjectName("logCard")
        log_group.setStyleSheet(
            "QFrame#logCard { background:#FFFFFF; border:1px solid #E3E8EF;"
            " border-radius:10px; }"
        )
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(14, 12, 14, 14)
        log_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        header_row.setContentsMargins(0, 0, 0, 0)
        log_title = QLabel("通信日志")
        log_title.setStyleSheet(
            "QLabel { background:#111827; color:#FFFFFF; font-size:10.5pt;"
            " font-weight:700; padding:3px 10px; border-radius:5px; }"
        )
        header_row.addWidget(log_title)
        header_row.addStretch(1)
        self.ping_btn = make_btn("Ping测试", on_click=self._ping)
        header_row.addWidget(self.ping_btn)
        self.status_btn = make_btn("获取状态", on_click=self._query_status)
        header_row.addWidget(self.status_btn)
        header_row.addWidget(make_btn("清空日志", on_click=self.clear_log))
        log_layout.addLayout(header_row)

        self.log_text = make_text_edit("")
        self.log_text.setMinimumHeight(420)
        self.log_text.setStyleSheet(
            "QTextEdit { font-family: Consolas, Microsoft YaHei UI;"
            " background:#0F172A; color:#E5E7EB; border:none;"
            " border-radius:6px; padding:8px; }"
        )
        self.log_text.document().setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_text, 1)

        # 与其他页面保持一致：内容最大宽度 1440，并在页面内水平居中。
        content = QWidget()
        content.setMaximumWidth(1440)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(log_group, 1)

        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        holder_layout.addStretch(1)
        holder_layout.addWidget(content, 100)
        holder_layout.addStretch(1)
        self.root.addWidget(holder, 1)
        self.set_connected(bool(getattr(window, "connected", False)))

    def set_connected(self, connected):
        enabled = bool(connected)
        self.ping_btn.setEnabled(enabled)
        self.status_btn.setEnabled(enabled)

    def append_log(self, line):
        self.log_text.append(str(line))
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def clear_log(self):
        self.log_text.clear()

    def _ping(self):
        self.win.ping_async()

    def _query_status(self):
        self.win.query_status_async()


class TopicPage(PageBase):
    def __init__(self, window):
        super().__init__("消息管理", None, window)
        topic_data = load_topics_data()
        topics = list(topic_data.get("topics", []))
        categories = list(topic_data.get("categories", TOPIC_CATEGORIES))
        active_category = str(
            topic_data.get("active_category", ALL_TOPIC_CATEGORIES)
            or ALL_TOPIC_CATEGORIES
        )
        if active_category not in [ALL_TOPIC_CATEGORIES] + categories:
            active_category = ALL_TOPIC_CATEGORIES
        self.interval_value = _int_value(topic_data.get("interval", 30), 30, 1)
        send_mode = str(topic_data.get("send_mode", "sequential"))
        self.no_repeat_value = _int_value(
            topic_data.get("no_repeat_seconds", 300), 300, 1
        )
        pressure_data = load_pressure_menu_data()
        pressure_interval = str(pressure_data.get("interval", 60))
        pressure_send_mode = str(pressure_data.get("send_mode", "sequential"))

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(6)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        topics_column = QVBoxLayout()
        topics_column.setSpacing(8)
        pressure_column = QVBoxLayout()
        pressure_column.setSpacing(8)
        columns.addLayout(topics_column, 1)
        columns.addLayout(pressure_column, 1)
        body.addLayout(columns, 1)

        control = make_group("话题设置")
        control_layout = QGridLayout(control)
        control_layout.setContentsMargins(12, 14, 12, 12)
        control_layout.setHorizontalSpacing(10)
        control_layout.setVerticalSpacing(10)

        self.enable_check = make_check("启用话题定时发送")
        self.enable_check.toggled.connect(self._toggle_timer)
        control_layout.addWidget(QLabel("发送间隔(秒):"), 0, 0)
        self.interval_edit = make_field(str(self.interval_value), 90)
        control_layout.addWidget(self.interval_edit, 0, 1)
        control_layout.addWidget(make_btn("修改", on_click=self._apply_interval), 0, 2)
        control_layout.addWidget(QLabel("发送模式:"), 0, 3)
        self.mode_combo = make_combo(
            ["按顺序", "随机"],
            "按顺序" if send_mode == "sequential" else "随机",
            120,
        )
        control_layout.addWidget(self.mode_combo, 0, 4)
        self.mode_desc = QLabel("(按顺序)" if send_mode == "sequential" else "(随机)")
        self.mode_desc.setStyleSheet("QLabel { color:#2563EB; }")
        control_layout.addWidget(self.mode_desc, 0, 5)
        self.audio_label = QLabel("音频: 未预热")
        control_layout.addWidget(self.audio_label, 0, 6)

        self.topic_status = QLabel("")
        self.topic_status.setStyleSheet("QLabel { color:#64748B; }")

        control_layout.addWidget(QLabel("手动发送话题:"), 1, 0)
        self.manual_combo = make_combo([])
        self.manual_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.manual_combo.setMinimumContentsLength(12)
        self.manual_combo.setMinimumWidth(0)
        self.manual_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        control_layout.addWidget(self.manual_combo, 1, 1, 1, 5)
        self.manual_send_btn = make_btn(
            "发送", on_click=self._send_manual
        )
        control_layout.addWidget(self.manual_send_btn, 1, 6)
        control_layout.addWidget(QLabel("随机不重复(秒):"), 2, 0)
        self.no_repeat_edit = make_field(str(self.no_repeat_value), 90)
        control_layout.addWidget(self.no_repeat_edit, 2, 1)
        control_layout.addWidget(
            make_btn("应用", on_click=self._apply_no_repeat), 2, 2
        )
        no_repeat_hint = QLabel("随机模式下，这段时间内不会重复同一个话题")
        no_repeat_hint.setStyleSheet("QLabel { color:#64748B; }")
        control_layout.addWidget(no_repeat_hint, 2, 3, 1, 4)
        control_layout.setColumnStretch(1, 1)
        topics_column.addWidget(
            _wrap_group_with_header(
                control,
                "话题设置",
                [self.enable_check, self.topic_status],
            )
        )

        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)

        list_group = _add_group(
            topics_column, "分类 → 话题（先选分类，再勾话题）", 1
        )
        list_layout = _set_group_layout(list_group)

        category_caption = QHBoxLayout()
        category_caption.setSpacing(8)
        category_caption.addWidget(QLabel("① 分类（4个字）:"))
        self.topic_selection_label = QLabel("")
        self.topic_selection_label.setStyleSheet("QLabel { color:#64748B; }")
        category_caption.addWidget(self.topic_selection_label, 1)
        category_caption.addWidget(
            make_btn("全部分类", on_click=self._show_all_categories)
        )
        list_layout.addLayout(category_caption)

        self.category_list = QListWidget()
        self.category_list.setMinimumHeight(108)
        self.category_list.setMaximumHeight(150)
        self.category_list.setSelectionMode(QListWidget.SingleSelection)
        self._loading_category_list = False
        self._category_refresh_pending = False
        self._draft_selected = None
        self.category_list.currentRowChanged.connect(
            self._on_category_list_changed
        )
        self.category_list.itemChanged.connect(
            self._on_category_item_changed
        )
        list_layout.addWidget(self.category_list)

        category_add_row = QHBoxLayout()
        category_add_row.setSpacing(8)
        category_add_row.addWidget(QLabel("新增分类:"))
        self.new_category_edit = make_field()
        self.new_category_edit.setMaxLength(4)
        self.new_category_edit.setPlaceholderText("必须正好 4 个字")
        category_add_row.addWidget(self.new_category_edit, 1)
        category_add_row.addWidget(
            make_btn("添加分类", on_click=self._add_topic_category)
        )
        list_layout.addLayout(category_add_row)

        topic_caption = QHBoxLayout()
        topic_caption.setSpacing(8)
        topic_caption.addWidget(QLabel("② 当前分类话题:"))
        topic_caption.addStretch(1)
        topic_caption.addWidget(
            make_btn("全选本类", on_click=self._select_all_visible_topics)
        )
        topic_caption.addWidget(
            make_btn("清空勾选", on_click=self._clear_topic_selection)
        )
        list_layout.addLayout(topic_caption)

        self.topic_list = QListWidget()
        self.topic_list.setMinimumHeight(250)
        self.topic_list.setSelectionMode(QListWidget.SingleSelection)
        self._loading_topic_list = False
        self._selection_dirty = False
        self.topic_list.itemChanged.connect(self._on_topic_item_changed)
        list_layout.addWidget(self.topic_list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(
            make_btn("✔ 确认使用", on_click=self._confirm_topic_selection)
        )
        buttons.addWidget(make_btn("编辑", on_click=self._edit_topic))
        buttons.addWidget(make_btn("删除", on_click=self._delete_topic))
        buttons.addWidget(make_btn("清空所有", on_click=self._clear_topics))
        buttons.addWidget(make_btn("重置默认", on_click=self._reset_topics))
        buttons.addWidget(make_btn("刷新", on_click=self.refresh))
        buttons.addStretch(1)
        list_layout.addLayout(buttons)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(QLabel("新话题:"))
        self.new_topic_edit = make_field()
        self.new_topic_edit.setPlaceholderText(
            f"最多{MAX_TOPIC_CHARS}字，超过添加不了"
        )
        add_row.addWidget(self.new_topic_edit, 1)
        add_row.addWidget(make_btn("添加", on_click=self._add_topic))
        list_layout.addLayout(add_row)

        pressure_control = make_group("逼单设置")
        pressure_layout = QGridLayout(pressure_control)
        pressure_layout.setContentsMargins(12, 14, 12, 12)
        pressure_layout.setHorizontalSpacing(10)
        pressure_layout.setVerticalSpacing(10)

        self.pressure_enable_check = make_check("启用逼单定时发送")
        self.pressure_enable_check.toggled.connect(self._toggle_pressure_timer)
        pressure_layout.addWidget(QLabel("发送间隔(秒):"), 0, 0)
        self.pressure_interval_edit = make_field(pressure_interval, 90)
        pressure_layout.addWidget(self.pressure_interval_edit, 0, 1)
        pressure_layout.addWidget(
            make_btn("修改", on_click=self._apply_pressure_interval), 0, 2
        )
        pressure_layout.addWidget(QLabel("发送模式:"), 0, 3)
        self.pressure_mode_combo = make_combo(
            ["按顺序", "随机"],
            "按顺序" if pressure_send_mode == "sequential" else "随机",
            120,
        )
        pressure_layout.addWidget(self.pressure_mode_combo, 0, 4)
        self.pressure_mode_desc = QLabel(
            "(按顺序)" if pressure_send_mode == "sequential" else "(随机)"
        )
        self.pressure_mode_desc.setStyleSheet("QLabel { color:#2563EB; }")
        pressure_layout.addWidget(self.pressure_mode_desc, 0, 5)
        self.pressure_status_label = QLabel("未启用")
        pressure_layout.addWidget(QLabel("手动发送逼单消息:"), 1, 0)
        self.pressure_manual_combo = make_combo([])
        self.pressure_manual_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.pressure_manual_combo.setMinimumContentsLength(12)
        self.pressure_manual_combo.setMinimumWidth(0)
        self.pressure_manual_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        pressure_layout.addWidget(self.pressure_manual_combo, 1, 1, 1, 5)
        self.pressure_manual_send_btn = make_btn(
            "发送", on_click=self._send_pressure_manual
        )
        pressure_layout.addWidget(self.pressure_manual_send_btn, 1, 6)
        pressure_layout.setColumnStretch(1, 1)
        pressure_column.addWidget(
            _wrap_group_with_header(
                pressure_control,
                "逼单设置",
                [self.pressure_enable_check, self.pressure_status_label],
            )
        )

        pressure_list_group = _add_group(pressure_column, "逼单列表", 1)
        pressure_list_layout = _set_group_layout(pressure_list_group)
        pressure_add_row = QHBoxLayout()
        pressure_add_row.setSpacing(8)
        pressure_add_row.addWidget(QLabel("新逼单消息:"))
        self.new_pressure_edit = make_field()
        self.new_pressure_edit.setPlaceholderText(
            f"最多{MAX_PRESSURE_CHARS}字，超过添加不了"
        )
        pressure_add_row.addWidget(self.new_pressure_edit, 1)
        pressure_add_row.addWidget(
            make_btn("添加", on_click=self._add_pressure_message)
        )
        pressure_list_layout.addLayout(pressure_add_row)
        self.pressure_list = QListWidget()
        self.pressure_list.setMinimumHeight(220)
        pressure_list_layout.addWidget(self.pressure_list, 1)
        pressure_buttons = QHBoxLayout()
        pressure_buttons.setSpacing(8)
        pressure_buttons.addWidget(
            make_btn("编辑", on_click=self._edit_pressure_message)
        )
        pressure_buttons.addWidget(
            make_btn("删除", on_click=self._delete_pressure_message)
        )
        pressure_buttons.addWidget(
            make_btn("清空", on_click=self._clear_pressure_messages)
        )
        pressure_buttons.addWidget(
            make_btn("重置默认", on_click=self._reset_pressure_messages)
        )
        pressure_buttons.addWidget(make_btn("刷新", on_click=self.refresh))
        pressure_buttons.addStretch(1)
        pressure_list_layout.addLayout(pressure_buttons)

        self.pressure_mode_combo.currentTextChanged.connect(
            self._on_pressure_mode_changed
        )
        self.root.addWidget(make_scroll(content), 1)
        self.refresh()
        self._audio_status_timer = QTimer(self)
        self._audio_status_timer.setInterval(1000)
        self._audio_status_timer.timeout.connect(self._update_status)
        self._audio_status_timer.start()

    def _manager(self):
        return getattr(self.win, "topic_manager", None)

    def _selected_mode(self):
        return "sequential" if self.mode_combo.currentText() == "按顺序" else "random"

    def _on_category_list_changed(self, row):
        if bool(getattr(self, "_loading_category_list", False)):
            return
        item = self.category_list.item(row) if row >= 0 else None
        if item is None:
            return
        category = str(item.data(Qt.UserRole) or ALL_TOPIC_CATEGORIES)
        manager = self._manager()
        if manager is not None:
            if str(manager.active_category) == category:
                return
            manager.set_active_category(category)
        else:
            data = load_topics_data()
            data["active_category"] = category
            data["selected_topics"] = []
            save_topics_data(data)
        if not getattr(self, "_category_refresh_pending", False):
            self._category_refresh_pending = True
            QTimer.singleShot(0, self._run_category_refresh)

    def _run_category_refresh(self):
        self._category_refresh_pending = False
        self.refresh(preserve_draft=True)

    def _category_member_topics(self, category):
        manager = self._manager()
        if manager is not None:
            topics = list(manager.topics)
            mapping = dict(manager.topic_categories)
        else:
            data = load_topics_data()
            topics = list(data.get("topics", []))
            mapping = dict(data.get("topic_categories", {}))
        if category == ALL_TOPIC_CATEGORIES:
            return topics
        return [
            topic
            for topic in topics
            if mapping.get(topic, DEFAULT_TOPIC_CATEGORY) == category
        ]

    @staticmethod
    def _check_state_for_category(category, topics, mapping, draft):
        if category == ALL_TOPIC_CATEGORIES:
            members = topics
        else:
            members = [
                topic
                for topic in topics
                if mapping.get(topic, DEFAULT_TOPIC_CATEGORY) == category
            ]
        if not members:
            return Qt.Unchecked
        checked = sum(1 for topic in members if topic in draft)
        if checked == len(members):
            return Qt.Checked
        if checked == 0:
            return Qt.Unchecked
        return Qt.PartiallyChecked

    def _on_category_item_changed(self, item):
        if bool(getattr(self, "_loading_category_list", False)):
            return
        if item is None or self._draft_selected is None:
            return
        category = str(item.data(Qt.UserRole) or ALL_TOPIC_CATEGORIES)
        members = self._category_member_topics(category)
        state = item.checkState()
        if state == Qt.Checked:
            self._draft_selected.update(members)
        elif state == Qt.Unchecked:
            self._draft_selected.difference_update(members)
        else:
            return
        self._selection_dirty = True
        row = self.category_list.row(item)
        if row >= 0 and row != self.category_list.currentRow():
            self.category_list.setCurrentRow(row)
            return
        self._apply_draft_to_visible_topics()
        self._update_category_check_states()
        self._refresh_selection_label()

    def _apply_draft_to_visible_topics(self):
        if self._draft_selected is None:
            return
        self._loading_topic_list = True
        for index in range(self.topic_list.count()):
            item = self.topic_list.item(index)
            topic = str(item.data(Qt.UserRole + 1) or "").strip()
            item.setCheckState(
                Qt.Checked
                if topic in self._draft_selected
                else Qt.Unchecked
            )
        self._loading_topic_list = False

    def _update_category_check_states(self):
        if self._draft_selected is None:
            return
        manager = self._manager()
        if manager is not None:
            topics = list(manager.topics)
            mapping = dict(manager.topic_categories)
        else:
            data = load_topics_data()
            topics = list(data.get("topics", []))
            mapping = dict(data.get("topic_categories", {}))
        self._loading_category_list = True
        for row in range(self.category_list.count()):
            item = self.category_list.item(row)
            category = str(item.data(Qt.UserRole) or ALL_TOPIC_CATEGORIES)
            item.setCheckState(
                self._check_state_for_category(
                    category, topics, mapping, self._draft_selected
                )
            )
        self._loading_category_list = False

    def _show_all_categories(self):
        manager = self._manager()
        if manager is None:
            return
        manager.set_active_category(ALL_TOPIC_CATEGORIES)
        self.refresh(preserve_draft=True)

    def _add_topic_category(self):
        name = self.new_category_edit.text().strip()
        if len(name) != 4:
            QMessageBox.warning(
                self, "提示", "分类名必须正好 4 个字。"
            )
            return
        manager = self._manager()
        if manager is None:
            return
        if not manager.add_category(name):
            QMessageBox.warning(
                self, "提示", "该分类已存在，或分类名不符合要求。"
            )
            return
        self.new_category_edit.clear()
        manager.set_active_category(name)
        self.refresh(preserve_draft=True)
        self.notify(f"已新增分类：{name}")

    def _checked_topic_names(self):
        names = []
        for index in range(self.topic_list.count()):
            item = self.topic_list.item(index)
            if item.checkState() != Qt.Checked:
                continue
            topic = str(item.data(Qt.UserRole + 1) or "").strip()
            if topic and topic not in names:
                names.append(topic)
        return names

    def _on_topic_item_changed(self, _item):
        if bool(getattr(self, "_loading_topic_list", False)):
            return
        if self._draft_selected is None:
            return
        topic = str(_item.data(Qt.UserRole + 1) or "").strip()
        if not topic:
            return
        if _item.checkState() == Qt.Checked:
            self._draft_selected.add(topic)
        else:
            self._draft_selected.discard(topic)
        # 勾选只是草稿，必须点“确认使用”才写入素材库。
        self._selection_dirty = True
        self._update_category_check_states()
        self._refresh_selection_label()

    def _select_all_visible_topics(self):
        if self._draft_selected is None:
            return
        self._draft_selected.update(self._visible_topic_names())
        self._loading_topic_list = True
        for index in range(self.topic_list.count()):
            self.topic_list.item(index).setCheckState(Qt.Checked)
        self._loading_topic_list = False
        self._selection_dirty = True
        self._update_category_check_states()
        self._refresh_selection_label()

    def _clear_topic_selection(self):
        if self._draft_selected is None:
            return
        self._draft_selected.difference_update(self._visible_topic_names())
        self._loading_topic_list = True
        for index in range(self.topic_list.count()):
            self.topic_list.item(index).setCheckState(Qt.Unchecked)
        self._loading_topic_list = False
        self._selection_dirty = True
        self._update_category_check_states()
        self._refresh_selection_label()

    def _visible_topic_names(self):
        names = []
        for index in range(self.topic_list.count()):
            topic = str(
                self.topic_list.item(index).data(Qt.UserRole + 1) or ""
            ).strip()
            if topic and topic not in names:
                names.append(topic)
        return names

    def _confirm_topic_selection(self):
        manager = self._manager()
        if manager is None:
            return
        if self._draft_selected is None:
            return
        ordered = [
            topic
            for topic in manager.topics
            if topic in self._draft_selected
        ]
        manager.set_selected_topics(ordered)
        self._selection_dirty = False
        self.refresh()
        self._sync_home_topic_options()
        self.notify(f"已确认素材库：{len(manager.selected_topics)} 条")

    def _refresh_selection_label(self):
        if not hasattr(self, "topic_selection_label"):
            return
        manager = self._manager()
        if manager is None:
            self.topic_selection_label.setText("")
            return
        selected = len(getattr(manager, "selected_topics", []))
        pending = len(self._draft_selected or set())
        if bool(getattr(self, "_selection_dirty", False)):
            self.topic_selection_label.setText(
                f"待确认 {pending} 条｜当前已确认 {selected} 条"
            )
        elif selected:
            self.topic_selection_label.setText(
                f"已确认素材库 {selected} 条"
            )
        else:
            self.topic_selection_label.setText(
                "素材库为空，勾选后点“确认使用”"
            )

    def _sync_home_topic_options(self):
        home = getattr(self.win, "pages", {}).get("main")
        if home is not None and hasattr(home, "sync_manual_send_options"):
            home.sync_manual_send_options()

    def _on_mode_changed(self, text):
        manager = self._manager()
        mode = self._selected_mode()
        if manager is not None:
            manager.set_mode(mode)
        self.mode_desc.setText("(按顺序)" if mode == "sequential" else "(随机)")
        self._update_status()

    def _apply_interval(self):
        value = _int_value(self.interval_edit.text(), 30, 1)
        self.interval_value = value
        self.interval_edit.setText(str(value))
        data = load_topics_data()
        data["interval"] = value
        save_topics_data(data)
        if self.enable_check.isChecked():
            self.win.start_topic_timer(value, self._selected_mode())
        self._update_status()
        self.notify(f"话题间隔已设为 {value} 秒")

    def _apply_no_repeat(self):
        value = _int_value(self.no_repeat_edit.text(), 300, 1)
        self.no_repeat_value = value
        self.no_repeat_edit.setText(str(value))
        manager = self._manager()
        if manager is not None:
            manager.set_no_repeat_seconds(value)
        else:
            data = load_topics_data()
            data["no_repeat_seconds"] = value
            save_topics_data(data)
        self._update_status()
        self.notify(f"随机话题 {value} 秒内不重复")

    def _toggle_timer(self, checked):
        if checked:
            try:
                ai_ready = bool(self.win._ai_ready())
            except Exception:
                ai_ready = False
            if not getattr(self.win, "connected", False) and not ai_ready:
                QMessageBox.warning(self, "提示", "请先连接设备或小智服务")
                self.enable_check.blockSignals(True)
                self.enable_check.setChecked(False)
                self.enable_check.blockSignals(False)
                return
            manager = self._manager()
            if manager is not None and not manager.effective_topics:
                QMessageBox.warning(
                    self,
                    "提示",
                    "素材库为空。请先在下方勾选话题，"
                    "再点“确认使用”。",
                )
                self.enable_check.blockSignals(True)
                self.enable_check.setChecked(False)
                self.enable_check.blockSignals(False)
                return
            if manager is not None:
                manager.set_mode(self._selected_mode())
            self.win.start_topic_timer(
                _int_value(self.interval_edit.text(), self.interval_value, 1),
                self._selected_mode(),
            )
        else:
            self.win.stop_topic_timer()
        self._save_auto_start(checked)
        self._update_status()

    def _save_auto_start(self, enabled):
        data = load_topics_data()
        data["auto_start"] = bool(enabled)
        save_topics_data(data)

    def _send_manual(self):
        text = str(self.manual_combo.currentData() or "").strip()
        if text and self.win.send_text_async(
            text, "手动话题", True, notify=True
        ):
            self.manual_combo.setCurrentIndex(0)

    def _add_topic(self):
        text = self.new_topic_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请输入话题内容")
            return
        if len(text) > MAX_TOPIC_CHARS:
            QMessageBox.warning(
                self,
                "提示",
                f"话题最多{MAX_TOPIC_CHARS}字，当前{len(text)}字，添加不了。",
            )
            return
        manager = self._manager()
        category = (
            str(manager.active_category)
            if manager is not None
            else ALL_TOPIC_CATEGORIES
        )
        if category == ALL_TOPIC_CATEGORIES:
            QMessageBox.warning(
                self,
                "提示",
                "请先在分类列表里选中一个具体分类，再添加话题。",
            )
            return
        if manager is not None and manager.add(text, category):
            self.new_topic_edit.clear()
            self.refresh()

    def _edit_topic(self):
        item = self.topic_list.currentItem()
        manager = self._manager()
        if item is None or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要编辑的话题")
            return
        row_index = int(item.data(Qt.UserRole))
        topic = str(item.data(Qt.UserRole + 1) or "").strip()
        dialog = TopicEditDialog(
            self,
            "编辑话题",
            topic,
            manager.categories,
            manager.category_for_topic(topic),
        )
        if dialog.exec_():
            value = dialog.value().strip()
            if len(value) > MAX_TOPIC_CHARS:
                QMessageBox.warning(
                    self,
                    "提示",
                    f"话题最多{MAX_TOPIC_CHARS}字，当前{len(value)}字，保存不了。",
                )
                return
            manager.edit(row_index, value, dialog.category())
            self.refresh()

    def _delete_topic(self):
        item = self.topic_list.currentItem()
        manager = self._manager()
        if item is None or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要删除的话题")
            return
        row_index = int(item.data(Qt.UserRole))
        if QMessageBox.question(
            self, "确认删除", "确定删除选中的话题吗？"
        ) == QMessageBox.Yes:
            manager.delete(row_index)
            self.refresh()

    def _clear_topics(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认清空", "确定清空所有话题吗？"
        ) == QMessageBox.Yes:
            manager.clear()
            self.refresh()

    def _reset_topics(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认重置", "确定恢复默认话题吗？"
        ) == QMessageBox.Yes:
            manager.reset()
            self.refresh()

    def _update_status(self):
        manager = self._manager()
        if manager is None:
            self.topic_status.setText("未启用 | 暂无话题")
            return
        enabled = bool(getattr(self.win, "_topic_enabled", False))
        mode_text = "按顺序" if manager.send_mode == "sequential" else "随机"
        prefix = "已启用" if enabled else "未启用"
        effective = list(manager.runtime_topics)
        selected_count = len(getattr(manager, "selected_topics", []))
        self._refresh_selection_label()
        if effective:
            scope_text = (
                f"当前分类素材 {len(effective)} 条 / "
                f"已确认 {selected_count} 条"
            )
            if manager.send_mode == "sequential":
                next_index = (manager.current_index % len(effective)) + 1
                self.topic_status.setText(
                    f"{prefix} | {scope_text} | {mode_text} | "
                    f"下一个: {next_index}"
                )
            else:
                self.topic_status.setText(
                    f"{prefix} | {scope_text} | {mode_text} | "
                    f"{manager.no_repeat_seconds} 秒内不重复"
                )
        elif selected_count:
            self.topic_status.setText(
                f"{prefix} | 当前分类没有已确认话题"
            )
        else:
            self.topic_status.setText(
                f"{prefix} | 未选择话题（素材库为空）"
            )

        try:
            ai_ready = bool(self.win._ai_ready())
        except Exception:
            ai_ready = False
        client = None
        try:
            client = self.win._ai_client()
        except Exception:
            client = None
        audio_ready = bool(
            client is not None
            and getattr(client, "_audio_bridge", None) is not None
        )
        if ai_ready and audio_ready:
            self.audio_label.setText("音频: 小智语音就绪")
            self.audio_label.setStyleSheet("QLabel { color:#15803D; }")
        elif ai_ready:
            self.audio_label.setText("音频: 小智语音预热中…")
            self.audio_label.setStyleSheet("QLabel { color:#B45309; }")
        elif getattr(self.win, "connected", False):
            self.audio_label.setText("音频: 硬件音频就绪")
            self.audio_label.setStyleSheet("QLabel { color:#15803D; }")
        else:
            self.audio_label.setText("音频: 未就绪（未连接）")
            self.audio_label.setStyleSheet("QLabel { color:#64748B; }")

    def _pressure_manager(self):
        return getattr(self.win, "pressure_manager", None)

    def _selected_pressure_mode(self):
        return (
            "sequential"
            if self.pressure_mode_combo.currentText() == "按顺序"
            else "random"
        )

    def _on_pressure_mode_changed(self, _text):
        manager = self._pressure_manager()
        mode = self._selected_pressure_mode()
        if manager is not None:
            manager.set_mode(mode)
        self.pressure_mode_desc.setText(
            "(按顺序)" if mode == "sequential" else "(随机)"
        )

    def _apply_pressure_interval(self):
        value = _int_value(self.pressure_interval_edit.text(), 60, 1)
        self.pressure_interval_edit.setText(str(value))
        manager = self._pressure_manager()
        if manager is not None:
            manager.set_interval(value)
        if self.pressure_enable_check.isChecked():
            self.win.start_pressure_timer(value, self._selected_pressure_mode())
        self._refresh_pressure()
        self.notify(f"逼单间隔已设为 {value} 秒")

    def _toggle_pressure_timer(self, checked):
        if checked:
            if not getattr(self.win, "connected", False):
                QMessageBox.warning(self, "提示", "请先连接设备")
                self.pressure_enable_check.blockSignals(True)
                self.pressure_enable_check.setChecked(False)
                self.pressure_enable_check.blockSignals(False)
                return
            self.win.start_pressure_timer(
                _int_value(self.pressure_interval_edit.text(), 60, 1),
                self._selected_pressure_mode(),
            )
        else:
            self.win.stop_pressure_timer()
        self.pressure_status_label.setText("已启用" if checked else "未启用")

    def _send_pressure_manual(self):
        text = str(self.pressure_manual_combo.currentData() or "").strip()
        if text and self.win.send_text_async(
            text, "手动逼单消息", True, notify=True
        ):
            self.pressure_manual_combo.setCurrentIndex(0)

    def _add_pressure_message(self):
        text = self.new_pressure_edit.text().strip()
        manager = self._pressure_manager()
        if not text:
            QMessageBox.warning(self, "提示", "请输入逼单消息")
            return
        if len(text) > MAX_PRESSURE_CHARS:
            QMessageBox.warning(
                self,
                "提示",
                f"逼单消息最多{MAX_PRESSURE_CHARS}字，当前{len(text)}字，添加不了。",
            )
            return
        if manager is not None and manager.add(text):
            self.new_pressure_edit.clear()
            self._refresh_pressure()

    def _edit_pressure_message(self):
        row_index = self.pressure_list.currentRow()
        manager = self._pressure_manager()
        if row_index < 0 or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要编辑的消息")
            return
        dialog = TextEditDialog(
            self,
            "编辑逼单消息",
            "编辑消息:",
            manager.messages[row_index],
        )
        if dialog.exec_():
            value = dialog.value().strip()
            if len(value) > MAX_PRESSURE_CHARS:
                QMessageBox.warning(
                    self,
                    "提示",
                    f"逼单消息最多{MAX_PRESSURE_CHARS}字，当前{len(value)}字，保存不了。",
                )
                return
            manager.edit(row_index, value)
            self._refresh_pressure()

    def _delete_pressure_message(self):
        row_index = self.pressure_list.currentRow()
        manager = self._pressure_manager()
        if row_index < 0 or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要删除的消息")
            return
        if QMessageBox.question(
            self, "确认删除", "确定删除选中的逼单消息吗？"
        ) == QMessageBox.Yes:
            manager.delete(row_index)
            self._refresh_pressure()

    def _clear_pressure_messages(self):
        manager = self._pressure_manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认清空", "确定清空所有逼单消息吗？"
        ) == QMessageBox.Yes:
            manager.clear()
            self._refresh_pressure()

    def _reset_pressure_messages(self):
        manager = self._pressure_manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认重置", "确定恢复默认逼单消息吗？"
        ) == QMessageBox.Yes:
            manager.reset()
            self._refresh_pressure()

    def _refresh_pressure(self):
        if not hasattr(self, "pressure_list"):
            return
        data = load_pressure_menu_data()
        manager = self._pressure_manager()
        if manager is not None:
            manager.messages = list(data.get("messages", manager.messages))
            manager.interval = _int_value(
                data.get("interval", manager.interval), 60, 1
            )
            manager.send_mode = str(data.get("send_mode", manager.send_mode))
        send_mode = str(data.get("send_mode", "sequential"))
        self.pressure_interval_edit.setText(str(data.get("interval", 60)))
        self.pressure_mode_combo.blockSignals(True)
        self.pressure_mode_combo.setCurrentText(
            "按顺序" if send_mode == "sequential" else "随机"
        )
        self.pressure_mode_combo.blockSignals(False)
        self.pressure_mode_desc.setText(
            "(按顺序)" if send_mode == "sequential" else "(随机)"
        )
        self.pressure_list.clear()
        for index, message in enumerate(data.get("messages", [])):
            self.pressure_list.addItem(f"{index + 1}. {message}")
        self._fill_manual_combo(
            self.pressure_manual_combo,
            list(data.get("messages", [])),
            "暂无逼单消息，请先在下方添加",
        )
        self.pressure_manual_send_btn.setEnabled(
            self.pressure_manual_combo.count() > 0
            and bool(self.pressure_manual_combo.currentData())
        )
        enabled = bool(getattr(self.win, "_pressure_enabled", False))
        self.pressure_status_label.setText("已启用" if enabled else "未启用")
        home = getattr(self.win, "pages", {}).get("main")
        if home is not None and hasattr(home, "sync_manual_send_options"):
            home.sync_manual_send_options()

    def refresh(self, preserve_draft=False):
        if not hasattr(self, "topic_list"):
            return
        data = load_topics_data()
        manager = self._manager()
        if manager is not None:
            topics = list(manager.topics)
            categories = list(manager.categories)
            active_category = str(manager.active_category)
            mapping = dict(manager.topic_categories)
            selected = set(manager.selected_topics)
            send_mode = str(manager.send_mode)
            no_repeat = int(manager.no_repeat_seconds)
            current_index = int(manager.current_index)
            effective = list(manager.runtime_topics)
        else:
            topics = list(data.get("topics", []))
            categories = list(data.get("categories", TOPIC_CATEGORIES))
            active_category = str(
                data.get("active_category", ALL_TOPIC_CATEGORIES)
            )
            mapping = dict(data.get("topic_categories", {}))
            selected = set(data.get("selected_topics", []))
            send_mode = str(data.get("send_mode", "sequential"))
            no_repeat = _int_value(
                data.get("no_repeat_seconds", self.no_repeat_value),
                self.no_repeat_value,
                1,
            )
            current_index = int(data.get("current_index", 0))
            effective = (
                [topic for topic in topics if topic in selected]
            )
        self.no_repeat_value = no_repeat
        if active_category not in [ALL_TOPIC_CATEGORIES] + categories:
            active_category = ALL_TOPIC_CATEGORIES

        if preserve_draft and self._draft_selected is not None:
            draft = {
                topic for topic in self._draft_selected if topic in topics
            }
        else:
            draft = set(selected)
            self._selection_dirty = False
        self._draft_selected = draft

        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(
            "按顺序" if send_mode == "sequential" else "随机"
        )
        self.mode_combo.blockSignals(False)
        self.mode_desc.setText(
            "(按顺序)" if self._selected_mode() == "sequential" else "(随机)"
        )
        self.no_repeat_edit.setText(str(no_repeat))

        category_counts = {category: 0 for category in categories}
        for topic in topics:
            category = mapping.get(topic, DEFAULT_TOPIC_CATEGORY)
            if category in category_counts:
                category_counts[category] += 1

        self._loading_category_list = True
        self.category_list.clear()
        all_item = QListWidgetItem(
            f"全部分类  ({len(topics)})"
        )
        all_item.setFlags(all_item.flags() | Qt.ItemIsUserCheckable)
        all_item.setData(Qt.UserRole, ALL_TOPIC_CATEGORIES)
        all_item.setCheckState(
            self._check_state_for_category(
                ALL_TOPIC_CATEGORIES, topics, mapping, draft
            )
        )
        self.category_list.addItem(all_item)
        active_row = 0
        for row, category in enumerate(categories, start=1):
            item = QListWidgetItem(
                f"{category}  ({category_counts.get(category, 0)})"
            )
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, category)
            item.setCheckState(
                self._check_state_for_category(
                    category, topics, mapping, draft
                )
            )
            self.category_list.addItem(item)
            if category == active_category:
                active_row = row
        self.category_list.setCurrentRow(active_row)
        self._loading_category_list = False
        self.category_list.setStyleSheet(
            "QListWidget { font-size:9.5pt; background:#F8FAFC; }"
        )

        next_topic = None
        if send_mode == "sequential" and effective:
            next_topic = effective[current_index % len(effective)]

        self._loading_topic_list = True
        self.topic_list.clear()
        category_counters = {}
        for index, topic in enumerate(topics):
            category = mapping.get(topic, DEFAULT_TOPIC_CATEGORY)
            if (
                active_category != ALL_TOPIC_CATEGORIES
                and category != active_category
            ):
                continue
            category_counters[category] = category_counters.get(category, 0) + 1
            local_no = category_counters[category]
            suffix = " ← 下一个" if topic == next_topic else ""
            if active_category == ALL_TOPIC_CATEGORIES:
                text = f"{local_no:02d}. [{category}] {topic}{suffix}"
            else:
                text = f"{local_no:02d}. {topic}{suffix}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, index)
            item.setData(Qt.UserRole + 1, topic)
            item.setCheckState(
                Qt.Checked if topic in draft else Qt.Unchecked
            )
            if topic == next_topic:
                item.setBackground(QColor("#E8F1FF"))
            self.topic_list.addItem(item)
        self._loading_topic_list = False

        manual_topics = list(selected)
        self._fill_manual_combo(
            self.manual_combo,
            manual_topics,
            "请先在列表中勾选话题",
        )
        self.manual_send_btn.setEnabled(
            self.manual_combo.count() > 0
            and bool(self.manual_combo.currentData())
        )
        self._refresh_selection_label()
        self._update_status()
        self._refresh_pressure()

    @staticmethod
    def _fill_manual_combo(combo, items, empty_text):
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for item in items:
            combo.addItem(str(item), str(item))
        if not items:
            combo.addItem(empty_text, "")
        if current:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)


class CategoryPage(PageBase):
    TYPES = [
        ("message", "互动"),
        ("like", "点赞"),
        ("social", "关注"),
        ("gift", "礼物"),
        ("fansclub", "粉丝团"),
        ("share", "分享"),
        ("member", "进入直播间"),
        ("roomUserSeq", "统计信息"),
        ("control", "状态变更"),
        ("roomStats", "直播间状态"),
    ]
    PRIORITY_LABELS = (
        "互动", "点赞", "关注", "礼物", "粉丝团", "分享", "进入直播间",
    )
    PRIORITY_TYPE_MAP = {
        "互动": "message", "点赞": "like", "关注": "social",
        "礼物": "gift", "粉丝团": "fansclub", "分享": "share",
        "进入直播间": "member",
    }

    def __init__(self, window):
        super().__init__("弹幕设置", None, window)
        self.root.setContentsMargins(24, 8, 24, 10)
        self.categories = load_barrage_categories()
        settings = load_category_settings()
        ai_settings = load_ai_barrage_settings()
        priority_settings = load_priority_settings()
        saved_priority_types = (
            priority_settings.get("types")
            or priority_settings.get("priority_types")
            or {}
        )
        saved_priority_levels = (
            priority_settings.get("levels")
            or priority_settings.get("priority_levels")
            or {}
        )
        self.type_checks = {}
        self._syncing = False
        self._loading = True
        self._priority_type_map = self.PRIORITY_TYPE_MAP
        self._priority_status = (
            "已启动" if priority_settings.get("enabled", False) else "未启动"
        )

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(8)

        # 第一行：左侧是“收什么”，右侧是“发什么内容”，并列利用横向空间。
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        control_wrap, control_outer, control_header = _make_group_header(
            "① 弹幕监听"
        )
        control = make_group("")
        control.setStyleSheet("QGroupBox#sourceGroup { margin-top: 0px; }")
        control_layout = QVBoxLayout(control)
        # 增加“小智读弹幕”一行后，压缩卡片内垂直间距，保持卡片高度不增加。
        control_layout.setContentsMargins(12, 8, 12, 8)
        control_layout.setSpacing(5)
        control_outer.addWidget(control, 1)
        top_row.addWidget(control_wrap, 3)

        self.category_enable = make_check(
            "启用弹幕消息分类智能发送",
            settings.get("category_monitor", True),
        )
        self.event_enable = make_check(
            "启用弹幕事件实时监控",
            settings.get("barrage_event_monitor", True),
        )
        self.category_status = QLabel("")
        self.category_status.setWordWrap(False)
        self.category_status.setStyleSheet(
            "QLabel { color:#2563EB; font-size:9pt; }"
        )
        self.category_state = QLabel("")
        self.event_state = QLabel("")
        # 状态紧跟在各自开关后面，避免“已启用”跑到别的开关旁边。
        switch_grid = QGridLayout()
        switch_grid.setHorizontalSpacing(10)
        switch_grid.setVerticalSpacing(4)
        switch_grid.addWidget(self.category_enable, 0, 0)
        switch_grid.addWidget(self.category_state, 0, 1)
        switch_grid.addWidget(self.event_enable, 0, 2)
        switch_grid.addWidget(self.event_state, 0, 3)
        switch_grid.setColumnMinimumWidth(2, 18)
        switch_grid.setColumnStretch(4, 1)
        # 两个模块开关移到卡片外，与标题同一行、右对齐。
        control_header.addLayout(switch_grid)

        # 软件小智与硬件小智共用的一组弹幕朗读设置。
        # 第一行放宽窄的朗读开关，第二行放间隔、状态和测试按钮，
        # 避免窗口变窄后后面的文字被挤压重叠。
        ai_rows = QVBoxLayout()
        ai_rows.setSpacing(4)
        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        ai_hint = QLabel("小智读弹幕")
        ai_hint.setFixedWidth(90)
        ai_hint.setStyleSheet(
            "QLabel { color:#475569; font-size:9.5pt; font-weight:600; }"
        )
        ai_row.addWidget(ai_hint)
        self.auto_barrage_check = make_check(
            "自动读弹幕并回复", bool(ai_settings.get("enabled", True))
        )
        self.welcome_check = make_check(
            "欢迎新观众", bool(ai_settings.get("welcome_enabled", True))
        )
        self.answer_check = make_check(
            "回答提问", bool(ai_settings.get("answer_enabled", True))
        )
        # 旧版“先复述再回答”会改写观众原句，和现在的“原句直发、
        # 由小智自己理解后回答”链路冲突；改成固定状态提示，不再
        # 暴露一个实际不生效的开关。
        read_aloud_hint = QLabel("原句直发")
        read_aloud_hint.setStyleSheet(
            "QLabel { color:#16A34A; font-size:9pt; font-weight:600; }"
        )
        read_aloud_hint.setToolTip(
            "观众问题按原话交给小智，不插入复述、昵称或平台侧拼句"
        )
        ai_row.addWidget(self.auto_barrage_check)
        ai_row.addWidget(self.welcome_check)
        ai_row.addWidget(self.answer_check)
        ai_row.addWidget(read_aloud_hint)
        ai_row.addStretch(1)
        ai_rows.addLayout(ai_row)

        ai_options_row = QHBoxLayout()
        ai_options_row.setSpacing(8)
        options_indent = QLabel("")
        options_indent.setFixedWidth(90)
        ai_options_row.addWidget(options_indent)
        ai_options_row.addWidget(QLabel("回复间隔"))
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 30)
        self.auto_interval.setSuffix(" 秒")
        try:
            self.auto_interval.setValue(
                max(1, int(float(ai_settings.get("min_interval", 4.0))))
            )
        except (TypeError, ValueError):
            self.auto_interval.setValue(4)
        self.auto_interval.setFixedWidth(72)
        ai_options_row.addWidget(self.auto_interval)
        ai_options_row.addWidget(QLabel("欢迎间隔"))
        self.welcome_interval = QSpinBox()
        self.welcome_interval.setRange(5, 180)
        self.welcome_interval.setSuffix(" 秒")
        try:
            self.welcome_interval.setValue(
                max(5, int(float(ai_settings.get("welcome_min_interval", 30.0))))
            )
        except (TypeError, ValueError):
            self.welcome_interval.setValue(30)
        self.welcome_interval.setFixedWidth(72)
        self.welcome_interval.setToolTip(
            "两次欢迎之间的最小间隔；人多时也会按这个间隔排队播报。"
            "若「进入直播间」在优先发送里设为 1 级，则自动改用优先发送间隔，实时欢迎。"
        )
        ai_options_row.addWidget(self.welcome_interval)
        self.auto_status_label = QLabel("等待新弹幕")
        self.auto_status_label.setStyleSheet(
            "QLabel { color:#86909C; font-size:9pt; }"
        )
        self.auto_status_label.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        ai_options_row.addWidget(self.auto_status_label, 1)
        self.test_barrage_btn = make_btn(
            "测试读弹幕", small=True, on_click=self._test_ai_barrage
        )
        ai_options_row.addWidget(self.test_barrage_btn)
        ai_rows.addLayout(ai_options_row)
        control_layout.addLayout(ai_rows)
        self.auto_barrage_check.stateChanged.connect(
            self._save_ai_barrage_settings
        )
        self.welcome_check.stateChanged.connect(
            self._save_ai_barrage_settings
        )
        self.welcome_interval.valueChanged.connect(
            self._save_ai_barrage_settings
        )
        self.answer_check.stateChanged.connect(
            self._save_ai_barrage_settings
        )
        self.auto_interval.valueChanged.connect(
            self._save_ai_barrage_settings
        )

        type_head = QHBoxLayout()
        type_head.setSpacing(8)
        type_hint = QLabel("获取监控弹幕类型")
        type_hint.setStyleSheet(
            "QLabel { color:#475569; font-size:9.5pt; font-weight:600; }"
        )
        type_head.addWidget(type_hint)
        type_head.addWidget(self.category_status)
        type_head.addStretch(1)
        type_head.addWidget(make_btn("全选", on_click=self._select_all_types))
        type_head.addWidget(make_btn("取消全选", on_click=self._clear_all_types))
        control_layout.addLayout(type_head)
        type_grid = QGridLayout()
        type_grid.setHorizontalSpacing(12)
        type_grid.setVerticalSpacing(3)
        type_states = settings.get("barrage_types", {})
        for index, (key, text) in enumerate(self.TYPES):
            check = make_check(
                text,
                bool(
                    type_states.get(
                        key, key in {"message", "like", "social", "gift", "fansclub"}
                    )
                ),
            )
            self.type_checks[key] = check
            check.toggled.connect(self._on_type_changed)
            type_grid.addWidget(check, index // 5, index % 5)
        for column in range(5):
            type_grid.setColumnStretch(column, 1)
        control_layout.addLayout(type_grid)

        dedup_row = QHBoxLayout()
        dedup_row.setSpacing(8)
        dedup_hint = QLabel("点赞去重")
        dedup_hint.setFixedWidth(90)
        dedup_hint.setStyleSheet(
            "QLabel { color:#475569; font-size:9.5pt; font-weight:600; }"
        )
        dedup_row.addWidget(dedup_hint)
        self.like_once = QRadioButton("只发送一次")
        self.like_all = QRadioButton("发送所有点赞")
        self.like_once.setChecked(settings.get("like_dedup_mode", "once") == "once")
        self.like_all.setChecked(settings.get("like_dedup_mode", "once") == "all")
        dedup_row.addWidget(self.like_once)
        dedup_row.addWidget(self.like_all)
        dedup_row.addSpacing(12)
        dedup_row.addWidget(QLabel("去重间隔(秒):"))
        self.like_interval_edit = make_field(
            str(settings.get("like_dedup_interval", "60")), 90
        )
        dedup_row.addWidget(self.like_interval_edit)
        dedup_row.addWidget(
            make_btn("保存设置", on_click=self._save_like_settings)
        )
        dedup_row.addStretch(1)
        control_layout.addLayout(dedup_row)

        keyboard_row = QHBoxLayout()
        keyboard_row.setSpacing(8)
        keyboard_hint = QLabel("键盘输入优先")
        keyboard_hint.setFixedWidth(90)
        keyboard_hint.setStyleSheet(
            "QLabel { color:#475569; font-size:9.5pt; font-weight:600; }"
        )
        keyboard_row.addWidget(keyboard_hint)
        self.keyboard_check = make_check(
            "启用优先处理", settings.get("keyboard_input_priority", True)
        )
        keyboard_row.addWidget(self.keyboard_check)
        keyboard_row.addSpacing(12)
        keyboard_row.addWidget(QLabel("输入间隔(秒):"))
        self.keyboard_interval_edit = make_field(
            str(settings.get("keyboard_input_interval", "0.5")), 80
        )
        keyboard_row.addWidget(self.keyboard_interval_edit)
        keyboard_row.addWidget(
            make_btn("应用设置", on_click=self._save_keyboard)
        )
        keyboard_row.addStretch(1)
        control_layout.addLayout(keyboard_row)

        # 分类预览下方保留现场测试入口（与主页“测试发送”同一套逻辑，两处都能测）
        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        test_hint = QLabel("测试发送")
        test_hint.setFixedWidth(90)
        test_hint.setStyleSheet(
            "QLabel { color:#475569; font-size:9.5pt; font-weight:600; }"
        )
        test_row.addWidget(test_hint)
        test_row.addWidget(QLabel("用户名:"))
        self.cat_test_username = make_field(
            str(settings.get("test_username", "测试用户")), 100
        )
        test_row.addWidget(self.cat_test_username)
        test_row.addWidget(QLabel("数量:"))
        self.cat_test_count = make_field(
            str(settings.get("test_count", "1")), 55
        )
        test_row.addWidget(self.cat_test_count)
        test_row.addWidget(QLabel("礼物名:"))
        self.cat_test_gift = make_field(
            str(settings.get("test_gift", "爱心")), 95
        )
        test_row.addWidget(self.cat_test_gift)
        test_row.addWidget(
            make_btn("发送测试", on_click=self._send_category_test)
        )
        test_row.addStretch(1)
        control_layout.addLayout(test_row)

        # 顶部两块高度由右侧列表决定时，把多余空间留在组底部，
        # 避免中间各行被拉开、行距忽大忽小。
        control_layout.addStretch(1)

        # 右侧：消息列表管理，压缩高度并与左侧分类控制齐平。
        list_group = make_group("② 分类话术")
        list_layout = QVBoxLayout(list_group)
        list_layout.setContentsMargins(12, 8, 12, 8)
        list_layout.setSpacing(6)
        top_row.addWidget(list_group, 2)

        # 分类预览与当前分类的消息列表合并，放在列表上方。
        category_row = QHBoxLayout()
        category_row.setSpacing(8)
        preview_hint = QLabel("分类预览")
        preview_hint.setFixedWidth(90)
        preview_hint.setStyleSheet(
            "QLabel { color:#475569; font-size:9.5pt; font-weight:600; }"
        )
        category_row.addWidget(preview_hint)
        self.category_combo = make_combo(
            [
                ("点赞", "like"),
                ("关注", "follow"),
                ("礼物", "gift"),
                ("粉丝团", "fansclub"),
                ("分享", "share"),
                ("进入直播间", "enter"),
                ("互动", "message"),
            ],
            settings.get("test_category", "like"),
            130,
        )
        category_row.addWidget(self.category_combo)
        self.category_name = QLabel("")
        self.category_name.setStyleSheet("QLabel { color:#2563EB; }")
        category_row.addWidget(self.category_name)
        category_row.addSpacing(8)
        category_row.addWidget(QLabel("发送顺序:"))
        self.category_mode = make_combo(
            [("按顺序", "sequential"), ("随机", "random")],
            "sequential",
            120,
        )
        category_row.addWidget(self.category_mode)
        self.category_mode_desc = QLabel("(按顺序)")
        self.category_mode_desc.setStyleSheet("QLabel { color:#2563EB; }")
        category_row.addWidget(self.category_mode_desc)
        category_row.addStretch(1)
        list_layout.addLayout(category_row)

        list_hint = QLabel(
            "当前分类的消息内容；支持 {username}、{count}、{gift_name} 等变量。"
            f"正文最多 {MAX_CATEGORY_BODY_CHARS} 字，加“请说：”后共 15 字。"
        )
        list_hint.setWordWrap(True)
        list_hint.setStyleSheet(
            "QLabel { color:#64748B; font-size:9pt; }"
        )
        list_layout.addWidget(list_hint)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(QLabel("新消息:"))
        self.new_message_edit = make_field()
        add_row.addWidget(self.new_message_edit, 1)
        add_row.addWidget(make_btn("添加", on_click=self._add_message))
        list_layout.addLayout(add_row)
        self.message_list = QListWidget()
        self.message_list.setFixedHeight(120)
        self.message_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        list_layout.addWidget(self.message_list)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        button_specs = (
            ("编辑", self._edit_message),
            ("删除", self._delete_message),
            ("清空分类", self._clear_messages),
            ("刷新", self.refresh),
            ("重置默认", self._reset_messages),
        )
        for text, handler in button_specs:
            buttons.addWidget(make_btn(text, on_click=handler), 1)
        list_layout.addLayout(buttons)
        list_layout.addStretch(1)
        body.addLayout(top_row)

        # 第三块：发送策略。列表的暂停/继续、手动发送仍放在“弹幕列表”页。
        manage = make_group("③ 发送节奏")
        manage.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        manage.setToolTip(
            "集中设置发送模式与节奏；暂停/继续、手动优先发送在“实时弹幕”页操作。"
        )
        manage_layout = QVBoxLayout(manage)
        manage_layout.setContentsMargins(8, 6, 8, 6)
        manage_layout.setSpacing(4)

        manage_row = QHBoxLayout()
        manage_row.setSpacing(8)
        manage_row.addWidget(QLabel("发送模式:"))
        self.send_mode_combo = make_combo(
            [("普通", "normal"), ("智能", "smart")],
            str(priority_settings.get("send_mode", "normal")),
            110,
        )
        manage_row.addWidget(self.send_mode_combo)
        manage_row.addWidget(QLabel("普通发送间隔(秒):"))
        interval_data = load_barrage_interval_settings()
        self.normal_interval_edit = make_field(
            str(interval_data.get("normal_interval", "10.0")), 82
        )
        manage_row.addWidget(self.normal_interval_edit)
        manage_row.addWidget(QLabel("优先发送间隔(秒):"))
        self.priority_interval_edit = make_field(
            str(interval_data.get("priority_interval", "2.0")), 82
        )
        manage_row.addWidget(self.priority_interval_edit)
        manage_row.addStretch(1)
        manage_layout.addLayout(manage_row)

        manage_actions_row = QHBoxLayout()
        manage_actions_row.setSpacing(8)
        manage_actions_row.addWidget(
            make_btn("应用间隔", on_click=self._save_intervals)
        )
        manage_actions_row.addWidget(
            make_btn("恢复默认", on_click=self._reset_intervals)
        )
        self.send_state_label = QLabel("")
        self.send_state_label.setStyleSheet(
            "QLabel { color:#334155; font-size:9pt; }"
        )
        self.interval_state_label = QLabel("")
        self.interval_state_label.setStyleSheet(
            "QLabel { color:#334155; font-size:9pt; }"
        )
        manage_actions_row.addSpacing(8)
        manage_actions_row.addWidget(self.send_state_label)
        manage_actions_row.addWidget(self.interval_state_label)
        manage_actions_row.addStretch(1)
        manage_layout.addLayout(manage_actions_row)
        manage_layout.addStretch(1)

        # 第四块：优先类型 + 1-10 等级 + 启停。
        priority = make_group("④ 优先发送")
        priority.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        priority.setToolTip(
            "先勾选需要优先发送的类型，再设置等级（1 最高，10 最低），最后启动。"
        )
        priority_layout = QVBoxLayout(priority)
        priority_layout.setContentsMargins(8, 6, 8, 6)
        priority_layout.setSpacing(4)

        self.priority_checks = {}
        self.priority_levels = {}
        priority_grid = QGridLayout()
        priority_grid.setHorizontalSpacing(12)
        priority_grid.setVerticalSpacing(4)
        for column, text in enumerate(self.PRIORITY_LABELS):
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            msg_type = self._priority_type_map[text]
            if isinstance(saved_priority_types, dict):
                checked = bool(
                    saved_priority_types.get(
                        msg_type, saved_priority_types.get(text, False)
                    )
                )
            else:
                checked = (
                    msg_type in saved_priority_types
                    or text in saved_priority_types
                )
            check = make_check(text, checked)
            self.priority_checks[text] = check
            cell_layout.addWidget(check)
            cell_layout.addWidget(QLabel("等级"))
            if isinstance(saved_priority_levels, dict):
                level = saved_priority_levels.get(
                    msg_type, saved_priority_levels.get(text, 5)
                )
            else:
                level = 5
            edit = make_field(str(level or 5), 42)
            self.priority_levels[text] = edit
            cell_layout.addWidget(edit)
            priority_grid.addWidget(cell, column // 4, column % 4)
        for column in range(4):
            priority_grid.setColumnStretch(column, 1)
        priority_layout.addLayout(priority_grid)
        for check in self.priority_checks.values():
            check.toggled.connect(self._persist_priority_choices)
        for edit in self.priority_levels.values():
            edit.editingFinished.connect(self._persist_priority_choices)

        priority_actions = QHBoxLayout()
        priority_actions.setSpacing(8)
        self.start_priority_btn = make_btn(
            "启动优先发送", on_click=self._start_priority
        )
        self.stop_priority_btn = make_btn(
            "停止优先发送", on_click=self._stop_priority
        )
        priority_actions.addWidget(self.start_priority_btn)
        priority_actions.addWidget(self.stop_priority_btn)
        priority_actions.addSpacing(12)
        self.priority_status_label = QLabel("优先发送状态: 未启动")
        self.priority_status_label.setStyleSheet(
            "QLabel { color:#64748B; font-size:9pt; font-weight:600; }"
        )
        priority_actions.addWidget(self.priority_status_label)
        priority_actions.addStretch(1)
        priority_layout.addLayout(priority_actions)
        priority_layout.addStretch(1)

        # ③④ 同一行左右各占一半，避免宽卡片右侧留白。
        manage_priority_row = QHBoxLayout()
        manage_priority_row.setSpacing(8)
        manage_priority_row.addWidget(manage, 1)
        manage_priority_row.addWidget(priority, 1)
        body.addLayout(manage_priority_row)

        # 第五块：原来的“过滤设置”页并入弹幕设置。
        filter_group = make_group("⑤ 弹幕过滤")
        filter_layout = QVBoxLayout(filter_group)
        filter_layout.setContentsMargins(6, 6, 6, 6)
        filter_layout.setSpacing(8)
        self.filter_panel = FilterSettingsPanel(window)
        filter_layout.addWidget(self.filter_panel)
        body.addWidget(filter_group)

        self.category_combo.currentTextChanged.connect(self._on_category_changed)
        self.category_mode.currentTextChanged.connect(self._on_mode_changed)
        self.send_mode_combo.currentTextChanged.connect(self._on_send_mode_changed)
        self.root.addWidget(make_scroll(content), 1)
        self.refresh()
        self._loading = False
        self.category_enable.toggled.connect(self._on_category_enabled)
        self.event_enable.toggled.connect(self._on_event_enabled)
        self._update_priority_state()

    def _manager(self):
        return getattr(self.win, "category_manager", None)

    def _current_category(self):
        return self.category_combo.currentData() or "like"

    def _current_category_mode(self):
        return self.category_mode.currentData() or "sequential"

    def _current_send_mode(self):
        return self.send_mode_combo.currentData() or "normal"

    def _send_category_test(self):
        """在弹幕设置页直接测试当前分类的话术（与主页测试发送一致）。"""
        key = self._current_category()
        manager = self._manager()
        if manager is None:
            QMessageBox.warning(self, "提示", "消息分类管理器未就绪")
            return
        msg_type = CATEGORY_FOR_TYPE.get(key, key)
        context = {
            "username": self.cat_test_username.text().strip() or "测试用户",
            "content": "测试互动内容",
            "count": _int_value(self.cat_test_count.text(), 1, 1),
            "gift_count": _int_value(self.cat_test_count.text(), 1, 1),
            "gift_name": self.cat_test_gift.text().strip() or "爱心",
            "gift_value": 1,
            "total": 0,
            "level": 1,
            "fansclub_name": "粉丝团",
            "share_platform": "抖音",
        }
        text = manager.get_next_message(key, context)
        if not text:
            QMessageBox.warning(self, "提示", "当前分类没有可发送的消息")
            return
        data = load_category_settings()
        data["test_category"] = key
        data["test_username"] = (
            self.cat_test_username.text().strip() or "测试用户"
        )
        data["test_count"] = self.cat_test_count.text().strip() or "1"
        data["test_gift"] = self.cat_test_gift.text().strip() or "爱心"
        save_category_settings(data)
        self.win.send_text_async(text, f"测试{msg_type}", True, notify=True)

    def _save_ai_barrage_settings(self, *_args):
        """保存小智读弹幕设置；软件/硬件通道共用这一份配置。"""
        current = load_ai_barrage_settings()
        save_ai_barrage_settings(
            {
                **current,
                "enabled": self.auto_barrage_check.isChecked(),
                "welcome_enabled": self.welcome_check.isChecked(),
                "answer_enabled": self.answer_check.isChecked(),
                "min_interval": float(self.auto_interval.value()),
                "welcome_min_interval": float(self.welcome_interval.value()),
            }
        )
        responder = getattr(self.win, "ai_responder", None)
        if responder is not None:
            responder.reload_settings()

    def set_auto_status(self, text):
        if hasattr(self, "auto_status_label"):
            value = str(text or "等待新弹幕")
            self.auto_status_label.setText(value)
            self.auto_status_label.setToolTip(value)

    def _test_ai_barrage(self):
        win = self.win
        if getattr(win, "output_channel", "software") == "hardware":
            responder = getattr(win, "ai_responder", None)
            if responder is None:
                QMessageBox.warning(self, "提示", "自动读弹幕服务尚未启动")
                return
            speaker = getattr(win, "hw_speaker", None)
            if speaker is None or not getattr(speaker, "enabled", False):
                QMessageBox.warning(
                    self, "提示", "硬件语音通道未开启或设备 IP 未配置"
                )
                return
            # 测试按钮必须走和真实弹幕同一条队列，否则测不出真实链路。
            responder.submit(
                "message",
                {
                    "id": f"test-{time.time()}",
                    "username": "测试观众",
                    "content": "介绍一下你自己",
                    "msg_type": "message",
                },
                force=True,
            )
            self.set_auto_status("测试弹幕已提交给硬件小智")
            return
        responder = getattr(self.win, "ai_responder", None)
        if responder is None:
            QMessageBox.warning(self, "提示", "自动读弹幕服务尚未启动")
            return
        responder.submit(
            "message",
            {
                "id": f"test-{time.time()}",
                "username": "测试观众",
                "content": "介绍一下你自己",
                "msg_type": "message",
            },
            force=True,
        )

    def _set_switch_state(self, label, enabled):
        label.setText("已启用" if enabled else "未启用")
        color = "#16A34A" if enabled else "#DC2626"
        label.setStyleSheet(
            f"QLabel {{ color:{color}; font-size:9pt; font-weight:600; }}"
        )

    def _update_switch_states(self):
        self._set_switch_state(
            self.category_state, self.category_enable.isChecked()
        )
        self._set_switch_state(self.event_state, self.event_enable.isChecked())

    def _on_category_enabled(self, enabled):
        if self._loading:
            return
        data = self._settings_snapshot()
        data["category_monitor"] = bool(enabled)
        save_category_settings(data)
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.set_category_smart_enabled(bool(enabled))
        self._on_category_changed(self._current_category())
        self.notify("分类智能发送已启用" if enabled else "分类智能发送已停用")

    def _on_event_enabled(self, enabled):
        if self._loading:
            return
        data = self._settings_snapshot()
        data["barrage_event_monitor"] = bool(enabled)
        save_category_settings(data)
        self._update_switch_states()
        if enabled:
            self.win.start_monitoring()
            self.notify("弹幕事件实时监控已启用")
        else:
            if getattr(self.win, "monitoring", False):
                self.win.stop_monitoring()
            self.notify("弹幕事件实时监控已停用")

    def _on_type_changed(self, _checked):
        if self._loading or self._syncing:
            return
        save_category_settings(self._settings_snapshot())
        self._on_category_changed(self._current_category())

    def _select_all_types(self):
        self._syncing = True
        try:
            for check in self.type_checks.values():
                check.setChecked(True)
        finally:
            self._syncing = False
        self._on_type_changed(True)

    def _clear_all_types(self):
        self._syncing = True
        try:
            for check in self.type_checks.values():
                check.setChecked(False)
        finally:
            self._syncing = False
        self._on_type_changed(False)

    def _settings_snapshot(self):
        data = load_category_settings()
        data["category_monitor"] = self.category_enable.isChecked()
        data["barrage_event_monitor"] = self.event_enable.isChecked()
        data["barrage_types"] = {
            key: check.isChecked() for key, check in self.type_checks.items()
        }
        data["like_dedup_mode"] = "once" if self.like_once.isChecked() else "all"
        data["like_dedup_interval"] = self.like_interval_edit.text().strip() or "60"
        data["normal_message_interval"] = self.normal_interval_edit.text().strip() or "3.0"
        data["priority_message_interval"] = (
            self.priority_interval_edit.text().strip() or "2.0"
        )
        data["keyboard_input_priority"] = self.keyboard_check.isChecked()
        data["keyboard_input_interval"] = (
            self.keyboard_interval_edit.text().strip() or "0.5"
        )
        return data

    def _save_like_settings(self):
        data = self._settings_snapshot()
        save_category_settings(data)
        interval = _int_value(self.like_interval_edit.text(), 60, 1)
        save_like_dedup_settings({
            "enabled": self.like_once.isChecked(),
            "dedup_interval": interval,
            "max_likes_per_user": 1 if self.like_once.isChecked() else 999999,
        })
        self._reload_manager_settings()
        self.notify("点赞去重设置已保存")

    def _save_intervals(self):
        normal = _float_value(self.normal_interval_edit.text(), 10.0, 0.0)
        priority = _float_value(self.priority_interval_edit.text(), 2.0, 0.0)
        self.normal_interval_edit.setText(str(normal))
        self.priority_interval_edit.setText(str(priority))
        data = self._settings_snapshot()
        data["normal_message_interval"] = str(normal)
        data["priority_message_interval"] = str(priority)
        save_category_settings(data)
        save_message_interval_settings({
            "min_message_interval": str(normal),
            "priority_message_interval": str(priority),
        })
        save_barrage_interval_settings({
            "normal_interval": normal,
            "priority_interval": priority,
        })
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.set_intervals(normal, priority)
        self._reload_manager_settings()
        self._update_send_controls()
        self.notify("发送间隔已应用")

    def _reset_intervals(self):
        self.normal_interval_edit.setText("10.0")
        self.priority_interval_edit.setText("2.0")
        data = self._settings_snapshot()
        data["normal_message_interval"] = "10.0"
        data["priority_message_interval"] = "2.0"
        save_category_settings(data)
        save_barrage_interval_settings({
            "normal_interval": 10.0,
            "priority_interval": 2.0,
        })
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.set_intervals(10.0, 2.0)
            manager.reload_settings()
        self._update_send_controls()
        self.notify("发送间隔已恢复默认")

    def _save_keyboard(self):
        data = self._settings_snapshot()
        save_category_settings(data)
        save_keyboard_settings({
            "keyboard_input_priority": self.keyboard_check.isChecked(),
            "keyboard_input_interval": self.keyboard_interval_edit.text().strip()
            or "0.5",
        })
        self.notify("键盘输入优先设置已保存")

    def _update_send_controls(self):
        manager = getattr(self.win, "barrage_manager", None)
        if manager is None:
            self.send_state_label.setText("发送状态: 未连接弹幕服务")
        elif getattr(manager, "sending_enabled", True):
            self.send_state_label.setText("发送状态: 运行中")
        else:
            self.send_state_label.setText("发送状态: 已暂停")
        normal = _float_value(self.normal_interval_edit.text(), 10.0, 0.0)
        priority = _float_value(self.priority_interval_edit.text(), 2.0, 0.0)
        self.interval_state_label.setText(
            f"当前节奏: 普通 {normal:g} 秒 / 优先 {priority:g} 秒"
        )

    def _update_priority_state(self):
        settings = load_priority_settings()
        enabled = bool(settings.get("enabled", False))
        mode = str(settings.get("send_mode", "normal"))
        mode_text = "智能" if mode == "smart" else "普通"
        self._priority_status = "已启动" if enabled else "未启动"
        if enabled:
            self.priority_status_label.setText(
                f"优先发送状态: 已启动 ({mode_text})"
            )
            self.priority_status_label.setStyleSheet(
                "QLabel { color:#16A34A; font-size:9pt; font-weight:600; }"
            )
        else:
            self.priority_status_label.setText("优先发送状态: 未启动")
            self.priority_status_label.setStyleSheet(
                "QLabel { color:#64748B; font-size:9pt; font-weight:600; }"
            )
        self.start_priority_btn.setEnabled(not enabled)
        self.stop_priority_btn.setEnabled(enabled)

    def _start_priority(self):
        settings = load_priority_settings()
        selected = {
            self._priority_type_map[key]: check.isChecked()
            for key, check in self.priority_checks.items()
        }
        if not any(selected.values()):
            QMessageBox.warning(
                self, "提示", "请至少勾选一种优先发送类型，再启动。"
            )
            return
        settings["enabled"] = True
        settings["types"] = selected
        settings["levels"] = {
            self._priority_type_map[key]: min(10, _int_value(edit.text(), 5, 1))
            for key, edit in self.priority_levels.items()
        }
        settings["priority_types"] = dict(settings["types"])
        settings["priority_levels"] = dict(settings["levels"])
        settings["send_mode"] = self._current_send_mode()
        save_priority_settings(settings)
        self._reload_priority_runtime()
        self._update_priority_state()
        self.notify(f"优先发送已启动，模式: {settings['send_mode']}")

    def _stop_priority(self):
        settings = load_priority_settings()
        settings["enabled"] = False
        save_priority_settings(settings)
        self._reload_priority_runtime()
        self._update_priority_state()
        self.notify("优先发送设置已停止")

    def _persist_priority_choices(self, *_args):
        """勾选类型/等级改动立即落盘，启动状态下无需再点一次启动。"""
        if not hasattr(self, "priority_checks") or not self.priority_checks:
            return
        settings = load_priority_settings()
        settings["types"] = {
            self._priority_type_map[key]: check.isChecked()
            for key, check in self.priority_checks.items()
        }
        settings["levels"] = {
            self._priority_type_map[key]: min(10, _int_value(edit.text(), 5, 1))
            for key, edit in self.priority_levels.items()
        }
        settings["priority_types"] = dict(settings["types"])
        settings["priority_levels"] = dict(settings["levels"])
        save_priority_settings(settings)
        self._reload_priority_runtime()

    def _reload_priority_runtime(self):
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.reload_priority_settings()
        responder = getattr(self.win, "ai_responder", None)
        if responder is not None:
            responder.reload_settings()

    def _on_send_mode_changed(self, _mode):
        if self._loading:
            return
        mode = self._current_send_mode()
        settings = load_priority_settings()
        settings["send_mode"] = "smart" if mode == "smart" else "normal"
        save_priority_settings(settings)
        self._reload_priority_runtime()
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.set_manual_send_mode(settings["send_mode"])
        self._update_priority_state()

    def _reload_manager_settings(self):
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.reload_settings()

    def _on_category_changed(self, _key=None):
        key = self._current_category()
        manager = self._manager()
        if manager is not None:
            data = manager.categories.get(key, {})
        else:
            data = self.categories.get(key, {})
        name = data.get("name", key)
        mode = data.get("send_mode", "random")
        self.category_name.setText(name)
        self.category_mode.blockSignals(True)
        mode_index = self.category_mode.findData(mode)
        if mode_index >= 0:
            self.category_mode.setCurrentIndex(mode_index)
        self.category_mode.blockSignals(False)
        self._on_mode_changed(mode)
        self.message_list.clear()
        messages = data.get("messages", [])
        current_index = data.get("current_index", 0)
        for index, message in enumerate(messages):
            if mode == "sequential" and index == current_index:
                self.message_list.addItem(f"{index + 1:2d}. {message} ← 下一个")
            else:
                self.message_list.addItem(f"{index + 1:2d}. {message}")
        mode_text = "按顺序" if mode == "sequential" else "随机"
        self._update_switch_states()
        if messages:
            if mode == "sequential":
                self.category_status.setText(
                    f"分类: {name} | 共 {len(messages)} 条消息 | "
                    f"{mode_text} | 下一个: {current_index + 1}"
                )
            else:
                self.category_status.setText(
                    f"分类: {name} | 共 {len(messages)} 条消息 | {mode_text}"
                )
        else:
            self.category_status.setText(f"分类: {name} | 暂无消息")

    def _on_mode_changed(self, _mode=None):
        mode = self._current_category_mode()
        self.category_mode_desc.setText("(按顺序)" if mode == "sequential" else "(随机)")
        key = self._current_category()
        manager = self._manager()
        if manager is not None and not self._syncing:
            manager.set_send_mode(key, mode)
            self.categories = manager.categories

    def _add_message(self):
        key = self._current_category()
        text = self.new_message_edit.text().strip()
        manager = self._manager()
        if not text:
            QMessageBox.warning(self, "提示", "请输入消息内容")
            return
        if not category_message_fits(text):
            QMessageBox.warning(
                self,
                "提示",
                "这条话术加“请说：”后会超过 15 字，"
                f"当前正文 {rendered_category_length(text)} 字，"
                f"最多 {MAX_CATEGORY_BODY_CHARS} 字，添加不了。",
            )
            return
        if manager is not None and manager.add_message(key, text):
            self.new_message_edit.clear()
            self.categories = manager.categories
            self._on_category_changed(key)

    def _edit_message(self):
        key = self._current_category()
        row_index = self.message_list.currentRow()
        manager = self._manager()
        if row_index < 0 or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要编辑的消息")
            return
        messages = manager.messages(key)
        dialog = TextEditDialog(
            self,
            "编辑消息",
            "消息内容:",
            messages[row_index],
            hint=(
                "支持的变量: {username}, {count}, {gift_name}, {gift_value}, "
                "{fansclub_name}, {level}, {share_platform}"
            ),
        )
        if dialog.exec_():
            value = dialog.value().strip()
            if not category_message_fits(value):
                QMessageBox.warning(
                    self,
                    "提示",
                    "这条话术加“请说：”后会超过 15 字，"
                    f"当前正文 {rendered_category_length(value)} 字，"
                    f"最多 {MAX_CATEGORY_BODY_CHARS} 字，保存不了。",
                )
                return
            manager.edit_message(key, row_index, value)
            self.categories = manager.categories
            self._on_category_changed(key)

    def _delete_message(self):
        key = self._current_category()
        row_index = self.message_list.currentRow()
        manager = self._manager()
        if row_index < 0 or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要删除的消息")
            return
        if QMessageBox.question(
            self, "确认删除", "确定删除选中的消息吗？"
        ) == QMessageBox.Yes:
            manager.delete_message(key, row_index)
            self.categories = manager.categories
            self._on_category_changed(key)

    def _clear_messages(self):
        key = self._current_category()
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认清空", f"确定清空分类 '{key}' 的所有消息吗？"
        ) == QMessageBox.Yes:
            manager.clear_messages(key)
            self.categories = manager.categories
            self._on_category_changed(key)

    def _reset_messages(self):
        key = self._current_category()
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认重置", f"确定恢复分类 '{key}' 的默认消息吗？"
        ) == QMessageBox.Yes:
            manager.reset_messages(key)
            self.categories = manager.categories
            self._on_category_changed(key)

    def refresh(self):
        if not hasattr(self, "category_combo"):
            return
        manager = self._manager()
        if manager is not None:
            self.categories = manager.categories
        self._on_category_changed(self._current_category())
        self._update_send_controls()
        self._update_priority_state()
        if hasattr(self, "filter_panel"):
            self.filter_panel.refresh()


class PressurePage(PageBase):
    def __init__(self, window):
        super().__init__("逼单设置", None, window)
        pressure_data = load_pressure_menu_data()
        interval = str(pressure_data.get("interval", 60))
        send_mode = str(pressure_data.get("send_mode", "sequential"))

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(12)

        control = make_group("逼单设置")
        control_layout = QGridLayout(control)
        control_layout.setContentsMargins(12, 14, 12, 12)
        control_layout.setHorizontalSpacing(10)
        control_layout.setVerticalSpacing(10)

        self.enable_check = make_check("启用逼单定时发送")
        self.enable_check.toggled.connect(self._toggle_timer)
        control_layout.addWidget(QLabel("发送间隔(秒):"), 0, 0)
        self.interval_edit = make_field(interval, 90)
        control_layout.addWidget(self.interval_edit, 0, 1)
        control_layout.addWidget(make_btn("修改", on_click=self._apply_interval), 0, 2)
        control_layout.addWidget(QLabel("发送模式:"), 0, 3)
        self.mode_combo = make_combo(["sequential", "random"], send_mode, 120)
        control_layout.addWidget(self.mode_combo, 0, 4)
        self.mode_desc = QLabel("(按顺序)" if send_mode == "sequential" else "(随机)")
        self.mode_desc.setStyleSheet("QLabel { color:#2563EB; }")
        control_layout.addWidget(self.mode_desc, 0, 5)
        self.status_label = QLabel("未启用")

        control_layout.addWidget(QLabel("手动发送逼单消息:"), 1, 0)
        self.manual_edit = make_field()
        control_layout.addWidget(self.manual_edit, 1, 1, 1, 5)
        control_layout.addWidget(
            make_btn("发送", on_click=self._send_manual), 1, 6
        )
        control_layout.setColumnStretch(1, 1)
        body.addWidget(
            _wrap_group_with_header(
                control,
                "逼单设置",
                [self.enable_check, self.status_label],
            )
        )

        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)

        list_group = _add_group(body, "逼单列表", 1)
        list_layout = _set_group_layout(list_group)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(QLabel("新逼单消息:"))
        self.new_message_edit = make_field()
        self.new_message_edit.setPlaceholderText(
            f"最多{MAX_PRESSURE_CHARS}字，超过添加不了"
        )
        add_row.addWidget(self.new_message_edit, 1)
        add_row.addWidget(make_btn("添加", on_click=self._add_message))
        list_layout.addLayout(add_row)
        self.pressure_list = QListWidget()
        self.pressure_list.setMinimumHeight(280)
        list_layout.addWidget(self.pressure_list, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(make_btn("编辑", on_click=self._edit_message))
        buttons.addWidget(make_btn("删除", on_click=self._delete_message))
        buttons.addWidget(make_btn("清空", on_click=self._clear_messages))
        buttons.addWidget(make_btn("重置默认", on_click=self._reset_messages))
        buttons.addWidget(make_btn("刷新", on_click=self.refresh))
        buttons.addStretch(1)
        list_layout.addLayout(buttons)
        self.root.addWidget(make_scroll(content), 1)
        self.refresh()

    def _manager(self):
        return getattr(self.win, "pressure_manager", None)

    def _on_mode_changed(self, mode):
        manager = self._manager()
        if manager is not None:
            manager.set_mode(mode)
        self.mode_desc.setText("(按顺序)" if mode == "sequential" else "(随机)")

    def _apply_interval(self):
        value = _int_value(self.interval_edit.text(), 60, 1)
        self.interval_edit.setText(str(value))
        manager = self._manager()
        if manager is not None:
            manager.set_interval(value)
        if self.enable_check.isChecked():
            self.win.start_pressure_timer(value, self.mode_combo.currentText())
        self.refresh()
        self.notify(f"逼单间隔已设为 {value} 秒")

    def _toggle_timer(self, checked):
        if checked:
            if not getattr(self.win, "connected", False):
                QMessageBox.warning(self, "提示", "请先连接设备")
                self.enable_check.blockSignals(True)
                self.enable_check.setChecked(False)
                self.enable_check.blockSignals(False)
                return
            self.win.start_pressure_timer(
                _int_value(self.interval_edit.text(), 60, 1),
                self.mode_combo.currentText(),
            )
        else:
            self.win.stop_pressure_timer()
        self.status_label.setText("已启用" if checked else "未启用")

    def _send_manual(self):
        text = self.manual_edit.text().strip()
        if text and self.win.send_text_async(
            text, "手动逼单消息", True, notify=True
        ):
            self.manual_edit.clear()

    def _add_message(self):
        text = self.new_message_edit.text().strip()
        manager = self._manager()
        if not text:
            QMessageBox.warning(self, "提示", "请输入逼单消息")
            return
        if len(text) > MAX_PRESSURE_CHARS:
            QMessageBox.warning(
                self,
                "提示",
                f"逼单消息最多{MAX_PRESSURE_CHARS}字，当前{len(text)}字，添加不了。",
            )
            return
        if manager is not None and manager.add(text):
            self.new_message_edit.clear()
            self.refresh()

    def _edit_message(self):
        row_index = self.pressure_list.currentRow()
        manager = self._manager()
        if row_index < 0 or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要编辑的消息")
            return
        dialog = TextEditDialog(
            self,
            "编辑逼单消息",
            "编辑消息:",
            manager.messages[row_index],
        )
        if dialog.exec_():
            value = dialog.value().strip()
            if len(value) > MAX_PRESSURE_CHARS:
                QMessageBox.warning(
                    self,
                    "提示",
                    f"逼单消息最多{MAX_PRESSURE_CHARS}字，当前{len(value)}字，保存不了。",
                )
                return
            manager.edit(row_index, value)
            self.refresh()

    def _delete_message(self):
        row_index = self.pressure_list.currentRow()
        manager = self._manager()
        if row_index < 0 or manager is None:
            QMessageBox.warning(self, "警告", "请先选择要删除的消息")
            return
        if QMessageBox.question(
            self, "确认删除", "确定删除选中的逼单消息吗？"
        ) == QMessageBox.Yes:
            manager.delete(row_index)
            self.refresh()

    def _clear_messages(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认清空", "确定清空所有逼单消息吗？"
        ) == QMessageBox.Yes:
            manager.clear()
            self.refresh()

    def _reset_messages(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认重置", "确定恢复默认逼单消息吗？"
        ) == QMessageBox.Yes:
            manager.reset()
            self.refresh()

    def refresh(self):
        if not hasattr(self, "pressure_list"):
            return
        data = load_pressure_menu_data()
        manager = self._manager()
        if manager is not None:
            manager.messages = list(data.get("messages", manager.messages))
            manager.interval = _int_value(data.get("interval", manager.interval), 60, 1)
            manager.send_mode = str(data.get("send_mode", manager.send_mode))
        self.interval_edit.setText(str(data.get("interval", 60)))
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(str(data.get("send_mode", "sequential")))
        self.mode_combo.blockSignals(False)
        self.mode_desc.setText(
            "(按顺序)" if self.mode_combo.currentText() == "sequential" else "(随机)"
        )
        self.pressure_list.clear()
        for index, message in enumerate(data.get("messages", [])):
            self.pressure_list.addItem(f"{index + 1}. {message}")
        enabled = bool(getattr(self.win, "_pressure_enabled", False))
        self.status_label.setText("已启用" if enabled else "未启用")


class BarrageManagePage(PageBase):
    def __init__(self, window):
        super().__init__("实时弹幕", None, window)
        self.page = 1
        self.page_size = 100
        self._rows = []

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(12)

        list_group = _add_group(body, "实时弹幕", 1)
        list_layout = _set_group_layout(list_group)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(8)
        self.stats_label = QLabel("总弹幕: 0 | 未发送: 0 | 已发送: 0")
        self.stats_label.setStyleSheet(
            "QLabel { color:#0F172A; font-size:10.5pt; font-weight:600; }"
        )
        stats_row.addWidget(self.stats_label)
        stats_row.addSpacing(12)
        stats_row.addWidget(
            make_btn("手动优先发送", on_click=self._manual_priority_send)
        )
        stats_row.addWidget(
            make_btn("重新发送已发送弹幕", on_click=self._requeue_all_sent)
        )
        stats_row.addWidget(make_btn("清空已发送", on_click=self._clear_sent))
        stats_row.addWidget(make_btn("清空所有", on_click=self._clear_all))
        self.resend_btn = make_btn(
            "重新发送选中弹幕", on_click=self._requeue_selected
        )
        self.delete_btn = make_btn(
            "删除选中弹幕", on_click=self._delete_selected
        )
        self.mark_btn = make_btn(
            "标记为已发送", on_click=self._mark_selected
        )
        for button in (self.resend_btn, self.delete_btn, self.mark_btn):
            button.setEnabled(False)
            stats_row.addWidget(button)
        stats_row.addStretch(1)
        stats_row.addWidget(make_btn("刷新", on_click=self.refresh))
        self.pause_btn = make_btn("暂停发送", on_click=self._pause)
        self.resume_btn = make_btn("继续发送", on_click=self._resume)
        self.resume_btn.hide()
        stats_row.addWidget(self.pause_btn)
        stats_row.addWidget(self.resume_btn)
        list_layout.addLayout(stats_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("筛选:"))
        self.filter_combo = make_combo(["全部", "未发送", "已发送"], "全部", 110)
        self.filter_combo.currentTextChanged.connect(self._reset_page)
        filter_row.addWidget(self.filter_combo)
        filter_row.addSpacing(12)
        filter_row.addWidget(QLabel("类型:"))
        self.type_combo = make_combo(
            ["全部", "互动", "点赞", "关注", "礼物", "粉丝团", "分享", "进入直播间", "手动"],
            "全部",
            130,
        )
        self.type_combo.currentTextChanged.connect(self._reset_page)
        filter_row.addWidget(self.type_combo)
        filter_row.addSpacing(12)
        self.filter_count_label = QLabel("")
        self.filter_count_label.setStyleSheet(
            "QLabel { color:#64748B; font-size:9pt; }"
        )
        filter_row.addWidget(self.filter_count_label)
        filter_row.addStretch(1)
        list_layout.addLayout(filter_row)

        self.table = make_table(
            ["ID", "接收时间", "类型", "用户名", "内容", "状态"],
            [],
            widths=[70, 150, 100, 140, 360, 100],
            stretch_col=4,
        )
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC;"
            " gridline-color:#E3E8EF; }"
            "QTableWidget::item { padding:5px; border-right:1px solid #E3E8EF;"
            " border-bottom:1px solid #E3E8EF; }"
            "QHeaderView::section { background:#F7F9FC; color:#64748B; border:none;"
            " border-right:1px solid #E3E8EF; border-bottom:1px solid #EDF0F5;"
            " padding:6px; font-size:9.5pt; font-weight:600; }"
        )
        self.table.setMinimumHeight(360)
        self.table.itemSelectionChanged.connect(self._update_action_buttons)
        list_layout.addWidget(self.table, 1)

        page_row = QHBoxLayout()
        page_row.setSpacing(6)
        self.page_label = QLabel("第 1 页，共 1 页 (总计 0 条)")
        page_row.addWidget(self.page_label)
        page_row.addSpacing(12)
        self.first_btn = make_btn("首页", on_click=self._first_page)
        self.first_btn.setEnabled(False)
        page_row.addWidget(self.first_btn)
        self.prev_btn = make_btn("上一页", on_click=self._prev_page)
        self.prev_btn.setEnabled(False)
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(QLabel("页码:"))
        self.page_edit = make_field("", 55)
        page_row.addWidget(self.page_edit)
        page_row.addWidget(make_btn("跳转", on_click=self._jump_page))
        self.next_btn = make_btn("下一页", on_click=self._next_page)
        self.next_btn.setEnabled(False)
        page_row.addWidget(self.next_btn)
        self.last_btn = make_btn("末页", on_click=self._last_page)
        self.last_btn.setEnabled(False)
        page_row.addWidget(self.last_btn)
        page_row.addSpacing(12)
        page_row.addWidget(QLabel("每页:"))
        self.page_size_combo = make_combo(
            ["50", "100", "200", "500", "1000"], "100", 95
        )
        self.page_size_combo.currentTextChanged.connect(self._page_size_changed)
        page_row.addWidget(self.page_size_combo)
        page_row.addStretch(1)
        list_layout.addLayout(page_row)

        self.root.addWidget(make_scroll(content), 1)
        self.refresh()

    def _manager(self):
        return getattr(self.win, "barrage_manager", None)

    def _reset_page(self, *_args):
        self.page = 1
        self.refresh()

    def _page_size_changed(self, text):
        self.page_size = _int_value(text, 100, 1)
        self.page = 1
        self.refresh()

    def _selected_id(self):
        row_index = self.table.currentRow()
        if row_index < 0:
            return None
        item = self.table.item(row_index, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _selected_ids(self):
        item = self._selected_id()
        return [item] if item is not None else []

    def _update_action_buttons(self):
        has_selection = self._selected_id() is not None
        for button in (self.resend_btn, self.delete_btn, self.mark_btn):
            button.setEnabled(has_selection)

    def _filtered_rows(self):
        manager = self._manager()
        if manager is None:
            return []
        rows = []
        for item in manager.get_all_barrages():
            row_item = dict(item)
            row_item["sent"] = False
            rows.append(row_item)
        rows.extend(dict(item) for item in manager.get_sent_barrages())
        f_type = self.filter_combo.currentText()
        if f_type == "未发送":
            rows = [item for item in rows if not item.get("sent")]
        elif f_type == "已发送":
            rows = [item for item in rows if item.get("sent")]
        label_filter = self.type_combo.currentText()
        if label_filter != "全部":
            rows = [
                item
                for item in rows
                if TYPE_LABELS.get(str(item.get("msg_type", "")), "其它") == label_filter
            ]
        rows.sort(key=lambda item: item.get("received_at", 0), reverse=True)
        return rows

    def refresh(self):
        if not hasattr(self, "table"):
            return
        manager = self._manager()
        counts = {"total": 0, "unsent": 0, "sent": 0}
        if manager is not None:
            counts = manager.get_counts()
        self.stats_label.setText(
            f"总弹幕: {counts['total']} | 未发送: {counts['unsent']} | 已发送: {counts['sent']}"
        )
        rows = self._filtered_rows()
        self._rows = rows
        self.filter_count_label.setText(f"当前筛选: {len(rows)} 条")
        total_pages = max(1, math.ceil(len(rows) / self.page_size))
        self.page = min(max(1, self.page), total_pages)
        start = (self.page - 1) * self.page_size
        page_rows = rows[start:start + self.page_size]
        self.table.setRowCount(0)
        self.table.setRowCount(len(page_rows))
        for row_index, item in enumerate(page_rows):
            values = [
                str(item.get("id", "")),
                str(item.get("timestamp", "")),
                TYPE_LABELS.get(str(item.get("msg_type", "")), "其它"),
                str(item.get("username", "")),
                str(item.get("content", "")),
                "已发送" if item.get("sent") else "未发送",
            ]
            for col_index, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignCenter)
                if row_index % 2 == 1:
                    cell.setBackground(QColor("#F8FAFC"))
                if col_index == 0:
                    cell.setData(Qt.UserRole, item.get("id"))
                if col_index == 5:
                    cell.setForeground(
                        Qt.green if item.get("sent") else Qt.darkYellow
                    )
                self.table.setItem(row_index, col_index, cell)
            self.table.setRowHeight(row_index, 30)
        self.page_label.setText(
            f"第 {self.page} 页，共 {total_pages} 页 (总计 {len(rows)} 条)"
        )
        self.first_btn.setEnabled(self.page > 1)
        self.prev_btn.setEnabled(self.page > 1)
        self.next_btn.setEnabled(self.page < total_pages)
        self.last_btn.setEnabled(self.page < total_pages)
        if manager is not None:
            if manager.sending_enabled:
                self.pause_btn.show()
                self.resume_btn.hide()
            else:
                self.pause_btn.hide()
                self.resume_btn.show()
        self._update_action_buttons()

    def _first_page(self):
        self.page = 1
        self.refresh()

    def _prev_page(self):
        self.page = max(1, self.page - 1)
        self.refresh()

    def _next_page(self):
        self.page += 1
        self.refresh()

    def _last_page(self):
        total_pages = max(1, math.ceil(len(self._rows) / self.page_size))
        self.page = total_pages
        self.refresh()

    def _jump_page(self):
        self.page = _int_value(self.page_edit.text(), self.page, 1)
        self.refresh()

    def _clear_sent(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认清空", "确定清空所有已发送弹幕记录吗？"
        ) == QMessageBox.Yes:
            manager.clear_sent()
            self.refresh()

    def _clear_all(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认清空", "确定清空所有未发送弹幕吗？"
        ) == QMessageBox.Yes:
            manager.clear_all()
            self.refresh()

    def _manual_priority_send(self):
        manager = self._manager()
        if manager is None:
            return
        if not getattr(self.win, "connected", False):
            QMessageBox.warning(self, "提示", "请先连接设备")
            return
        item_id = self._selected_id()
        self.win.force_send_barrage(item_id)

    def _requeue_all_sent(self):
        manager = self._manager()
        if manager is None:
            return
        if QMessageBox.question(
            self, "确认重发", "确定把所有已发送弹幕重新加入发送队列吗？"
        ) == QMessageBox.Yes:
            count = manager.requeue_sent(None)
            self.win.log(f"↩️ 已重新入队 {count} 条弹幕")
            self.refresh()

    def _requeue_selected(self):
        manager = self._manager()
        if manager is None:
            return
        ids = self._selected_ids()
        if not ids:
            return
        count = manager.requeue_sent(ids)
        if count == 0:
            QMessageBox.information(self, "提示", "选中的弹幕不是已发送状态")
        self.refresh()

    def _delete_selected(self):
        manager = self._manager()
        if manager is None:
            return
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(
            self, "确认删除", "确定删除选中的弹幕吗？"
        ) == QMessageBox.Yes:
            manager.delete_ids(ids)
            self.refresh()

    def _mark_selected(self):
        manager = self._manager()
        if manager is None:
            return
        ids = self._selected_ids()
        if not ids:
            return
        manager.mark_ids_sent(ids)
        self.refresh()

    def _pause(self):
        manager = self._manager()
        if manager is not None:
            manager.pause_sending()
            self.refresh()

    def _resume(self):
        manager = self._manager()
        if manager is not None:
            manager.resume_sending()
            self.refresh()


class FilterSettingsPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self._loading = True
        states = load_filter_states()
        filters = states.get("filters", {})
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        body.addLayout(grid)

        duplicate = make_group("过滤重复消息")
        duplicate_layout = _set_group_layout(duplicate)
        self.duplicate_check = make_check(
            "启用重复消息过滤", filters.get("duplicate", True)
        )
        dup_row = QHBoxLayout()
        dup_row.setSpacing(8)
        dup_row.addWidget(QLabel("时间窗口(秒):"))
        self.duplicate_window = make_field(
            str(states.get("duplicate_window", 30)), 75
        )
        dup_row.addWidget(self.duplicate_window)
        dup_row.addWidget(
            make_btn("应用设置", on_click=self._save_duplicate)
        )
        dup_row.addStretch(1)
        duplicate_layout.addLayout(dup_row)
        duplicate_layout.addWidget(label("在时间窗口内只保留一条相同的消息", "muted"))
        duplicate_wrap = _wrap_group_with_header(
            duplicate, "过滤重复消息", [self.duplicate_check]
        )
        grid.addWidget(duplicate_wrap, 0, 0)

        spam = make_group("刷屏检测")
        spam_layout = _set_group_layout(spam)
        self.spam_check = make_check("启用刷屏检测", filters.get("spam", True))
        spam_row = QHBoxLayout()
        spam_row.setSpacing(8)
        spam_row.addWidget(QLabel("时间窗口(秒):"))
        self.spam_window = make_field(str(states.get("spam_window", 10)), 65)
        spam_row.addWidget(self.spam_window)
        spam_row.addWidget(QLabel("消息限制:"))
        self.spam_limit = make_field(str(states.get("spam_limit", 3)), 65)
        spam_row.addWidget(self.spam_limit)
        spam_row.addWidget(make_btn("应用设置", on_click=self._save_spam))
        spam_row.addStretch(1)
        spam_layout.addLayout(spam_row)
        spam_layout.addWidget(label("在时间窗口内只保留一条相同的消息", "muted"))
        spam_wrap = _wrap_group_with_header(
            spam, "刷屏检测", [self.spam_check]
        )
        grid.addWidget(spam_wrap, 0, 1)

        keyword = make_group("关键词过滤")
        keyword_layout = _set_group_layout(keyword)
        self.keyword_check = make_check(
            "启用关键词过滤", filters.get("inappropriate", True)
        )
        keyword_row = QHBoxLayout()
        keyword_row.setSpacing(8)
        keyword_row.addWidget(QLabel("关键词:"))
        self.keyword_edit = make_field()
        keyword_row.addWidget(self.keyword_edit, 1)
        keyword_row.addWidget(make_btn("添加", on_click=self._add_keyword))
        keyword_layout.addLayout(keyword_row)
        self.keyword_list = make_list(load_filter_keywords())
        self.keyword_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.keyword_list.itemDoubleClicked.connect(self._edit_keyword)
        keyword_list_row = QHBoxLayout()
        keyword_list_row.setSpacing(8)
        keyword_list_row.addWidget(self.keyword_list, 1)
        keyword_buttons = QVBoxLayout()
        keyword_buttons.setSpacing(8)
        keyword_buttons.addWidget(
            make_btn("删除选中", on_click=self._delete_keyword)
        )
        keyword_buttons.addWidget(
            make_btn("重置默认", on_click=self._reset_keywords)
        )
        keyword_buttons.addStretch(1)
        keyword_list_row.addLayout(keyword_buttons)
        keyword_layout.addLayout(keyword_list_row)
        keyword_wrap = _wrap_group_with_header(
            keyword, "关键词过滤", [self.keyword_check]
        )
        grid.addWidget(keyword_wrap, 1, 0)

        number = make_group("数字重复过滤")
        number_layout = _set_group_layout(number)
        self.number_check = make_check(
            "启用数字重复模式过滤", states.get("number_enabled", True)
        )
        number_row = QHBoxLayout()
        number_row.setSpacing(8)
        number_row.addWidget(QLabel("时间窗口(秒):"))
        self.number_window = make_field(
            str(states.get("number_window", 10)), 65
        )
        number_row.addWidget(self.number_window)
        number_row.addWidget(make_btn("应用设置", on_click=self._save_number))
        number_row.addStretch(1)
        number_layout.addLayout(number_row)
        pattern_row = QHBoxLayout()
        pattern_row.setSpacing(8)
        pattern_row.addWidget(QLabel("模式:"))
        self.pattern_edit = make_field()
        pattern_row.addWidget(self.pattern_edit, 1)
        pattern_row.addWidget(make_btn("添加", on_click=self._add_pattern))
        number_layout.addLayout(pattern_row)
        self.pattern_list = make_list(load_number_patterns())
        self.pattern_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.pattern_list.itemDoubleClicked.connect(self._edit_number_pattern)
        pattern_list_row = QHBoxLayout()
        pattern_list_row.setSpacing(8)
        pattern_list_row.addWidget(self.pattern_list, 1)
        pattern_buttons = QVBoxLayout()
        pattern_buttons.setSpacing(8)
        pattern_buttons.addWidget(
            make_btn("删除选中", on_click=self._delete_pattern)
        )
        pattern_buttons.addStretch(1)
        pattern_list_row.addLayout(pattern_buttons)
        number_layout.addLayout(pattern_list_row)
        number_wrap = _wrap_group_with_header(
            number, "数字重复过滤", [self.number_check]
        )
        grid.addWidget(number_wrap, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 0)
        grid.setRowStretch(1, 0)
        for group_layout in (
            duplicate_layout,
            spam_layout,
            keyword_layout,
            number_layout,
        ):
            group_layout.setContentsMargins(10, 10, 10, 10)
            group_layout.setSpacing(8)
        for group in (duplicate, spam, keyword, number):
            group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # 两组列表改成按内容撑高，不再用固定 90px 压出内部滚动条。
        grid.setAlignment(keyword_wrap, Qt.AlignTop)
        grid.setAlignment(number_wrap, Qt.AlignTop)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.refresh()
        self._loading = False
        for check in (
            self.duplicate_check,
            self.spam_check,
            self.keyword_check,
            self.number_check,
        ):
            check.toggled.connect(self._on_filter_toggled)

    def notify(self, text):
        if self.win and hasattr(self.win, "toast"):
            self.win.toast(text)

    def _on_filter_toggled(self, _checked):
        if self._loading:
            return
        self._save_states()

    def _save_states(self):
        data = load_filter_states()
        data["enabled"] = True
        data["filters"] = {
            "duplicate": self.duplicate_check.isChecked(),
            "spam": self.spam_check.isChecked(),
            "inappropriate": self.keyword_check.isChecked(),
        }
        data["duplicate_window"] = _int_value(
            self.duplicate_window.text(), 30, 1
        )
        data["spam_window"] = _int_value(self.spam_window.text(), 10, 1)
        data["spam_limit"] = _int_value(self.spam_limit.text(), 3, 1)
        data["number_enabled"] = self.number_check.isChecked()
        data["number_window"] = _int_value(self.number_window.text(), 10, 1)
        save_filter_states(data)
        save_filter_keywords(
            self._keywords(), enabled=self.keyword_check.isChecked()
        )
        save_number_patterns(
            self._patterns(), enabled=self.number_check.isChecked()
        )
        self._reload_manager()

    def _reload_manager(self):
        manager = getattr(self.win, "barrage_manager", None)
        if manager is not None:
            manager.reload_settings()

    def _save_duplicate(self):
        self._save_states()
        self.notify("重复消息过滤设置已应用")

    def _save_spam(self):
        self._save_states()
        self.notify("刷屏检测设置已应用")

    def _save_number(self):
        self._save_states()
        save_number_patterns(
            self._patterns(), enabled=self.number_check.isChecked()
        )
        self.notify("数字重复过滤设置已应用")

    def _keywords(self):
        return [
            self.keyword_list.item(index).text()
            for index in range(self.keyword_list.count())
        ]

    def _patterns(self):
        return [
            self.pattern_list.item(index).text()
            for index in range(self.pattern_list.count())
        ]

    def _persist_keywords(self):
        save_filter_keywords(
            self._keywords(), enabled=self.keyword_check.isChecked()
        )
        self._reload_manager()

    def _persist_patterns(self):
        save_number_patterns(
            self._patterns(), enabled=self.number_check.isChecked()
        )
        self._reload_manager()

    def _add_keyword(self):
        text = self.keyword_edit.text().strip()
        if not text:
            return
        if text not in self._keywords():
            self.keyword_list.addItem(text)
            self._sync_list_heights()
            self._persist_keywords()
        self.keyword_edit.clear()

    def _edit_keyword(self, item):
        dialog = TextEditDialog(self, "编辑关键词", "关键词:", item.text())
        if dialog.exec_() and dialog.value():
            item.setText(dialog.value())
            self._persist_keywords()

    def _delete_keyword(self):
        row_index = self.keyword_list.currentRow()
        if row_index < 0:
            QMessageBox.warning(self, "警告", "请先选择要删除的关键词")
            return
        self.keyword_list.takeItem(row_index)
        self._sync_list_heights()
        self._persist_keywords()

    def _reset_keywords(self):
        self.keyword_list.clear()
        self._sync_list_heights()
        self._persist_keywords()
        self.notify("关键词列表已清空")

    def _add_pattern(self):
        text = self.pattern_edit.text().strip()
        if not text:
            return
        if text not in self._patterns():
            self.pattern_list.addItem(text)
            self._sync_list_heights()
            self._persist_patterns()
        self.pattern_edit.clear()

    def _edit_number_pattern(self, item):
        dialog = TextEditDialog(
            self, "编辑数字重复模式", "数字重复模式:", item.text()
        )
        if dialog.exec_() and dialog.value():
            item.setText(dialog.value())
            self._persist_patterns()

    def _delete_pattern(self):
        row_index = self.pattern_list.currentRow()
        if row_index < 0:
            QMessageBox.warning(self, "警告", "请先选择要删除的数字模式")
            return
        self.pattern_list.takeItem(row_index)
        self._sync_list_heights()
        self._persist_patterns()

    def _sync_list_heights(self):
        """按当前条目数撑高列表，最多 20 行，避免固定高度内滚动。"""
        for widget in (self.keyword_list, self.pattern_list):
            count = widget.count()
            rows = max(3, min(count, 20)) if count else 3
            row_height = widget.sizeHintForRow(0)
            if row_height <= 0:
                row_height = widget.fontMetrics().height() + 8
            height = rows * row_height + 2 * widget.frameWidth() + 4
            widget.setFixedHeight(max(84, height))

    def refresh(self):
        if not hasattr(self, "keyword_list"):
            return
        self._loading = True
        states = load_filter_states()
        filters = states.get("filters", {})
        self.duplicate_check.setChecked(bool(filters.get("duplicate", True)))
        self.spam_check.setChecked(bool(filters.get("spam", True)))
        self.keyword_check.setChecked(bool(filters.get("inappropriate", True)))
        self.number_check.setChecked(bool(states.get("number_enabled", True)))
        self.duplicate_window.setText(str(states.get("duplicate_window", 30)))
        self.spam_window.setText(str(states.get("spam_window", 10)))
        self.spam_limit.setText(str(states.get("spam_limit", 3)))
        self.number_window.setText(str(states.get("number_window", 10)))
        self._loading = False
        self.keyword_list.clear()
        self.keyword_list.addItems(load_filter_keywords())
        self.pattern_list.clear()
        self.pattern_list.addItems(load_number_patterns())
        self._sync_list_heights()
