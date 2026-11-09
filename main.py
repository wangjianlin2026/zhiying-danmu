import ipaddress
import os


def _hide_startup_console():
    """双击 py/bat 启动时隐藏黑色控制台；调试时设 XIAOZHI_KEEP_CONSOLE=1。"""
    if os.name != "nt" or os.environ.get("XIAOZHI_KEEP_CONSOLE") == "1":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


# 尽早隐藏控制台，避免导入 PyQt5 等模块期间一直露着黑框。
_hide_startup_console()

import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


_CRASH_LOG_FILE = None

# 硬件小智一次回答通常要十几秒；低于这个间隔连续灌文本会让设备
# ACK 后不出声，最后整条语音链路卡死。自动话题/逼单统一兜底。
MIN_HARDWARE_AUTO_INTERVAL = 20.0


def _clamp_hardware_interval(value) -> int:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = MIN_HARDWARE_AUTO_INTERVAL
    return int(max(MIN_HARDWARE_AUTO_INTERVAL, seconds))


def _install_crash_logging():
    """把未捕获异常和原生崩溃写入 data/crash.log，便于排查静默退出。"""
    global _CRASH_LOG_FILE
    try:
        import faulthandler
        import traceback

        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "crash.log")
        _CRASH_LOG_FILE = open(log_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_CRASH_LOG_FILE, all_threads=True)

        def _sys_hook(exc_type, exc_value, exc_tb):
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _CRASH_LOG_FILE.write(f"\n[{stamp}] 主线程未捕获异常\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=_CRASH_LOG_FILE)

        def _thread_hook(args):
            if args.exc_type is SystemExit:
                return
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _CRASH_LOG_FILE.write(
                f"\n[{stamp}] 线程 {getattr(args.thread, 'name', '?')} 未捕获异常\n"
            )
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback, file=_CRASH_LOG_FILE
            )

        sys.excepthook = _sys_hook
        threading.excepthook = _thread_hook
        _CRASH_LOG_FILE.write(
            f"\n=== 启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n"
        )
    except Exception:
        pass


_install_crash_logging()

from PyQt5.QtCore import QLockFile, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from data_access import (
    data_path,
    ensure_data_files,
    load_ai_barrage_settings,
    load_category_settings,
    load_connection_settings,
    load_devices,
    load_json,
    load_pressure_menu_data,
    load_topics_data,
    save_connection_settings,
    save_devices,
    save_topics_data,
)
from pages import (
    AiChatPage,
    AiSettingsPage,
    BarrageManagePage,
    CategoryPage,
    CollectorPage,
    CommunicationLogPage,
    MainPage,
    PressurePage,
    TopicPage,
    open_device_activation_dialog,
    open_license_dialog,
)
from services.barrage_manager import BarrageManager
from services.ai_barrage_responder import AiBarrageResponder
from services.category_manager import CategoryManager
from services.collector import CollectorService
from services.device_state import DeviceState, DeviceStateMachine
from services.hardware_ai_bridge import HardwareAiBridge
from services.hardware_speaker import HardwareSpeaker
from services.hardware_text import (
    MAX_PROMPT_CHARS,
    build_hardware_message,
    build_prompt,
)
from services.license import LicenseManager, machine_code
from services.manual_input_policy import ManualInputPolicy
from services.pressure_manager import PressureManager
from services.topic_manager import TopicManager
from services.udp_client import UdpClient, response_ok
from services.ws_monitor import WsMonitor
from cover import CoverPage
from theme import APP_QSS


APP_TITLE = "小智AI弹幕自动回复系统"
SKIP_BARRAGE_TYPES = {"roomUserSeq", "control", "roomStats"}
BARRAGE_TYPE_ALIASES = {"follow": "social", "enter": "member"}


def is_barrage_type_enabled(settings: dict, kind: str) -> bool:
    """设置页的 social/member 开关同时覆盖 follow/enter 事件别名。"""
    flags = settings.get("barrage_types", {}) if isinstance(settings, dict) else {}
    canonical = BARRAGE_TYPE_ALIASES.get(str(kind), str(kind))
    return bool(flags.get(str(kind), flags.get(canonical, True)))


class NavButton(QPushButton):
    """一级菜单下的树状节点，左侧绘制白色枝干线。"""

    def __init__(self, text, last=False):
        super().__init__(text)
        self._last = last

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidth(1)
        painter.setPen(pen)
        trunk_x = 34
        mid_y = self.height() // 2
        if self._last:
            painter.drawLine(trunk_x, 0, trunk_x, mid_y)
        else:
            painter.drawLine(trunk_x, 0, trunk_x, self.height())
        painter.drawLine(trunk_x, mid_y, trunk_x + 12, mid_y)
        painter.end()


