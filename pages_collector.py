import threading
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from data_access import load_json, save_json
from widgets import (
    PageBase,
    make_btn,
    make_field,
    make_group,
    make_scroll,
    make_text_edit,
)

COLLECTOR_STATUS_LOG = (
    Path(__file__).resolve().parent / "data" / "collector_status_debug.log"
)


class CollectorPage(PageBase):
    collector_result = pyqtSignal(bool, str)
    collector_log = pyqtSignal(str)

    def __init__(self, window):
        super().__init__("采集控制", None, window)
        self.add_action(make_btn("警告/Warning", on_click=self._show_warning))
        self._busy = False

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(12)

        websocket_group = make_group("本地服务")
        websocket_layout = QVBoxLayout(websocket_group)
        websocket_layout.setContentsMargins(12, 14, 12, 12)
        websocket_layout.setSpacing(9)

        location_row = QHBoxLayout()
        location_row.setSpacing(8)
        location_row.addWidget(QLabel("监听地址："))
        location = QLabel("ws://0.0.0.0:8888")
        location.setStyleSheet("QLabel { color:#DC2626; }")
        location_row.addWidget(location)
        location_row.addSpacing(24)
        thanks = QLabel("* 感谢点小心心的你，好人一生平安~")
        thanks.setStyleSheet("QLabel { color:#C026D3; }")
        location_row.addWidget(thanks)
        location_row.addStretch(1)
        websocket_layout.addLayout(location_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(QLabel("服务状态："))
        self.service_status = QLabel("未启动")
        self.service_status.setStyleSheet("QLabel { color:#64748B; }")
        status_row.addWidget(self.service_status)
        status_row.addSpacing(12)
        self.restart_btn = make_btn("重启", on_click=self._restart)
        status_row.addWidget(self.restart_btn)
        status_row.addStretch(1)
        websocket_layout.addLayout(status_row)

        monitor_group = make_group("弹幕监听")
        monitor_layout = QVBoxLayout(monitor_group)
        monitor_layout.setContentsMargins(12, 14, 12, 12)
        monitor_layout.setSpacing(9)
        monitor_row = QHBoxLayout()
        monitor_row.setSpacing(8)
        self.start_monitor_btn = make_btn(
            "开始监控", on_click=self._start_monitor
        )
        self.stop_monitor_btn = make_btn(
            "停止监控", on_click=self._stop_monitor
        )
        self.stop_monitor_btn.setEnabled(False)
        monitor_row.addWidget(self.start_monitor_btn)
        monitor_row.addWidget(self.stop_monitor_btn)
        monitor_row.addStretch(1)
        monitor_layout.addLayout(monitor_row)
        self.monitor_label = QLabel("未开始监控")
        self.monitor_label.setStyleSheet("QLabel { color:#64748B; }")
        monitor_layout.addWidget(self.monitor_label)
        monitor_layout.addStretch(1)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(websocket_group, 1)
        top_row.addWidget(monitor_group, 1)
        body.addLayout(top_row)

        grab_group = make_group("直播采集")
        grab_layout = QVBoxLayout(grab_group)
        grab_layout.setContentsMargins(12, 14, 12, 12)
        grab_layout.setSpacing(10)

        platform_row = QHBoxLayout()
        platform_row.setSpacing(10)
        platform_row.addWidget(QLabel("抓取平台："))
        douyin = QRadioButton("抖音")
        douyin.setChecked(True)
        platform_row.addWidget(douyin)
        platform_row.addSpacing(18)
        platform_row.addWidget(QLabel("直播间："))
        self.live_id = make_field()
        self.live_id.setPlaceholderText("粘贴抖音直播间链接，或填写 LiveId")
        settings = load_json("collector_settings.json")
        saved_live = str(
            settings.get("last_web_rid")
            or settings.get("last_live_input")
            or ""
        )
        self.live_id.setText(saved_live)
        self.live_id.editingFinished.connect(
            lambda: self._remember_live_input(self.live_id.text())
        )
        platform_row.addWidget(self.live_id, 1)
        self.grab_btn = make_btn("开始", on_click=self._toggle_grab)
        platform_row.addWidget(self.grab_btn)
        grab_layout.addLayout(platform_row)

        self.console = make_text_edit("")
        self.console.setMinimumHeight(260)
        self.console.setStyleSheet(
            "QTextEdit { background:#000000; color:#FFFFFF; border:1px solid #111827;"
            " font-family: Consolas, Microsoft YaHei UI; }"
        )
        self.console.document().setMaximumBlockCount(2000)
        grab_layout.addWidget(self.console, 1)
        body.addWidget(grab_group, 1)
        self.root.addWidget(make_scroll(content), 1)

        self.collector_result.connect(self._on_result)
        self.collector_log.connect(self._append)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(2000)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start()
        self._last_service_status_text = ""
        self._update_status()
        self.on_monitor_status(
            bool(getattr(window, "monitoring", False)),
            "未开始监控" if not getattr(window, "monitoring", False)
            else "弹幕监控运行中",
        )

    def _service(self):
        return getattr(self.win, "collector_service", None)

    def _append(self, text):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        self.console.append(line)
        self.console.verticalScrollBar().setValue(
            self.console.verticalScrollBar().maximum()
        )

    def append_runtime(self, text):
        """接收主线程/采集线程的运行日志，统一投递到 Qt 主线程。"""
        self.collector_log.emit(str(text))

    def on_monitor_status(self, running, text):
        self.monitor_label.setText(str(text or "未开始监控"))
        self.start_monitor_btn.setEnabled(not running)
        self.stop_monitor_btn.setEnabled(running)

    def _start_monitor(self):
        self.win.start_monitoring()

    def _stop_monitor(self):
        self.win.stop_monitoring()

    def _update_status(self):
        service = self._service()
        if service is None:
            return
        status = service.status()
        running = bool(status.get("process_running"))
        websocket = bool(status.get("websocket_ready"))
        douyin_connected = bool(status.get("douyin_connected"))
        event_age = status.get("last_event_age")
        if websocket:
            if douyin_connected and (event_age is None or event_age <= 60):
                self.service_status.setText("监听中 (抖音已连接，数据正常)")
                self.service_status.setStyleSheet("QLabel { color:#15803D; }")
            elif douyin_connected:
                self.service_status.setText(
                    f"监听中 (抖音已连接，等待新弹幕 {int(event_age)} 秒)"
                )
                self.service_status.setStyleSheet("QLabel { color:#15803D; }")
            else:
                reason = str(status.get("last_error") or "").strip()
                if reason:
                    self.service_status.setText(
                        f"监听中 (抖音连接中... {reason[:36]})"
                    )
                else:
                    self.service_status.setText("监听中 (本地服务就绪，抖音连接中...)")
                self.service_status.setStyleSheet("QLabel { color:#D97706; }")
        elif running:
            self.service_status.setText("采集器运行中，WebSocket 未就绪")
            self.service_status.setStyleSheet("QLabel { color:#D97706; }")
        else:
            self.service_status.setText("未启动")
            self.service_status.setStyleSheet("QLabel { color:#64748B; }")
        main = getattr(self.win, "pages", {}).get("main") if self.win else None
        if main and hasattr(main, "on_collector_status"):
            main.on_collector_status(running, websocket, self.service_status.text())
        self.grab_btn.setText("停止" if running else "开始")
        self.live_id.setEnabled(not running)
        self.restart_btn.setEnabled(running)
        status_text = self.service_status.text()
        if status_text != self._last_service_status_text:
            self._last_service_status_text = status_text
            try:
                COLLECTOR_STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
                with COLLECTOR_STATUS_LOG.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} {status_text}\n"
                    )
            except Exception:
                pass

    def _toggle_grab(self):
        service = self._service()
        if service is None or self._busy:
            return
        status = service.status()
        self._busy = True
        self.grab_btn.setEnabled(False)
        if status.get("process_running"):
            self._append("正在停止采集器...")
            threading.Thread(target=self._stop_worker, daemon=True).start()
        else:
            live_id = self.live_id.text().strip()
            if not live_id:
                QMessageBox.information(self, "提示", "LiveId不能为空!")
                self._busy = False
                self.grab_btn.setEnabled(True)
                return
            self._append(f"正在启动采集器，LiveId={live_id} ...")
            threading.Thread(
                target=self._start_worker, args=(live_id,), daemon=True
            ).start()

    def _start_worker(self, live_id):
        service = self._service()
        try:
            self._remember_live_input(live_id)
            ok, message = service.start(live_id)
            if ok:
                status = service.status()
                web_rid = str(status.get("live_room") or "")
                if web_rid:
                    settings = load_json("collector_settings.json")
                    settings["last_web_rid"] = web_rid
                    save_json("collector_settings.json", settings)
        except Exception as exc:
            ok, message = False, f"启动采集器异常: {exc}"
        self.collector_result.emit(ok, message)

    def _remember_live_input(self, live_id):
        settings = load_json("collector_settings.json")
        settings["last_live_input"] = str(live_id or "").strip()
        save_json("collector_settings.json", settings)

    def _stop_worker(self):
        service = self._service()
        try:
            ok, message = service.stop()
        except Exception as exc:
            ok, message = False, f"停止采集器异常: {exc}"
        self.collector_result.emit(ok, message)

    def _on_result(self, ok, message):
        self._busy = False
        self.grab_btn.setEnabled(True)
        self._append(("✅ " if ok else "❌ ") + message)
        if not ok:
            QMessageBox.warning(self, "采集器", message)
        self._update_status()

    def _restart(self):
        service = self._service()
        if service is None or self._busy:
            return
        self._busy = True
        self.grab_btn.setEnabled(False)
        self._append("正在重启采集器...")
        threading.Thread(target=self._restart_worker, daemon=True).start()

    def _restart_worker(self):
        service = self._service()
        try:
            live_id = self.live_id.text().strip()
            self._remember_live_input(live_id)
            ok, message = service.restart(live_id)
            if ok:
                status = service.status()
                web_rid = str(status.get("live_room") or "")
                if web_rid:
                    settings = load_json("collector_settings.json")
                    settings["last_web_rid"] = web_rid
                    save_json("collector_settings.json", settings)
        except Exception as exc:
            ok, message = False, f"重启采集器异常: {exc}"
        self.collector_result.emit(ok, message)

    def refresh(self):
        self._update_status()

    def _show_warning(self):
        QMessageBox.warning(
            self,
            "警告/Warning",
            "本程序只用作学习交流，请勿用作非法用途。如有违背，责任自行承担。\r\n"
            "This program is only for learning and communication purposes, please do not use it "
            "for illegal purposes. If there is any violation, the responsibility shall be borne by oneself.",
        )