class Sidebar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setFixedWidth(244)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 14)
        root.setSpacing(0)
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.buttons = {}

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 2, 4, 8)
        brand_row.setSpacing(9)
        brand_logo = QLabel()
        brand_logo.setObjectName("sideBrandLogo")
        brand_logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "assets",
            "zhiying-logo.png",
        )
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            brand_logo.setPixmap(
                pixmap.scaled(
                    34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        brand_row.addWidget(brand_logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_title = QLabel("智映AI")
        brand_title.setObjectName("sideBrandTitle")
        brand_sub = QLabel("小智AI弹幕自动回复系统")
        brand_sub.setObjectName("sideBrandSub")
        brand_text.addWidget(brand_title)
        brand_text.addWidget(brand_sub)
        brand_row.addLayout(brand_text, 1)
        root.addLayout(brand_row)

        sections = [
            ("直播总控", [
                ("直播总览", "main"), ("通信日志", "logs"),
                ("实时弹幕", "barrage"),
                ("消息管理", "messages"),
                ("弹幕设置", "categories"),
            ]),
            ("弹幕采集", [("采集控制", "collector")]),
            ("小智助手", [
                ("小智对话", "ai_chat"), ("参数设置", "ai_settings"),
            ]),
        ]
        for index, (section, entries) in enumerate(sections):
            if index:
                root.addSpacing(12)
            group_label = QLabel(section)
            group_label.setObjectName("navGroup")
            root.addWidget(group_label)
            for entry_index, (text, key) in enumerate(entries):
                self._add_nav(root, text, key, entry_index == len(entries) - 1)
        root.addStretch(1)

        footer = QFrame()
        footer.setObjectName("sideFooter")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        foot = QVBoxLayout(footer)
        foot.setContentsMargins(0, 10, 0, 0)
        foot.setSpacing(8)

        self.home_btn = QPushButton()
        self.home_btn.setObjectName("sideHome")
        self.home_btn.setAccessibleName("回到封面")
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.setToolTip("回到封面页（可重新激活授权、重新进入）")
        self.home_btn.setFixedHeight(40)
        self.home_btn.clicked.connect(self.win.show_cover)
        home_row = QHBoxLayout(self.home_btn)
        home_row.setContentsMargins(8, 5, 10, 5)
        home_row.setSpacing(9)
        home_icon = QLabel("←")
        home_icon.setObjectName("sideHomeIcon")
        home_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        home_icon.setAlignment(Qt.AlignCenter)
        home_icon.setFixedSize(26, 26)
        home_text = QLabel("回到封面")
        home_text.setObjectName("sideHomeText")
        home_text.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        home_row.addWidget(home_icon)
        home_row.addWidget(home_text, 1)
        foot.addWidget(self.home_btn)

        self.license_btn = QPushButton()
        self.license_btn.setObjectName("sideLicense")
        self.license_btn.setAccessibleName("授权状态")
        self.license_btn.setCursor(Qt.PointingHandCursor)
        self.license_btn.setToolTip("查看 / 激活授权")
        self.license_btn.setFixedHeight(46)
        self.license_btn.clicked.connect(
            lambda: open_license_dialog(self.win, self)
        )
        lic_row = QHBoxLayout(self.license_btn)
        lic_row.setContentsMargins(8, 5, 10, 5)
        lic_row.setSpacing(9)
        self.license_avatar = QLabel("!")
        self.license_avatar.setObjectName("sideLicenseAvatar")
        self.license_avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.license_avatar.setAlignment(Qt.AlignCenter)
        self.license_avatar.setFixedSize(30, 30)
        lic_text = QVBoxLayout()
        lic_text.setSpacing(0)
        self.license_title = QLabel("检测授权中")
        self.license_title.setObjectName("sideLicenseTitle")
        self.license_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.license_sub = QLabel("读取中…")
        self.license_sub.setObjectName("sideLicenseSub")
        self.license_sub.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lic_text.addWidget(self.license_title)
        lic_text.addWidget(self.license_sub)
        lic_row.addWidget(self.license_avatar)
        lic_row.addLayout(lic_text, 1)
        foot.addWidget(self.license_btn)

        self.exit_btn = QPushButton("退出程序")
        self.exit_btn.setObjectName("sideExit")
        self.exit_btn.setCursor(Qt.PointingHandCursor)
        self.exit_btn.setToolTip("退出程序")
        self.exit_btn.clicked.connect(self.win.close)
        foot.addWidget(self.exit_btn)
        root.addWidget(footer)

    def set_license(self, title, sub, activated=False):
        self.license_title.setText(title)
        self.license_sub.setText(sub)
        if activated:
            self.license_avatar.setText("✓")
            self.license_btn.setStyleSheet(
                "QPushButton#sideLicense { background:#1E3A2C; "
                "border:1px solid #2F6B49; border-radius:10px; "
                "text-align:left; }"
                "QPushButton#sideLicense:hover { background:#234635; "
                "border-color:#3C7E58; }"
            )
            self.license_avatar.setStyleSheet(
                "QLabel#sideLicenseAvatar { border-radius:15px; "
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:1, "
                "stop:0 #D7F2DF, stop:1 #C9EDDB); "
                "color:#12915F; font-size:11pt; font-weight:900; }"
            )
            self.license_title.setStyleSheet(
                "QLabel#sideLicenseTitle { background:transparent; "
                "color:#7BE3AC; font-size:9.5pt; font-weight:800; }"
            )
            self.license_sub.setStyleSheet(
                "QLabel#sideLicenseSub { background:transparent; "
                "color:#9FB3A6; font-size:7.5pt; font-weight:500; }"
            )
        else:
            self.license_avatar.setText("!")
            self.license_btn.setStyleSheet(
                "QPushButton#sideLicense { background:#2E343D; "
                "border:1px solid #3B424C; border-radius:10px; "
                "text-align:left; }"
                "QPushButton#sideLicense:hover { background:#3A414B; "
                "border-color:#4A515C; }"
            )
            self.license_avatar.setStyleSheet(
                "QLabel#sideLicenseAvatar { border-radius:15px; "
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:1, "
                "stop:0 #3B4250, stop:1 #343A45); "
                "color:#9EA7B7; font-size:11pt; font-weight:900; }"
            )
            self.license_title.setStyleSheet(
                "QLabel#sideLicenseTitle { background:transparent; "
                "color:#E5E7EB; font-size:9.5pt; font-weight:700; }"
            )
            self.license_sub.setStyleSheet(
                "QLabel#sideLicenseSub { background:transparent; "
                "color:#9CA3AF; font-size:7.5pt; font-weight:500; }"
            )

    def _add_nav(self, layout, text, key, last=False):
        button = NavButton(text, last)
        button.setObjectName("nav")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, page_key=key: self.win.go_page(page_key))
        self.button_group.addButton(button)
        self.buttons[key] = button
        layout.addWidget(button)

    def select(self, key):
        button = self.buttons.get(key)
        if button is not None:
            button.setChecked(True)


class MainWindow(QMainWindow):
    connection_success = pyqtSignal(dict)
    connection_failure = pyqtSignal(str)
    connection_lost = pyqtSignal(str)
    status_received = pyqtSignal(object)
    operation_result = pyqtSignal(str, bool, str)
    barrage_event = pyqtSignal(str, object)
    monitor_status = pyqtSignal(bool, str)
    log_signal = pyqtSignal(str)
    scan_done = pyqtSignal(list)
    device_discovered = pyqtSignal(dict)
    barrage_refresh_request = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._shutting_down = False
        self._category_settings_cache = None
        self._category_settings_mtime = 0.0
        self._barrage_refresh_pending = False
        self._last_heartbeat_at = time.monotonic()
        self._barrage_event_count = 0
        self._last_stability_sample = time.monotonic()
        ensure_data_files()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1100, 680)

        self._init_services()
        self._connect_signals()

        root = QWidget()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = Sidebar(self)
        layout.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.stack, 1)
        self.workspace_root = root
        self.central_stack = QStackedWidget()
        self.central_stack.addWidget(root)
        self.setCentralWidget(self.central_stack)

        # 悬浮提示：手动发送/测试发送被受理、成功或失败时直接显示，
        # 不再只写日志让用户“按了没动静”。
        self._toast_label = QLabel("", self)
        self._toast_label.setAlignment(Qt.AlignCenter)
        self._toast_label.setWordWrap(True)
        self._toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(4500)
        self._toast_timer.timeout.connect(self._toast_label.hide)

        self.pages = {}
        page_types = [
            ("main", MainPage), ("messages", TopicPage),
            ("categories", CategoryPage),
            ("barrage", BarrageManagePage),
            ("collector", CollectorPage), ("ai_chat", AiChatPage),
            ("ai_settings", AiSettingsPage),
            ("logs", CommunicationLogPage),
        ]
        for key, page_type in page_types:
            page = page_type(self)
            self.pages[key] = page
            self.stack.addWidget(page)
        ai_page = self.pages.get("ai_chat")
        categories_page = self.pages.get("categories")
        main_page = self.pages.get("main")
        if ai_page is not None and hasattr(ai_page, "ai_client"):
            ai_page.ai_client.status_changed.connect(
                self._on_ai_service_status
            )
            self.ai_responder = AiBarrageResponder(
                ai_page.ai_client,
                log=self.log,
                parent=self,
                # 必须和弹幕设置页共用同一个分类管理器，否则页面里改的
                # 分类话术/顺序只写进了另一个实例，朗读队列要重启才生效。
                category_manager=self.category_manager,
            )
            if categories_page is not None and hasattr(
                categories_page, "set_auto_status"
            ):
                self.ai_responder.status_changed.connect(
                    categories_page.set_auto_status
                )
            if main_page is not None:
                connected = bool(
                    getattr(ai_page.ai_client, "_connected", False)
                )
                main_page.on_software_status(
                    connected, "" if connected else "正在连接..."
                )
        self.cover = CoverPage(self)
        self.central_stack.insertWidget(0, self.cover)
        self.central_stack.setCurrentWidget(self.cover)
        self.go_page("main")
        # 按上次保存的语音通道恢复（硬件/软件只能一路出声）
        self.set_output_channel(self.output_channel)
        # 重启软件后自动恢复采集器和弹幕事件监控，否则界面显示正常但收不到弹幕
        QTimer.singleShot(800, self._auto_start_services)
        # 启动后自动连接上次保存的小智设备，连不上就每 15 秒重试
        self._auto_connect_timer = QTimer(self)
        self._auto_connect_timer.setInterval(15000)
        self._auto_connect_timer.timeout.connect(self._auto_connect_device)
        if self._hardware_channel_active():
            self._auto_connect_timer.start()
            QTimer.singleShot(1500, self._auto_connect_device)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._poll_status)
        self._barrage_refresh_timer = QTimer(self)
        self._barrage_refresh_timer.setSingleShot(True)
        self._barrage_refresh_timer.setInterval(250)
        self._barrage_refresh_timer.timeout.connect(self.refresh_barrage_page)
        self._send_timer = QTimer(self)
        self._send_timer.setInterval(600)
        self._send_timer.timeout.connect(self._schedule_barrage_send)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        # 弹幕接收线程看门狗：线程异常退出后自动重建，避免界面正常但完全收不到弹幕。
        self._monitor_watchdog_timer = QTimer(self)
        self._monitor_watchdog_timer.setInterval(5000)
        self._monitor_watchdog_timer.timeout.connect(self._check_monitor_health)
        self._monitor_watchdog_timer.start()
        self._topic_timer = QTimer(self)
        self._topic_timer.setSingleShot(True)
        self._topic_timer.timeout.connect(self._on_topic_tick)
        self._pressure_timer = QTimer(self)
        self._pressure_timer.setSingleShot(True)
        self._pressure_timer.timeout.connect(self._on_pressure_tick)
        self._topic_enabled = False
        self._topic_interval = 30
        self._pressure_enabled = False
        self._send_busy = False
        self._hw_last_text = ""
        self._hw_last_text_at = 0.0
        self._hw_last_send_at = 0.0
        self._last_hw_prompt = ""
        self._last_hw_error = ""
        self._status_busy = False
        self._connected_at = None
        self._status_failures = 0
        # 手动话题/逼单/测试发送统一排队，避免连点或小智正在朗读时
        # 直接被丢弃；定时话题仍走自己的安全闸门，不能插队。
        self._manual_send_queue = []
        self._manual_send_timer = QTimer(self)
        self._manual_send_timer.setInterval(1000)
        self._manual_send_timer.timeout.connect(self._drain_manual_send_queue)
        # 设备偶发卡在 speaking：屏幕有字但喇叭不出声，且永远不回 idle。
        # 超过阈值仍卡住时主动唤醒音频并放开队列，避免整场直播被一条卡死。
        self._speaking_since = 0.0
        self._stuck_speaking_recover_at = 0.0
        # 用音频包计数判断设备到底还在不在播放：长回答说 40 秒是正常的，
        # 只要 audio_rx_total 还在涨就不能算卡死，否则会误判并触发重启。
        self._last_audio_rx_total = -1
        self._last_audio_progress_at = 0.0

    def _auto_start_services(self):
        """启动时自动恢复上次的采集器和弹幕事件监控。"""
        if self._shutting_down:
            return
        try:
            settings = load_json("collector_settings.json")
        except Exception:
            settings = {}
        live_id = str(
            settings.get("last_web_rid") or settings.get("last_live_input") or ""
        ).strip()
        if live_id:
            def worker():
                try:
                    if not self.collector_service.status().get("process_running"):
                        ok, message = self.collector_service.start(live_id)
                        self.log(("✅ " if ok else "❌ ") + message)
                except Exception as exc:
                    self.log(f"❌ 自动启动采集器失败: {exc}")

            threading.Thread(target=worker, daemon=True).start()
        try:
            settings = load_category_settings()
        except Exception:
            settings = {}
        if settings.get("barrage_event_monitor", True):
            QTimer.singleShot(2500, self.start_monitoring)
        # 话题定时发送不能只在“软件小智连接成功”时恢复；硬件通道也要
        # 在启动后按 auto_start 自动拉起，否则界面显示开启但定时器没跑。
        QTimer.singleShot(1500, self._maybe_auto_start_topic_timer)
        # 逼单定时器同样按上次开关状态恢复。
        QTimer.singleShot(1600, self._maybe_auto_start_pressure_timer)
        # 调试用：data/test_inject.txt 存在时，按行注入几条模拟弹幕，
        # 走和真实弹幕完全相同的处理链路，注入后自动删除该文件。
        QTimer.singleShot(8000, self._run_test_inject)

    def _run_test_inject(self):
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data",
            "test_inject.txt",
        )
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        except OSError:
            return
        for index, text in enumerate(lines):
            QTimer.singleShot(
                1500 * index,
                lambda t=text: self._on_barrage_event(
                    "message",
                    {
                        "id": int(time.time() * 1000) + index,
                        "username": "测试观众",
                        "content": t,
                        "msg_type": "message",
                    },
                ),
            )
        try:
            os.remove(path)
        except OSError:
            pass
        self.log("🧪 已注入测试弹幕: " + " / ".join(lines))

    def _init_services(self):
        self.machine_code = machine_code()
        self.license_manager = LicenseManager()
        self.category_manager = CategoryManager()
        self.topic_manager = TopicManager()
        self.pressure_manager = PressureManager()
        self.manual_input_policy = ManualInputPolicy()
        self.collector_service = CollectorService()
        self.device_state = DeviceStateMachine()
        self.udp = None
        # 未连接硬件设备时也要接弹幕：先创建管理器，设备连上后只替换 udp 客户端
        self.barrage_manager = BarrageManager(
            self.device_state,
            None,
            log=lambda text: self.log_signal.emit(text),
            category_manager=self.category_manager,
        )
        self.ws_monitor = None
        self.monitoring = False
        self._auto_connect_enabled = True
        self.connected = False
        self._send_lock = threading.RLock()
        # 语音通道：hardware=小智硬件喇叭，software=小智电脑语音
        self.output_channel = self._load_output_channel()
        settings = load_connection_settings()
        # 硬件通道串行化：弹幕与手动/定时消息共用同一把锁，避免同时灌文本。
        self._hw_send_lock = threading.Lock()
        self.hw_speaker = HardwareSpeaker(
            host=str(settings.get("host", "") or ""),
            port=int(settings.get("port", 8888) or 8888),
            log=lambda text: self.log_signal.emit(text),
        )
        self.hw_ai = HardwareAiBridge(
            self.hw_speaker,
            log=lambda text: self.log_signal.emit(text),
            parent=self,
            send_lock=self._hw_send_lock,
        )
        # 朗读失败说明设备 TTS 已卡住；调度器会自动唤醒音频，这里再立即
        # 释放主程序侧的 speaking 状态，避免还要多等几十秒的全局兜底。
        try:
            self.hw_ai.scheduler.item_done.connect(self._on_hw_speech_done)
        except Exception:
            pass
        self._auto_discover_busy = False
        self._last_auto_discover_at = 0.0

    # ---------- 语音通道 ----------
    def _output_channel_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data",
            "output_channel.txt",
        )

    def _load_output_channel(self) -> str:
        try:
            with open(self._output_channel_path(), "r", encoding="utf-8") as f:
                value = f.read().strip().lower()
        except OSError:
            return "software"
        return value if value in {"hardware", "software"} else "software"

    def _hardware_channel_active(self) -> bool:
        """硬件语音通道是否激活；软件通道下硬件链路必须整体关闭。"""
        return str(getattr(self, "output_channel", "software")).lower() == "hardware"

    def set_output_channel(self, mode: str) -> None:
        mode = "hardware" if str(mode).lower() == "hardware" else "software"
        self.output_channel = mode
        try:
            path = self._output_channel_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(mode)
        except OSError as exc:
            self.log(f"⚠️ 保存语音通道失败: {exc}")
        self.hw_speaker.set_enabled(mode == "hardware")
        if hasattr(self, "barrage_manager"):
            self.barrage_manager.set_text_sender(
                self._send_hardware_text if mode == "hardware" else None
            )
        main_page = self.pages.get("main") if hasattr(self, "pages") else None
        if main_page is not None and hasattr(main_page, "on_output_channel"):
            main_page.on_output_channel(mode)
        responder = getattr(self, "ai_responder", None)
        if mode == "hardware":
            # 两条通道严格互斥：硬件模式下真正停掉软件小智，不是只改状态文字。
            self.set_software_connected(False)
            if responder is not None:
                responder.rebind_client(self.hw_ai)
            if main_page is not None and hasattr(main_page, "on_software_status"):
                main_page.on_software_status(False, "已断开（硬件通道）")
            if main_page is not None and hasattr(main_page, "on_hardware_status"):
                main_page.on_hardware_status(
                    bool(self.connected),
                    self.device_state.get_state_value() if self.connected else "",
                )
            self.log("🔀 已切到硬件喇叭：软件小智已断开，弹幕交给硬件小智")
            self._start_hardware_link()
        else:
            client = self._ai_client()
            if responder is not None and client is not None:
                responder.rebind_client(client)
            if main_page is not None and hasattr(main_page, "on_software_status"):
                main_page.on_software_status(False, "正在连接...")
            self.set_software_connected(True)
            self._stop_hardware_link()
            self.log("🔀 已切到软件语音：硬件已关闭，停止扫描设备/状态轮询")

    def _software_conn_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data",
            "software_connected.txt",
        )

    def set_software_connected(self, connected: bool) -> None:
        """随语音通道联动：软件语音时连接，硬件喇叭时断开。"""
        connected = bool(connected)
        try:
            path = self._software_conn_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("1" if connected else "0")
        except OSError as exc:
            self.log(f"⚠️ 保存软件连接开关失败: {exc}")

        main_page = self.pages.get("main") if hasattr(self, "pages") else None
        page = self.pages.get("ai_chat") if hasattr(self, "pages") else None
        client = getattr(page, "ai_client", None) if page is not None else None

        if not connected:
            if main_page is not None and hasattr(main_page, "on_software_status"):
                main_page.on_software_status(False, "已断开")
            if client is None or not getattr(client, "_running", False):
                return

            def stop_worker():
                try:
                    client.stop()
                    self.log("⏹ 已断开软件小智连接（省资源）")
                except Exception as exc:
                    self.log(f"⚠️ 断开软件小智连接失败: {exc}")
                finally:
                    client.status_changed.emit("已断开", False)

            # stop() 会 join 线程，放后台执行避免卡界面
            threading.Thread(target=stop_worker, daemon=True).start()
            return

        if client is None:
            return
        if getattr(client, "_connected", False):
            if main_page is not None and hasattr(main_page, "on_software_status"):
                main_page.on_software_status(True, "已连接")
            return
        if main_page is not None and hasattr(main_page, "on_software_status"):
            main_page.on_software_status(False, "正在连接...")
        if getattr(client, "_running", False):
            return

        def start_worker():
            try:
                client.start()
                self.log("▶️ 已启动软件小智连接")
            except Exception as exc:
                self.log(f"⚠️ 启动软件小智连接失败: {exc}")

        threading.Thread(target=start_worker, daemon=True).start()

    def _start_hardware_link(self) -> None:
        """硬件通道激活：恢复设备自动连接；同一时间只允许连接一台。"""
        self._auto_connect_enabled = True
        timer = getattr(self, "_auto_connect_timer", None)
        if timer is not None and not timer.isActive():
            timer.start()
        if (not getattr(self, "connected", False)
                and not getattr(self, "_connecting", False)):
            QTimer.singleShot(300, self._auto_connect_device)

    def _stop_hardware_link(self) -> None:
        """软件通道激活：硬件链路整体关闭——不扫描、不连接、不轮询。"""
        for name in ("_auto_connect_timer", "_status_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        self._auto_discover_busy = False
        self._connecting = False
        self._status_busy = False
        if self.udp is not None:
            try:
                self.udp.close()
            except Exception:
                pass
            self.udp = None
        self.connected = False
        self._connected_at = None
        self.device_state.reset()
        if hasattr(self, "barrage_manager") and self.barrage_manager is not None:
            self.barrage_manager.udp_client = None
        if hasattr(self, "hw_speaker"):
            self.hw_speaker.set_enabled(False)
        main = self.pages.get("main") if hasattr(self, "pages") else None
        if main is not None:
            if hasattr(main, "on_hardware_link_off"):
                main.on_hardware_link_off()
            elif hasattr(main, "on_hardware_status"):
                main.on_hardware_status(False, "已关闭（软件语音）")

    def _connect_signals(self):
        self.connection_success.connect(self._on_connected)
        self.connection_failure.connect(self._on_connect_failed)
        self.connection_lost.connect(self._on_connection_lost)
        self.status_received.connect(self._on_status)
        self.operation_result.connect(self._on_operation_result)
        self.barrage_event.connect(self._on_barrage_event)
        self.monitor_status.connect(self._on_monitor_status)
        self.log_signal.connect(self.log)
        self.scan_done.connect(self._on_scan_done)
        self.device_discovered.connect(self._on_device_discovered)
        self.barrage_refresh_request.connect(self._flush_barrage_refresh)

    # ---------- 公共入口 ----------
    def enter_workspace(self):
        main_page = getattr(self, "pages", {}).get("main")
        if main_page is not None and hasattr(main_page, "refresh"):
            main_page.refresh()
        self.go_page("main")
        self.central_stack.setCurrentWidget(self.workspace_root)
        self._refresh_side_license()

    def show_cover(self):
        if hasattr(self, "cover"):
            self.cover.refresh()
            self.central_stack.setCurrentWidget(self.cover)

    def go_page(self, key):
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.sidebar.select(key)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def set_global_device_status(self, connected):
        for page in getattr(self, "pages", {}).values():
            setter = getattr(page, "set_header_device_status", None)
            if callable(setter):
                setter(connected)

    def set_global_license(self, text, activated=False):
        for page in getattr(self, "pages", {}).values():
            setter = getattr(page, "set_header_license", None)
            if callable(setter):
                setter(text, activated)
        self._refresh_side_license()

    def _refresh_side_license(self):
        sidebar = getattr(self, "sidebar", None)
        if sidebar is None or not hasattr(sidebar, "set_license"):
            return
        try:
            status = self.license_status() or {}
        except Exception:
            status = {}
        if status.get("activated"):
            days = status.get("remaining_days", 0)
            sub = "长期有效" if days == 999999 else f"还剩 {days} 天"
            sidebar.set_license("已授权", sub, True)
        else:
            sidebar.set_license("未授权", "点此激活", False)

    def open_backend_tab(self, title):
        if title == "参数配置":
            self.go_page("ai_settings")
        else:
            open_device_activation_dialog(self)

    def toast(self, text):
        self.log(text)
        self._show_toast(text)

    def _show_toast(self, text, success=None):
        """主窗口底部悬浮提示，4.5 秒后自动消失。"""
        label = getattr(self, "_toast_label", None)
        if label is None:
            return
        color = "#1f8f4e" if success is True else ("#b23b3b" if success is False else "#2a2a2a")
        label.setStyleSheet(
            "background-color: rgba(20,20,20,235);"
            f"color: {color if success is not None else '#ffffff'};"
            "border: 1px solid rgba(255,255,255,60);"
            "border-radius: 8px; padding: 9px 16px; font-size: 14px;"
        )
        label.setText(str(text))
        label.setFixedWidth(min(720, max(340, self.width() - 140)))
        label.adjustSize()
        x = max(20, (self.width() - label.width()) // 2)
        y = max(20, self.height() - label.height() - 70)
        label.move(x, y)
        label.show()
        label.raise_()
        timer = getattr(self, "_toast_timer", None)
        if timer is not None:
            timer.start()

    def log(self, text):
        # 工作线程里直接调 log 时，切回 Qt 主线程再动控件。
        if QThread.currentThread() is not self.thread():
            self.log_signal.emit(text)
            return
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        # 同时落盘一份，GUI 之外的自动化排查也能看到完整链路日志。
        try:
            debug_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data",
                "ui_debug.log",
            )
            with open(debug_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass
        page = None
        if hasattr(self, "pages"):
            page = self.pages.get("logs") or self.pages.get("main")
        if page is not None and hasattr(page, "append_log"):
            page.append_log(line)

    def license_status(self):
        return self.license_manager.status(self.machine_code)

    def activate_license(self, code):
        result = self.license_manager.activate(code, self.machine_code)
        if result.get("valid"):
            self.log("✅ " + result.get("message", "授权成功"))
        else:
            self.log("❌ " + result.get("message", "授权失败"))
        return result

    # ---------- 连接 ----------
    def connect_device(self, host, port):
        if not self._hardware_channel_active():
            self.log("⚠️ 软件语音模式下硬件已关闭，请先切到「硬件喇叭」")
            return
        if self.connected or getattr(self, "_connecting", False):
            return
        self._connecting = True
        self.log(f"正在连接 {host}:{port}...")
        threading.Thread(
            target=self._connect_worker, args=(host, port), daemon=True
        ).start()

    def _auto_connect_device(self):
        """自动连接上次保存的设备；连不上就静默重试，不弹窗。"""
        if not self._hardware_channel_active():
            return
        if not getattr(self, "_auto_connect_enabled", True):
            return
        if self.connected or getattr(self, "_connecting", False):
            return
        try:
            settings = load_connection_settings()
        except Exception:
            return
        host = str(settings.get("host", "") or "").strip()
        if not host:
            return
        try:
            port = int(settings.get("port", 8888) or 8888)
        except (TypeError, ValueError):
            port = 8888
        if settings.get("locked"):
            self.log(f"🔒 使用已锁定的设备地址直连 {host}:{port}")
        else:
            self.log(f"↩️ 使用上次保存的设备地址直连 {host}:{port}")
        self._auto_connecting = True
        # 连接中标记由 connect_device 统一维护，避免这里先置位导致直接返回。
        self.connect_device(host, port)

    def _connect_worker(self, host, port):
        client = None
        try:
            client = UdpClient(host, int(port), timeout=1.5)
            response = client.ping()
            if not response_ok(response):
                client.close()
                self.connection_failure.emit(f"{host}:{port} 无响应")
                return
            state_value = str(response.get("state", "idle"))
            self.connection_success.emit({
                "host": host, "port": int(port), "response": response,
                "state": state_value, "client": client,
            })
            client = None
        except Exception as exc:
            if client is not None:
                client.close()
            self.connection_failure.emit(str(exc))

    def _on_connected(self, info):
        self._connecting = False
        if self.udp is not None:
            self.udp.close()
        self.udp = info.get("client")
        if self.udp is None:
            self.udp = UdpClient(info["host"], info["port"], timeout=1.5)
            if not response_ok(self.udp.ping()):
                self.udp.close()
                self.udp = None
                self.connection_failure.emit("连接对象创建失败")
                return
        self.barrage_manager.udp_client = self.udp
        self.connected = True
        self._auto_connecting = False
        if hasattr(self, "hw_speaker"):
            self.hw_speaker.set_target(info["host"], info["port"])
        self._connected_at = time.time()
        self._status_failures = 0
        state = str(info.get("state", "idle")).lower()
        try:
            self.device_state.set_state(DeviceState(state))
        except ValueError:
            self.device_state.set_state(DeviceState.IDLE)
        response = info.get("response", {})
        saved = load_connection_settings()
        save_connection_settings({
            "host": info["host"], "port": str(info["port"]),
            "mac": str(response.get("mac", "") or saved.get("mac", "")),
            # 第一次连上就把地址锁定；之后启动先试这个地址，失败才重扫。
            "locked": True,
            "last_good_at": int(time.time()),
            "last_good_host": info["host"],
            "last_good_port": str(info["port"]),
        })
        self._status_timer.start()
        self._send_timer.start()
        # 断开设备时 pause_sending() 会关掉自动发送；重连后必须恢复，
        # 否则界面显示已连接但模板弹幕一条都不再发。
        if self.barrage_manager is not None:
            self.barrage_manager.resume_sending()
        self.log(f"✅ 已连接 {info['host']}:{info['port']}")
        main = self.pages.get("main")
        if main and hasattr(main, "on_connected"):
            main.on_connected(info)
        # 换设备后按上次开关状态自动恢复话题定时发送；否则每切一台
        # 设备，界面上的“启用话题定时发送”都会掉成未勾选。
        QTimer.singleShot(
            0, lambda: self._maybe_auto_start_topic_timer(force=True)
        )

    def _on_connect_failed(self, message):
        self._connecting = False
        self.log(f"❌ 连接失败: {message}")
        main = self.pages.get("main")
        if main and hasattr(main, "on_disconnected"):
            main.on_disconnected()
        # 自动重连失败不弹窗，免得每 15 秒打断一次
        if not getattr(self, "_auto_connecting", False):
            QMessageBox.warning(self, "连接失败", message)
        self._auto_connecting = False
        now = time.time()
        if (self._hardware_channel_active()
                and not getattr(self, "_auto_discover_busy", False)
                and now - getattr(self, "_last_auto_discover_at", 0.0) >= 30.0):
            self._last_auto_discover_at = now
            QTimer.singleShot(250, self._auto_discover_device)

    def disconnect_device(self):
        self.stop_monitoring()
        self.stop_topic_timer()
        self.stop_pressure_timer()
        self._status_timer.stop()
        self._send_timer.stop()
        if self.barrage_manager:
            self.barrage_manager.pause_sending()
            self.barrage_manager.udp_client = None
        if self.udp:
            self.udp.close()
            self.udp = None
        self.connected = False
        self._connected_at = None
        self.device_state.reset()
        self.log("已断开设备连接")
        main = self.pages.get("main")
        if main and hasattr(main, "on_disconnected"):
            main.on_disconnected()

    def _poll_status(self):
        if not self.connected or self.udp is None or self._status_busy:
            return
        self._status_busy = True
        threading.Thread(target=self._status_worker, daemon=True).start()

    def _status_worker(self):
        try:
            response = self.udp.get_status() if self.udp else None
            # status 接口偶发丢包，但设备其实还在线；先 ping 复核一次，
            # 避免待命/朗读尾部被误判成离线并触发重连。
            if not isinstance(response, dict) and self.udp is not None:
                ping_response = self.udp.ping()
                if isinstance(ping_response, dict):
                    response = ping_response
            self.status_received.emit(response)
        finally:
            self._status_busy = False

    def _on_status(self, response):
        if not self.connected:
            return
        if not isinstance(response, dict):
            # 小智在朗读/思考时 UDP 控制线程可能暂时不处理 status 请求。
            # 这不是掉线，若按连续超时直接重连会打断当前朗读，并在直播间
            # 形成“每分钟掉线一次”的假象。
            speaker = getattr(self, "hw_speaker", None)
            last_send = float(getattr(speaker, "last_send_ok_at", 0.0) or 0.0)
            recent_send = last_send and (time.time() - last_send < 45.0)
            hw_busy = bool(getattr(getattr(self, "hw_ai", None), "is_busy", False))
            responder = getattr(self, "ai_responder", None)
            responder_busy = bool(getattr(responder, "_busy", False))
            if (
                recent_send
                or hw_busy
                or responder_busy
                or self.device_state.get_state() == DeviceState.SPEAKING
            ):
                self._status_failures = 0
                return
            self._status_failures += 1
            # status 和 ping 都连续失败才认为真掉线；待命时偶发丢包
            # 不再触发“每分钟重连一次”的假离线。
            if self._status_failures >= 8:
                self._status_failures = 0
                self.device_state.set_state(DeviceState.OFFLINE)
                self.connection_lost.emit("连续 8 次状态检测与 ping 均无响应")
            return
        self._status_failures = 0
        raw_state = str(response.get("state", "") or "").strip().lower()
        # 固件刚连上云端或 TTS 卡死后自重启时，会短暂上报
        # connecting/activating/booting；这些都不是业务错误，
        # 静默按 idle 处理，避免界面日志刷“未知设备状态”。
        if raw_state in {"connecting", "activating", "booting"}:
            device_state = DeviceState.IDLE
        else:
            try:
                device_state = DeviceState(raw_state)
            except ValueError:
                # 空 state / 固件新增状态不能静默丢弃，否则状态机会永远卡在
                # 旧状态，弹幕发送被 can_send_barrage() 一直拒绝。
                self.log(f"⚠️ 收到未知设备状态 {raw_state!r}，按 idle 处理")
                device_state = DeviceState.IDLE
        now = time.time()
        audio_rx_total = -1
        try:
            audio_rx_total = int(response.get("audio_rx_total") or -1)
        except (TypeError, ValueError):
            audio_rx_total = -1
        if audio_rx_total >= 0:
            if audio_rx_total != self._last_audio_rx_total:
                self._last_audio_rx_total = audio_rx_total
                self._last_audio_progress_at = now
        else:
            # 老固件没有计数器，退回按时间判断。
            self._last_audio_progress_at = now
        if device_state == DeviceState.SPEAKING:
            if not self._speaking_since:
                self._speaking_since = now
                # 刚进入朗读时重新计时，避免沿用上一轮的时间戳被判成卡死。
                self._last_audio_progress_at = now
        else:
            self._speaking_since = 0.0

        # 真正的卡死特征是“状态还在 speaking，但音频包长时间不再增长”。
        # 只按总时长判断会把正常的长回答误判成卡死，进而反复唤醒音频、
        # 让用户看到“每隔几分钟重启一次”。
        audio_stalled_for = (
            now - self._last_audio_progress_at
            if self._last_audio_progress_at else 0.0
        )
        stuck_speaking = (
            device_state == DeviceState.SPEAKING
            and self._speaking_since
            and audio_stalled_for >= 45.0
            and now - self._stuck_speaking_recover_at >= 60.0
        )
        if stuck_speaking:
            self._stuck_speaking_recover_at = now
            self._speaking_since = 0.0
            self.log(
                "⚠️ 设备 45 秒没有音频推进（疑似 TTS 卡死），已自动唤醒音频"
            )
            speaker = getattr(self, "hw_speaker", None)
            if speaker is not None and hasattr(speaker, "recover_audio"):
                try:
                    speaker.recover_audio()
                except Exception as exc:
                    self.log(f"⚠️ 自动唤醒音频失败: {exc}")
            device_state = DeviceState.IDLE
        self.device_state.set_state(device_state)
        hw_ai = getattr(self, "hw_ai", None)
        if hw_ai is not None and hasattr(hw_ai, "notify_device_state"):
            # 把设备原始状态也传下去。activating/booting 不能被归一化成
            # idle，否则硬件桥会误以为设备可发送，在重启期间继续灌文本。
            notify_state = (
                "idle"
                if stuck_speaking
                else (
                    raw_state
                    if raw_state in {
                        "idle", "listening", "speaking", "connecting",
                        "activating", "booting", "error",
                    }
                    else device_state.value
                )
            )
            hw_ai.notify_device_state(notify_state)
        main = self.pages.get("main")
        if main and hasattr(main, "on_device_status"):
            main.on_device_status(response)

    def _on_hw_speech_done(self, result):
        """朗读失败后立刻解除 speaking 锁，避免设备卡死拖住整条队列。"""
        if not isinstance(result, dict) or result.get("ok"):
            return
        # 确认超时但设备其实还在正常出声：不要复位状态，否则下一条会
        # 抢断当前朗读，用户看到的就是“突然重启”。
        if result.get("soft_timeout"):
            return
        if not self._hardware_channel_active():
            return
        reason = str(result.get("reason", "") or "")
        try:
            if self.device_state.get_state() == DeviceState.SPEAKING:
                now = time.time()
                self._speaking_since = 0.0
                self._stuck_speaking_recover_at = now
                self.device_state.set_state(DeviceState.IDLE)
                hw_ai = getattr(self, "hw_ai", None)
                if hw_ai is not None and hasattr(hw_ai, "notify_device_state"):
                    hw_ai.notify_device_state("idle")
                self.log(
                    f"⚠️ 播放失败后已快速复位设备状态：{reason[:40]}"
                )
        except Exception as exc:
            self.log(f"⚠️ 快速复位设备状态失败: {exc}")

    def _on_connection_lost(self, message):
        self.log(f"⚠️ {message}")
        if self.udp is not None:
            self.udp.close()
            self.udp = None
        self.connected = False
        self._status_timer.stop()
        self._send_timer.stop()
        self.device_state.set_state(DeviceState.OFFLINE)
        main = self.pages.get("main")
        if main and hasattr(main, "on_disconnected"):
            main.on_disconnected()
        # 掉线可能只是 DHCP 换了地址：别干等自动重连，立刻按 MAC 重扫。
        if (getattr(self, "_auto_connect_enabled", True)
                and self._hardware_channel_active()):
            self._last_auto_discover_at = 0.0
            QTimer.singleShot(800, self._auto_discover_device)

    def _update_clock(self):
        main = self.pages.get("main")
        if main and hasattr(main, "update_connection_time"):
            main.update_connection_time(self._connected_at)
        # UI 心跳：定时器被主线程卡住时记录实际延迟，便于上线后持续监控卡顿。
        now = time.monotonic()
        lag = now - self._last_heartbeat_at - 1.0
        self._last_heartbeat_at = now
        events = self._barrage_event_count
        self._barrage_event_count = 0
        if lag > 0.35 or now - self._last_stability_sample >= 60:
            self._last_stability_sample = now
            try:
                with data_path("stability.log").open(
                    "a", encoding="utf-8"
                ) as handle:
                    handle.write(
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                        f"ui_lag={lag * 1000:.0f}ms events_1s={events}\n"
                    )
            except OSError:
                pass

    # ---------- 手动操作 ----------
    def _ai_client(self):
        page = self.pages.get("ai_chat") if hasattr(self, "pages") else None
        return getattr(page, "ai_client", None)

    def _ai_ready(self) -> bool:
        client = self._ai_client()
        return bool(client is not None and getattr(client, "_connected", False))

    def _voice_busy(self) -> bool:
        """小智仍在思考/朗读时，定时话题和逼单必须让路。"""
        hw_ai = getattr(self, "hw_ai", None)
        if hw_ai is not None and bool(getattr(hw_ai, "is_busy", False)):
            return True
        responder = getattr(self, "ai_responder", None)
        if responder is not None:
            if bool(getattr(responder, "_busy", False)):
                return True
            if int(getattr(responder, "pending_count", 0) or 0) > 0:
                return True
        client = self._ai_client()
        if client is None:
            return False
        if bool(getattr(client, "is_busy", False)):
            return True
        bridge = getattr(client, "_audio_bridge", None)
        if bridge is not None and bool(getattr(bridge, "is_playing", False)):
            return True
        return False

    def _broadcast_gate(
        self, source: str = "播报", ignore_manual_queue: bool = False
    ) -> str:
        """所有非真人语音消息的统一安全闸门。

        优先级固定为：真人语音 > 实时弹幕/进场 > 定时话题 > 定时逼单。
        任何播报源只允许在整条语音链路空闲、且用户已说完话后发送；
        不允许 abort 自动对话来抢麦。
        """
        now = time.time()

        def _wait(reason: str) -> str:
            last_log = float(getattr(self, "_broadcast_wait_log_at", 0.0) or 0.0)
            if now - last_log > 10.0:
                self._broadcast_wait_log_at = now
                self.log(f"⏳ {source}排队等待：{reason}")
            return reason

        if not ignore_manual_queue and getattr(self, "_manual_send_queue", None):
            return _wait("手动发送还在排队")
        if self._send_busy:
            return _wait("上一条播报仍在发送")
        responder = getattr(self, "ai_responder", None)
        if responder is not None:
            if bool(getattr(responder, "_busy", False)):
                return _wait("实时弹幕正在播报")
            if int(getattr(responder, "pending_count", 0) or 0) > 0:
                return _wait("实时弹幕/进场消息还在排队")
        client = self._ai_client()
        if client is None or not bool(getattr(client, "_connected", False)):
            return _wait("小智服务未连接")
        if bool(getattr(client, "is_busy", False)):
            return _wait("小智正在回复或朗读")
        bridge = getattr(client, "_audio_bridge", None)
        if bridge is not None and bool(getattr(bridge, "is_playing", False)):
            return _wait("本地音频仍在播放")
        # 用户刚说话/刚被识别时，任何播报都不能插进去。
        last_voice = 0.0
        if responder is not None:
            last_voice = float(getattr(responder, "_last_voice_at", 0.0) or 0.0)
        stt_info = getattr(client, "_last_auto_stt", ("", 0.0))
        if isinstance(stt_info, tuple):
            last_voice = max(last_voice, float(stt_info[1] or 0.0))
        if last_voice and now - last_voice < 3.0:
            return _wait("用户刚说完话，留出语音识别时间")
        last_tts = float(getattr(client, "_last_tts_at", 0.0) or 0.0)
        if last_tts and now - last_tts < 1.0:
            return _wait("上一轮朗读刚结束")
        return ""

    def _queue_manual_send(self, text, msg_type, record, reason=""):
        """把手动发送放进统一队列；返回 True，界面即可提示已受理。"""
        limit = 20
        if len(self._manual_send_queue) >= limit:
            message = f"发送队列已满（{limit} 条），本条未受理：{text[:30]}"
            self.operation_result.emit("send_manual", False, message)
            return False
        self._manual_send_queue.append(
            {
                "text": str(text).strip(),
                "msg_type": str(msg_type),
                "record": bool(record),
                "queued_at": time.time(),
            }
        )
        position = len(self._manual_send_queue)
        detail = (
            f"已加入发送队列（{reason or '小智正忙'}），排队第 {position} 条："
            f"{str(text).strip()[:30]}"
        )
        self.log(f"📥 {detail}")
        self.operation_result.emit("send_queued", True, detail)
        if not self._manual_send_timer.isActive():
            self._manual_send_timer.start()
        return True

    def _start_ai_text_send(
        self, text, msg_type, record, notify=False, queue_item=None
    ):
        """真正启动一次软件小智文本发送；调用前必须先确认链路空闲。"""
        if self._send_busy:
            return False
        client = self._ai_client()
        if client is None or bool(getattr(client, "is_busy", False)):
            return False
        self._send_busy = True
        threading.Thread(
            target=self._send_ai_text_worker,
            args=(text, msg_type, record, bool(notify), queue_item),
            daemon=True,
        ).start()
        return True

    def _drain_manual_send_queue(self):
        """手动发送队列：按顺序等安全间隙，绝不与语音/弹幕抢麦。"""
        if not self._manual_send_queue:
            self._manual_send_timer.stop()
            return
        if self._shutting_down:
            return
        if self.output_channel == "hardware":
            hw_ai = getattr(self, "hw_ai", None)
            paused = hw_ai is not None and bool(
                getattr(hw_ai, "is_paused", False)
            )
            if paused or not self.connected:
                # 设备重启/掉线期间只静默等待，不再每秒弹一次“未发送”提示。
                # 队列会保留，等设备恢复后自动继续发送。
                now = time.time()
                if (
                    now
                    - getattr(self, "_manual_queue_wait_log_at", 0.0)
                    >= 15.0
                ):
                    self._manual_queue_wait_log_at = now
                    self.log(
                        "⏳ 小智正在重启或未连接，手动发送队列等待中，"
                        "恢复后自动继续"
                    )
                return
            item = self._manual_send_queue.pop(0)
            if not self.send_text_async(
                item["text"],
                item["msg_type"],
                item["record"],
                notify=True,
                from_queue=True,
            ):
                self._manual_send_queue.insert(0, item)
            return
        # 软件小智通道：始终等安全间隙，再按 FIFO 顺序送一条。
        reason = self._broadcast_gate("手动发送", ignore_manual_queue=True)
        if reason:
            return
        item = self._manual_send_queue.pop(0)
        if not self._start_ai_text_send(
            item["text"],
            item["msg_type"],
            item["record"],
            notify=True,
            queue_item=item,
        ):
            self._manual_send_queue.insert(0, item)
            return
        if not self._manual_send_queue:
            self._manual_send_timer.stop()

    def send_text_async(
        self,
        text,
        msg_type="手动消息",
        record=True,
        notify=False,
        from_queue=False,
    ):
        text = str(text).strip()
        if not text:
            if notify:
                self.operation_result.emit(
                    "send_manual", False, "发送失败：内容为空"
                )
            return False
        if msg_type == "手动消息":
            decision = self.manual_input_policy.allow()
            if not decision.allowed:
                self.log(f"⏳ {decision.reason}，本条未发送")
                if notify:
                    self.operation_result.emit(
                        "send_manual", False, f"未发送：{decision.reason}"
                    )
                return False
            if not decision.priority:
                responder = getattr(self, "ai_responder", None)
                responder_busy = bool(
                    getattr(responder, "_busy", False)
                    or getattr(responder, "pending_count", 0)
                )
                if responder_busy:
                    self.log(
                        "⏳ 键盘输入优先已关闭，自动弹幕正在处理，"
                        "请稍后再发"
                    )
                    if notify:
                        self.operation_result.emit(
                            "send_manual",
                            False,
                            "未发送：自动弹幕正在处理，请稍后再发",
                        )
                    return False
        # 统一 FIFO：只要前面还有手动消息，新点击一律排到队尾，
        # 避免后点的消息插到先点的前面。
        if (
            notify
            and not from_queue
            and getattr(self, "_manual_send_queue", None)
        ):
            return self._queue_manual_send(
                text, msg_type, record, "前面还有手动消息在排队"
            )
        if self.output_channel == "hardware":
            speaker = getattr(self, "hw_speaker", None)
            if speaker is None or not speaker.enabled or not speaker.host:
                if notify:
                    self.operation_result.emit(
                        "send_manual",
                        False,
                        f"{msg_type}未发送：硬件语音通道未开启或 IP 未配置",
                    )
                QMessageBox.warning(self, "提示", "硬件语音通道未开启或设备 IP 未配置")
                return False
            hw_ai = getattr(self, "hw_ai", None)
            if hw_ai is not None and bool(getattr(hw_ai, "is_paused", False)):
                if notify and not from_queue:
                    return self._queue_manual_send(
                        text, msg_type, record, "小智正在重启，等恢复后自动发送"
                    )
                self.log("⏳ 小智连接异常或正在重启，本条已跳过，等恢复后再发")
                if notify:
                    self.operation_result.emit(
                        "send_manual",
                        False,
                        f"{msg_type}未发送：小智正在重启，请稍后重试",
                    )
                return False
            now = time.time()
            if text == self._hw_last_text and now - self._hw_last_text_at < 2.0:
                # 连点“发送”会瞬间灌多条相同文本，触发设备连续 Abort。
                if notify and not from_queue:
                    return self._queue_manual_send(
                        text, msg_type, record, "2 秒内重复点击，稍后自动重发"
                    )
                self.log("⏳ 相同内容刚发过，已忽略重复点击")
                if notify:
                    self.operation_result.emit(
                        "send_manual",
                        False,
                        f"{msg_type}未发送：2 秒内重复点击，已忽略",
                    )
                return False
            # 所有手动/话题/逼单/弹幕统一进调度器，单 worker 串行朗读。
            def _done(result, text=text, msg_type=msg_type, record=record):
                success = bool(result.get("ok"))
                if success and record and self.barrage_manager:
                    self.barrage_manager.add_sent_text(text, "manual")
                detail = (
                    f"{msg_type}已朗读: {text}"
                    if success
                    else f"{msg_type}未出声: {result.get('reason', '') or '未知原因'}"
                )
                self.operation_result.emit("send", success, detail)

            if not hw_ai.submit_message(text, msg_type, on_done=_done):
                if notify and not from_queue:
                    return self._queue_manual_send(
                        text, msg_type, record, "设备朗读队列已满"
                    )
                self.log("⚠️ 语音队列已满或设备未就绪，本条未入队")
                if notify:
                    self.operation_result.emit(
                        "send_manual",
                        False,
                        f"{msg_type}未入队：设备朗读队列已满或未就绪",
                    )
                return False
            self._hw_last_text = text
            self._hw_last_text_at = now
            if notify:
                detail = f"{msg_type}已提交给小智，正在排队朗读: {text[:30]}"
                self.log(f"📤 {detail}")
                self.operation_result.emit("send_queued", True, detail)
            return True
        if not self.connected or self.udp is None:
            # 没接硬件时走软件小智：文字交给小智 TTS 直接说出来
            if not self._ai_ready():
                if notify:
                    self.operation_result.emit(
                        "send_manual",
                        False,
                        f"{msg_type}未发送：小智服务未连接",
                    )
                QMessageBox.warning(self, "提示", "请先连接设备或小智服务")
                return False
            if notify and not from_queue:
                # 手动发送一律先入统一队列：忙时不静默丢弃，空闲按顺序送。
                reason = (
                    self._broadcast_gate(msg_type, ignore_manual_queue=True)
                    or "等待语音空闲"
                )
                return self._queue_manual_send(
                    text, msg_type, record, reason=reason
                )
            if not self._start_ai_text_send(
                text, msg_type, record, notify=notify
            ):
                self.log("⏳ 小智正在说话，本条稍后重试")
                if notify:
                    self.operation_result.emit(
                        "send_manual",
                        False,
                        f"{msg_type}暂未送出：小智正在说话",
                    )
                return False
            return True
        if self._send_busy:
            self.log("⏳ 上一条消息仍在发送，请稍候")
            if notify:
                self.operation_result.emit(
                    "send_manual", False, f"{msg_type}暂未送出：上一条仍在发送"
                )
            return False
        self._send_busy = True
        threading.Thread(
            target=self._send_text_worker,
            args=(text, msg_type, record),
            daemon=True,
        ).start()
        return True

    def _send_ai_text_worker(
        self, text, msg_type, record, notify=False, queue_item=None
    ):
        success = False
        message = ""
        try:
            client = self._ai_client()
            success = bool(client is not None and client.send_text(text))
            message = (
                f"{msg_type}已交给小智朗读: {text}"
                if success else f"{msg_type}发送失败: 小智服务未响应"
            )
            if success and record and self.barrage_manager:
                self.barrage_manager.add_sent_text(text, "manual")
        except Exception as exc:
            message = f"{msg_type}发送失败: {exc}"
        finally:
            self._send_busy = False
            self.operation_result.emit("send", success, message)
            # 队列里的消息失败不丢：放回队首，最多重试 2 次。
            if queue_item is not None and not success:
                attempts = int(queue_item.get("attempts", 0) or 0) + 1
                if attempts <= 2:
                    queue_item["attempts"] = attempts
                    self._manual_send_queue.insert(0, queue_item)
                    self.log(
                        f"🔁 {msg_type}未送出，已放回队首重试"
                        f"（第 {attempts} 次）：{text[:30]}"
                    )
                    if not self._manual_send_timer.isActive():
                        self._manual_send_timer.start()
                else:
                    self.log(f"❌ {msg_type}连续 3 次未送出，已丢弃: {text[:30]}")

    def _send_hardware_text(self, text: str, direct: bool = False) -> bool:
        """弹幕管理器的手动优先发送入口：只入队，绝不阻塞等设备。"""
        hw_ai = getattr(self, "hw_ai", None)
        if hw_ai is None:
            return False
        if bool(getattr(hw_ai, "is_paused", False)):
            self.log("⏳ 设备正在重启，本条未发送")
            return False
        msg_type = "手动话题" if direct else "手动消息"
        return hw_ai.submit_message(text, msg_type)

    def _send_text_worker(self, text, msg_type, record):
        success = False
        message = ""
        try:
            with self._send_lock:
                if self.udp is None:
                    raise RuntimeError("设备未连接")
                if not self.device_state.can_send_barrage():
                    raise RuntimeError(f"设备当前状态 {self.device_state.get_state_value()} 不允许发送")
                if self.device_state.is_idle():
                    self.udp.send_audio_warmup(silent=True)
                    self.udp.send_text(" ")
                    self.udp.wake_audio()
                    time.sleep(1.5)
                response = self.udp.send_text(text)
                success = response_ok(response)
                message = f"{msg_type}发送成功: {text}" if success else f"{msg_type}发送失败: {text}"
                if success and record and self.barrage_manager:
                    self.barrage_manager.add_sent_text(text, "manual")
        except Exception as exc:
            message = f"{msg_type}发送失败: {exc}"
        finally:
            self._send_busy = False
            self.operation_result.emit("send", success, message)

    def ping_async(self):
        if not self.connected or self.udp is None:
            return
        threading.Thread(target=self._ping_worker, daemon=True).start()

    def _ping_worker(self):
        try:
            response = self.udp.ping() if self.udp else None
            ok = response_ok(response)
            message = f"Ping成功: {response}" if ok else "Ping失败: 无响应"
        except Exception as exc:
            ok = False
            message = f"Ping失败: {exc}"
        self.operation_result.emit("ping", ok, message)

    def query_status_async(self):
        if not self.connected or self.udp is None or self._status_busy:
            return
        self._status_busy = True
        threading.Thread(target=self._query_status_worker, daemon=True).start()

    def _query_status_worker(self):
        try:
            response = self.udp.get_status() if self.udp else None
            self.status_received.emit(response)
            message = f"设备状态: {response}" if response else "获取状态失败: 无响应"
            self.operation_result.emit("status", bool(response), message)
        finally:
            self._status_busy = False

    def _on_operation_result(self, kind, success, message):
        prefix = "✅" if success else "❌"
        self.log(f"{prefix} {message}")
        if kind in ("send", "send_queued", "send_manual"):
            # 状态栏直接可见的反馈：已入队 / 已送出 / 失败原因。
            try:
                self.statusBar().showMessage(message, 6000)
            except Exception:
                pass
            # 发送结果只写日志和状态栏，不再弹底部黑色提示框。
        if kind == "send" and success:
            main = self.pages.get("main")
            if main and hasattr(main, "clear_send_input"):
                main.clear_send_input()
        self.refresh_barrage_page()

    # ---------- 弹幕监控 ----------
    def start_monitoring(self):
        if self.monitoring:
            return True
        self.ws_monitor = WsMonitor(
            lambda kind, data: self.barrage_event.emit(kind, data)
        )
        self.ws_monitor.start()
        self.monitoring = True
        self.monitor_status.emit(True, "正在连接本地弹幕 WebSocket...")
        self.log("🚀 弹幕监控已启动")
        return True

    def stop_monitoring(self):
        if self.ws_monitor is not None:
            self.ws_monitor.stop()
            self.ws_monitor = None
        if self.monitoring:
            self.log("🛑 弹幕监控已停止")
        self.monitoring = False
        self.monitor_status.emit(False, "未开始监控")

    def _check_monitor_health(self):
        """只在监控线程真的退出时重建；静默不等于断线。"""
        if not self.monitoring:
            return
        monitor = self.ws_monitor
        if monitor is not None and monitor.is_alive:
            # 本地 WebSocket 自带 20 秒协议层 ping/pong 保活。
            # 安静直播间没有弹幕是正常状态，不能因此重启监控。
            return
        reason = "弹幕接收线程已退出"
        text = f"⚠️ {reason}，正在自动重启弹幕监控"
        self.log(text)
        self._append_collector_log(text)
        if monitor is not None:
            monitor.stop()
        self.ws_monitor = WsMonitor(
            lambda kind, data: self.barrage_event.emit(kind, data)
        )
        self.ws_monitor.start()
        self.monitor_status.emit(True, "弹幕监控自动恢复中...")

    def _get_category_settings(self):
        """分类设置按文件 mtime 缓存，避免每条弹幕都读一次 JSON。"""
        try:
            mtime = data_path("category_settings.json").stat().st_mtime
        except OSError:
            mtime = 0.0
        if (
            self._category_settings_cache is None
            or mtime != self._category_settings_mtime
        ):
            try:
                self._category_settings_cache = load_category_settings()
            except Exception:
                self._category_settings_cache = {}
            self._category_settings_mtime = mtime
        return self._category_settings_cache

    def _schedule_barrage_refresh(self):
        """刷新弹幕页做去抖，短时间大量事件只刷一次。"""
        if self._barrage_refresh_pending:
            return
        self._barrage_refresh_pending = True
        self.barrage_refresh_request.emit()

    def _flush_barrage_refresh(self):
        self._barrage_refresh_pending = False
        # 250ms 内的高频弹幕合并成一次重绘。
        if not self._barrage_refresh_timer.isActive():
            self._barrage_refresh_timer.start()

    def _on_barrage_event(self, kind, data):
        self._barrage_event_count += 1
        if kind in {"info", "error"}:
            text = str(data)
            if kind == "error":
                self.log("❌ " + text)
            else:
                self.log("ℹ️ " + text)
            self._append_collector_log(
                ("❌ " if kind == "error" else "ℹ️ ") + text
            )
            main = self.pages.get("main") if hasattr(self, "pages") else None
            if main and hasattr(main, "on_barrage_connection"):
                if kind == "info" and "连接成功" in text:
                    main.on_barrage_connection(True)
                    self.monitor_status.emit(True, "弹幕监控运行中，已连接")
                elif kind == "error":
                    main.on_barrage_connection(False)
                    if self.monitoring:
                        self.monitor_status.emit(True, "弹幕连接异常，自动重连中...")
            return
        if kind in SKIP_BARRAGE_TYPES or not isinstance(data, dict):
            return
        # 通讯日志：每次收到用户侧事件都落一行，便于确认数据到底有没有进来。
        if kind in {
            "member", "enter", "message", "like", "gift",
            "social", "follow", "share", "fansclub",
        }:
            try:
                line = (
                    f"📥 [{kind}] {data.get('username') or '未知用户'}: "
                    f"{str(data.get('content') or '')[:60]}"
                )
                self.log(line)
                self._append_collector_log(line)
            except Exception:
                pass
        settings = self._get_category_settings()
        if not settings.get("barrage_event_monitor", True):
            return
        if not is_barrage_type_enabled(settings, kind):
            return
        if self.barrage_manager is None:
            return
        self.barrage_manager.set_category_smart_enabled(
            bool(settings.get("category_monitor", True))
        )
        added = self.barrage_manager.add_barrage(data)
        responder = getattr(self, "ai_responder", None)
        # add_barrage 是唯一会推进重复/刷屏/数字/点赞去重窗口的入口。
        # 只有它放行时才交给朗读队列，避免过滤不生效和双重去重。
        if (
            responder is not None
            and added
            and self.barrage_manager.should_ai_read(data)
        ):
            responder.submit(kind, data)
        if added:
            self._schedule_barrage_refresh()

    def _append_collector_log(self, text):
        """同步到「弹幕助手」页的黑色运行日志。"""
        page = self.pages.get("collector") if hasattr(self, "pages") else None
        if page is not None and hasattr(page, "append_runtime"):
            page.append_runtime(text)

    # ---------- 自动发送 ----------
    def _schedule_barrage_send(self):
        # 硬件通道由小智 AI 回复队列统一朗读，旧模板发送不再并行，
        # 否则同一句弹幕会被小智读两遍。
        if self.output_channel == "hardware":
            return
        if (
            not self.connected or self.barrage_manager is None
            or self._send_busy or not self.device_state.can_send_barrage()
        ):
            return
        if self.barrage_manager.next_send_delay() not in (None, 0.0):
            delay = self.barrage_manager.next_send_delay()
            if delay and delay > 0:
                return
        self._send_busy = True
        threading.Thread(target=self._send_barrage_worker, daemon=True).start()

    def _send_barrage_worker(self):
        try:
            with self._send_lock:
                self.barrage_manager.try_send_next()
        except Exception as exc:
            self.log(f"❌ 自动发送异常: {exc}")
        finally:
            self._send_busy = False
            self._schedule_barrage_refresh()

    def force_send_barrage(self, item_id=None):
        if not self.connected or self.barrage_manager is None:
            QMessageBox.warning(self, "提示", "请先连接设备")
            return False
        if not self.device_state.can_send_barrage():
            QMessageBox.warning(self, "提示", "设备当前状态不允许发送")
            return False
        if self._send_busy:
            self.log("⏳ 上一条消息仍在发送，请稍候")
            return False
        self._send_busy = True
        threading.Thread(
            target=self._force_send_worker, args=(item_id,), daemon=True
        ).start()
        return True

    def _force_send_worker(self, item_id):
        success = False
        try:
            with self._send_lock:
                success = self.barrage_manager.try_send_next(
                    force=True, item_id=item_id
                )
        except Exception as exc:
            self.log(f"❌ 手动优先发送异常: {exc}")
        finally:
            self._send_busy = False
            self.operation_result.emit(
                "send",
                success,
                "手动优先发送成功" if success else "手动优先发送失败：没有可发送弹幕或设备未就绪",
            )

    def refresh_barrage_page(self):
        page = self.pages.get("barrage") if hasattr(self, "pages") else None
        if page and hasattr(page, "refresh"):
            page.refresh()
        main = self.pages.get("main") if hasattr(self, "pages") else None
        if main and hasattr(main, "update_counts"):
            main.update_counts()

    # ---------- 定时话题/逼单 ----------
    def start_topic_timer(self, interval, mode):
        if not self.topic_manager.runtime_topics:
            self._topic_enabled = False
            self._topic_timer.stop()
            if self.topic_manager.effective_topics:
                self.log(
                    "⚠️ 当前话题分类没有已确认素材，话题定时发送未启用"
                )
            else:
                self.log("⚠️ 素材库为空，话题定时发送未启用")
            return
        self.topic_manager.set_mode(mode)
        requested = max(1, int(interval))
        if self.output_channel == "hardware":
            self._topic_interval = _clamp_hardware_interval(requested)
            if self._topic_interval != requested:
                self.log(
                    f"⚠️ 硬件通道话题间隔最低 {int(MIN_HARDWARE_AUTO_INTERVAL)} 秒，"
                    f"已从 {requested} 秒调整为 {self._topic_interval} 秒"
                )
        else:
            self._topic_interval = requested
        self._topic_enabled = True
        self._topic_timer.start(self._topic_interval * 1000)
        client = self._ai_client()
        if client is not None and hasattr(client, "preheat_audio"):
            client.preheat_audio()
        self.log(f"🚀 话题定时发送已启用，间隔 {self._topic_interval} 秒")

    def stop_topic_timer(self):
        self._topic_enabled = False
        self._topic_timer.stop()
        self.log("🛑 话题定时发送已停止")

    def _on_topic_tick(self):
        if not self._topic_enabled:
            return
        # 硬件通道：设备未连上时只等待，不空发失败请求。
        if self.output_channel == "hardware":
            if not self.connected:
                self._topic_timer.start(3000)
                return
            hw_ai = getattr(self, "hw_ai", None)
            if (
                hw_ai is None
                or bool(getattr(hw_ai, "is_paused", False))
                or not self.device_state.can_send_barrage()
            ):
                self._topic_timer.start(3000)
                return
            # 统一队列里还有弹幕/手动消息，或设备正在读，就先让路。
            if hw_ai.is_busy or hw_ai.scheduler.has_pending("topic"):
                self._topic_timer.start(3000)
                return
            topic = self.topic_manager.get_next_topic()
            if topic and hw_ai.submit_message(topic, "定时话题"):
                self.log(f"📢 定时话题已入队: {topic}")
                self._topic_timer.start(self._topic_interval * 1000)
                return
            self._topic_timer.start(3000)
            return
        hw_ready = (
            self.connected and self.barrage_manager is not None
            and self.device_state.can_send_barrage()
        )
        ai_ready = self._ai_ready()
        if ai_ready:
            client = self._ai_client()
            if (
                client is not None
                and getattr(client, "_audio_bridge", None) is None
                and hasattr(client, "preheat_audio")
            ):
                try:
                    client.preheat_audio()
                except Exception:
                    pass
        can_send = (hw_ready or ai_ready) and not self._send_busy
        if hw_ready:
            # 硬件模式下有未发弹幕时先让弹幕优先
            can_send = can_send and (
                self.barrage_manager.get_counts().get("unsent", 0) == 0
            )
        if can_send and self.output_channel != "hardware":
            # 统一闸门：真人语音优先，所有播报串行排队，绝不打断在途回复。
            if self._broadcast_gate("定时话题"):
                can_send = False
        if can_send:
            topic = self.topic_manager.get_next_topic()
            if topic and self.send_text_async(topic, "定时话题", True):
                self.log(f"📢 发送定时话题: {topic}")
                self._topic_timer.start(self._topic_interval * 1000)
                return
        self._topic_timer.start(3000)

    def start_pressure_timer(self, interval, mode):
        self.pressure_manager.set_mode(mode)
        requested = max(1, int(interval))
        if self.output_channel == "hardware":
            requested = _clamp_hardware_interval(requested)
        self.pressure_manager.set_interval(requested)
        self._pressure_enabled = True
        self._pressure_timer.start(self.pressure_manager.interval * 1000)
        self.log(f"🚀 逼单定时发送已启用，间隔 {self.pressure_manager.interval} 秒")

    def stop_pressure_timer(self):
        self._pressure_enabled = False
        self._pressure_timer.stop()
        self.log("🛑 逼单定时发送已停止")

    def _on_pressure_tick(self):
        if not self._pressure_enabled:
            return
        # 硬件通道：设备未连上时只等待，不空发失败请求。
        if self.output_channel == "hardware":
            if not self.connected:
                self._pressure_timer.start(3000)
                return
            hw_ai = getattr(self, "hw_ai", None)
            if (
                hw_ai is None
                or bool(getattr(hw_ai, "is_paused", False))
                or not self.device_state.can_send_barrage()
            ):
                self._pressure_timer.start(3000)
                return
            if hw_ai.is_busy or hw_ai.scheduler.has_pending("pressure"):
                self._pressure_timer.start(3000)
                return
            message = self.pressure_manager.get_next()
            if message and hw_ai.submit_message(message, "定时逼单消息"):
                self.log(f"📢 定时逼单已入队: {message}")
                self._pressure_timer.start(self.pressure_manager.interval * 1000)
                return
            self._pressure_timer.start(3000)
            return
        # 软件语音通道不依赖硬件 connected；只看小智服务是否在线。
        can_send = self._ai_ready() and not self._send_busy
        if can_send and self.output_channel != "hardware":
            if self._broadcast_gate("定时逼单"):
                can_send = False
        if can_send:
            message = self.pressure_manager.get_next()
            if message and self.send_text_async(message, "定时逼单消息", True):
                self.log(f"📢 发送定时逼单消息: {message}")
                self._pressure_timer.start(self.pressure_manager.interval * 1000)
                return
        self._pressure_timer.start(5000)

    def _on_monitor_status(self, running, text):
        for key in ("collector", "main"):
            page = self.pages.get(key) if hasattr(self, "pages") else None
            if page and hasattr(page, "on_monitor_status"):
                page.on_monitor_status(running, text)

    def _on_ai_service_status(self, text, connected):
        page = self.pages.get("main") if hasattr(self, "pages") else None
        if page and hasattr(page, "on_software_status"):
            if getattr(self, "output_channel", "software") == "hardware":
                # 硬件通道下，后台连的是小智语音引擎，界面“软件”仍显示关闭。
                page.on_software_status(False, "已断开（硬件通道）")
            else:
                page.on_software_status(connected, text)
        topic_page = self.pages.get("messages") if hasattr(self, "pages") else None
        if topic_page is not None and hasattr(topic_page, "_update_status"):
            topic_page._update_status()
        if connected:
            self._maybe_auto_start_topic_timer(force=True)
            self._maybe_auto_start_pressure_timer()

    def _maybe_auto_start_topic_timer(self, force=False):
        """小智连上后自动恢复话题定时发送。

        force=True 时忽略上次保存的开关，直接勾选并启用，保证换设备后
        “启用话题定时发送”始终是默认打勾状态。
        """
        if getattr(self, "_topic_enabled", False):
            return
        try:
            data = load_topics_data()
        except Exception:
            return
        if not force and not data.get("auto_start", True):
            return

        def _sync_topic_enable_ui(checked):
            pages = getattr(self, "pages", {})
            for key, check_attr, update_attr in (
                ("main", "topic_enable_check", "_update_topic_control_status"),
                ("messages", "enable_check", "_update_status"),
            ):
                page = pages.get(key)
                if page is None:
                    continue
                check = getattr(page, check_attr, None)
                if check is not None:
                    check.blockSignals(True)
                    check.setChecked(bool(checked))
                    check.blockSignals(False)
                update = getattr(page, update_attr, None)
                if callable(update):
                    update()

        if not self.topic_manager.runtime_topics:
            _sync_topic_enable_ui(False)
            if self.topic_manager.effective_topics:
                self.log(
                    "⚠️ 当前话题分类没有已确认素材，未自动启用话题定时发送"
                )
            else:
                self.log("⚠️ 素材库为空，未自动启用话题定时发送")
            return
        self.start_topic_timer(
            int(data.get("interval", 30) or 30),
            str(data.get("send_mode", "sequential")),
        )
        _sync_topic_enable_ui(True)
        if force and not data.get("auto_start", True):
            data["auto_start"] = True
            try:
                save_topics_data(data)
            except Exception:
                pass

    def _maybe_auto_start_pressure_timer(self):
        """小智连上后，若上次开着逼单定时发送，就自动恢复。"""
        if getattr(self, "_pressure_enabled", False):
            return
        try:
            data = load_pressure_menu_data()
        except Exception:
            return
        if not data.get("auto_start"):
            return
        self.start_pressure_timer(
            int(data.get("interval", 60) or 60),
            str(data.get("send_mode", "sequential")),
        )
        topic_page = self.pages.get("messages") if hasattr(self, "pages") else None
        if topic_page is not None and hasattr(
            topic_page, "_update_pressure_control_status"
        ):
            topic_page._update_pressure_control_status()

    # ---------- 设备扫描 ----------
    def _local_subnet(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ipaddress.ip_network(ip + "/24", strict=False)
        except OSError:
            return None

    def scan_devices_async(self):
        if not self._hardware_channel_active():
            self.log("⚠️ 软件语音模式下硬件已关闭，不扫描设备")
            page = self.pages.get("main")
            if page is not None and hasattr(page, "on_scan_blocked"):
                page.on_scan_blocked()
            return
        network = self._local_subnet()
        if network is None:
            QMessageBox.warning(self, "提示", "无法确定本机局域网地址")
            return
        self.log(f"🔍 扫描局域网 {network} ...")
        threading.Thread(target=self._scan_worker, args=(network,), daemon=True).start()

    def _scan_lan(self, network, timeout=0.35):
        found = []

        def probe(host):
            try:
                client = UdpClient(str(host), 8888, timeout=timeout)
                try:
                    response = client.ping()
                    if response_ok(response):
                        return {
                            "host": str(host), "port": 8888,
                            "mac": response.get("mac", ""),
                            "state": response.get("state", ""),
                        }
                finally:
                    client.close()
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=64) as executor:
            futures = [executor.submit(probe, host) for host in network.hosts()]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    found.append(result)
        return found

    def _scan_worker(self, network):
        self.scan_done.emit(self._scan_lan(network))

    def _auto_discover_device(self):
        if not self._hardware_channel_active():
            return
        if getattr(self, "connected", False):
            return
        if getattr(self, "_auto_discover_busy", False):
            return
        network = self._local_subnet()
        if network is None:
            self.log("⚠️ 自动发现失败：无法确定本机局域网地址")
            return
        self._auto_discover_busy = True
        threading.Thread(
            target=self._auto_discover_worker,
            args=(network,),
            daemon=True,
        ).start()

    def _auto_discover_worker(self, network):
        try:
            saved = load_connection_settings()
            saved_mac = str(saved.get("mac", "")).strip().lower()
            self.log(f"📡 自动扫描当前网段 {network} 查找小智...")
            found = self._scan_lan(network)
            if not found:
                self.log("⚠️ 自动扫描未发现小智设备")
                return
            match = None
            if saved_mac:
                match = next(
                    (
                        item for item in found
                        if str(item.get("mac", "")).strip().lower() == saved_mac
                    ),
                    None,
                )
            if match is None and len(found) == 1:
                match = found[0]
            if match is None:
                hosts = "，".join(str(item.get("host", "")) for item in found[:5])
                self.log(
                    f"⚠️ 发现 {len(found)} 台设备（{hosts}），"
                    "但未匹配已保存的 MAC"
                )
                return
            self.device_discovered.emit(match)
        except Exception as exc:
            self.log(f"⚠️ 自动发现设备失败: {exc}")
        finally:
            self._auto_discover_busy = False

    def _on_device_discovered(self, device):
        if not self._hardware_channel_active():
            return
        host = str(device.get("host", "")).strip()
        if not host:
            return
        port = int(device.get("port", 8888) or 8888)
        mac = str(device.get("mac", "")).strip()
        save_connection_settings({
            "host": host, "port": str(port), "mac": mac,
            "locked": True, "last_good_at": int(time.time()),
            "last_good_host": host, "last_good_port": str(port),
        })
        data = load_devices()
        devices = list(data.get("devices", []))
        matched = False
        for item in devices:
            if (
                (mac and str(item.get("mac", "")).strip().lower() == mac.lower())
                or str(item.get("host", "")).strip() == host
            ):
                item["host"] = host
                item["port"] = port
                if mac:
                    item["mac"] = mac
                matched = True
                break
        if not matched:
            devices.append({
                "name": f"小智 {len(devices) + 1}",
                "host": host,
                "port": port,
                "mac": mac,
                "description": "自动发现",
            })
        save_devices({"devices": devices, "current_index": 0})
        if hasattr(self, "hw_speaker"):
            self.hw_speaker.set_target(host, port)
        self.log(f"📡 自动发现小智，当前 IP 已更新为 {host}:{port}")
        self._auto_connecting = True
        self.connect_device(host, port)

    def _on_scan_done(self, found):
        self.log(f"扫描完成，发现 {len(found)} 台设备")
        page = self.pages.get("main")
        if page and hasattr(page, "on_scan_done"):
            page.on_scan_done(found)

    # ---------- 关闭 ----------
    def closeEvent(self, event):
        if self._shutting_down:
            event.accept()
            return
        counts = self.barrage_manager.get_counts() if self.barrage_manager else {}
        if counts.get("unsent", 0) > 0:
            box = QMessageBox(self)
            box.setWindowTitle("还有未发送弹幕")
            box.setText(f"还有 {counts['unsent']} 条弹幕未发送，确定退出吗？")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)
            choice = box.exec_()
            if choice == QMessageBox.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.No:
                event.ignore()
                return
        self._shutdown_services()
        event.accept()

    def _shutdown_services(self):
        """停止所有定时器、连接和后台服务，保证进程可立即完全退出。"""
        if self._shutting_down:
            return
        self._shutting_down = True

        # 先停所有 Qt 定时器，避免清理过程中又被回调重新拉起。
        for name in (
            "_auto_connect_timer",
            "_status_timer",
            "_barrage_refresh_timer",
            "_send_timer",
            "_clock_timer",
            "_monitor_watchdog_timer",
            "_topic_timer",
            "_pressure_timer",
        ):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

        self._auto_connect_enabled = False
        self._connecting = False
        self._status_busy = False

        try:
            self.stop_monitoring()
        except Exception as exc:
            self.log(f"⚠️ 停止弹幕监控失败: {exc}")

        udp = getattr(self, "udp", None)
        if udp is not None:
            try:
                udp.close()
            except Exception:
                pass
            self.udp = None

        try:
            self.collector_service.stop()
        except Exception as exc:
            self.log(f"⚠️ 停止采集器失败: {exc}")

        responder = getattr(self, "ai_responder", None)
        if responder is not None:
            try:
                responder.shutdown()
            except Exception as exc:
                self.log(f"⚠️ 停止自动回复失败: {exc}")

        ai_chat = self.pages.get("ai_chat") if hasattr(self, "pages") else None
        if ai_chat is not None and hasattr(ai_chat, "shutdown"):
            try:
                ai_chat.shutdown()
            except Exception as exc:
                self.log(f"⚠️ 停止软件小智失败: {exc}")

        hw_ai = getattr(self, "hw_ai", None)
        if hw_ai is not None:
            try:
                hw_ai.stop()
            except Exception as exc:
                self.log(f"⚠️ 停止硬件小智失败: {exc}")

        hw_speaker = getattr(self, "hw_speaker", None)
        if hw_speaker is not None:
            try:
                hw_speaker.stop()
            except Exception as exc:
                self.log(f"⚠️ 停止硬件发送失败: {exc}")

        if self.barrage_manager is not None:
            try:
                self.barrage_manager.flush()
                self.barrage_manager._flush_stop.set()
            except Exception as exc:
                self.log(f"⚠️ 保存弹幕缓存失败: {exc}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
            return
        super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyleSheet(APP_QSS)

    # 整个程序只允许一个实例。第二个实例直接退出，避免两个进程同时
    # 抢占同一个云端会话，导致服务端来回踢线、界面反复“断开/重连”。
    lock = QLockFile(str(data_path("app.lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(
            None,
            "程序已在运行",
            "小智AI弹幕自动回复系统已经在运行，请勿重复启动。",
        )
        return 1

    try:
        window = MainWindow()
        window._app_lock = lock
        app.aboutToQuit.connect(window._shutdown_services)
        window.showMaximized()
        exit_code = app.exec_()
    finally:
        lock.unlock()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

