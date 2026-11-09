from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def make_btn(text, primary=False, small=False, ghost=False, on_click=None):
    button = QPushButton(text)
    if primary:
        button.setObjectName("primary")
    elif ghost:
        button.setObjectName("ghost")
    button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    button.setCursor(Qt.PointingHandCursor)
    if on_click:
        button.clicked.connect(on_click)
    return button


def label(text, name=None, wrap=False, alignment=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    if alignment is not None:
        widget.setAlignment(alignment)
    return widget


def badge(text, kind="green"):
    colors = {
        "green": ("#0D7C57", "#E7F7F1"),
        "blue": ("#2563D9", "#EAF1FF"),
        "purple": ("#7052D0", "#F1EDFF"),
        "orange": ("#B16A11", "#FFF4E2"),
        "teal": ("#0C7F87", "#E6F8F8"),
        "gray": ("#64748B", "#EEF2F6"),
        "red": ("#B42318", "#FEE4E2"),
    }
    fg, bg = colors.get(kind, colors["gray"])
    widget = QLabel(text)
    widget.setStyleSheet(
        f"QLabel {{ color:{fg}; background:{bg}; border-radius:5px;"
        " padding:3px 8px; font-size:9pt; font-weight:600; }}"
    )
    widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return widget


def make_field(text="", width=None):
    widget = QLineEdit(text)
    if width:
        widget.setFixedWidth(width)
    return widget


def make_combo(items, current=None, width=None):
    widget = QComboBox()
    for item in items:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            label, value = item[0], item[1]
        else:
            label = value = item
        widget.addItem(str(label), value)
    if current is not None:
        index = widget.findData(current)
        if index < 0:
            index = widget.findText(str(current))
        if index >= 0:
            widget.setCurrentIndex(index)
    if width:
        widget.setFixedWidth(width)
    return widget


def make_check(text, checked=False):
    widget = QCheckBox(text)
    widget.setChecked(checked)
    return widget


def make_radio(text, checked=False):
    widget = QRadioButton(text)
    widget.setChecked(checked)
    return widget


def make_table(headers, rows, widths=None, stretch_col=None):
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setShowGrid(False)
    table.horizontalHeader().setStretchLastSection(stretch_col is None)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
    if widths:
        for index, width in enumerate(widths):
            table.setColumnWidth(index, width)
    if stretch_col is not None:
        table.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_index, col_index, item)
        table.setRowHeight(row_index, 30)
    table.verticalHeader().setDefaultSectionSize(30)
    return table


def make_list(items):
    widget = QListWidget()
    widget.addItems(items)
    return widget


def row(*widgets, spacing=8, stretch=None):
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, str):
            layout.addWidget(label(widget))
        elif isinstance(widget, int):
            layout.addSpacing(widget)
        else:
            layout.addWidget(widget)
    if stretch is not None:
        layout.setStretch(stretch, 1)
    return layout


def vbox(*widgets, spacing=8, margins=(0, 0, 0, 0)):
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, str):
            layout.addWidget(label(widget))
        else:
            layout.addWidget(widget)
    return layout


def hline():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("QFrame { color:#EEF1F5; background:#EEF1F5; max-height:1px; }")
    return line


def make_group(title):
    group = QGroupBox(title)
    group.setObjectName("sourceGroup")
    return group


def make_text_edit(text="", height=None, readonly=True):
    widget = QTextEdit()
    widget.setPlainText(text)
    widget.setReadOnly(readonly)
    if height:
        widget.setFixedHeight(height)
    return widget


def make_scroll(content):
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    holder = QWidget()
    holder.setObjectName("scrollHolder")
    holder_layout = QHBoxLayout(holder)
    holder_layout.setContentsMargins(0, 0, 0, 0)
    holder_layout.setSpacing(0)
    holder_layout.addStretch(1)
    content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    content.setMaximumWidth(1440)
    holder_layout.addWidget(content, 100)
    holder_layout.addStretch(1)
    scroll.setWidget(holder)
    return scroll


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(16, 12, 16, 14)
        self.outer.setSpacing(10)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        if title:
            header = QHBoxLayout()
            header.setSpacing(8)
            header.addWidget(label(title, "cardTitle"))
            if subtitle:
                header.addWidget(label(subtitle, "cardSub"))
            header.addStretch(1)
            header.addLayout(self.header_actions)
            self.outer.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.outer.addLayout(self.body, 1)

    def add_action(self, widget):
        self.header_actions.addWidget(widget)
        return widget

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def addWidget(self, widget, stretch=0, alignment=None):
        if alignment is None:
            self.body.addWidget(widget, stretch)
        else:
            self.body.addWidget(widget, stretch, alignment)
        return widget

    def add_layout(self, layout, stretch=0):
        self.body.addLayout(layout, stretch)
        return layout

    def addStretch(self, stretch=1):
        self.body.addStretch(stretch)

    def addSpacing(self, spacing):
        self.body.addSpacing(spacing)


class PageBase(QWidget):
    def __init__(self, title, subtitle=None, window=None, show_header=True):
        super().__init__()
        self.win = window
        self.setObjectName("page")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.center_actions = QHBoxLayout()
        self.center_actions.setSpacing(14)
        self.header_status = QLabel("○ 设备未连接    |    自动检测: 未启动")
        self.header_status.setAlignment(Qt.AlignCenter)
        self.header_status.setStyleSheet(
            "QLabel { color:#64748B; font-size:10pt; font-weight:600; }"
        )
        self.center_actions.addWidget(self.header_status)
        self.header_license = QLabel("授权到期: 获取中...")
        self.header_license.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.header_license.setStyleSheet(
            "QLabel { color:#64748B; font-size:9.5pt; font-weight:600; }"
        )
        if not show_header:
            self._outer.setSpacing(8)
            self.root = self._outer
            return
        header_widget = QWidget()
        header_widget.setObjectName("pageHeader")
        header = QGridLayout(header_widget)
        header.setContentsMargins(24, 12, 24, 12)
        header.setHorizontalSpacing(10)
        header.setColumnStretch(0, 1)
        header.setColumnStretch(1, 2)
        header.setColumnStretch(2, 1)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(label(title, "h1"))
        if subtitle:
            title_box.addWidget(label(subtitle, "pageSub"))
        header.addLayout(title_box, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        header.addLayout(self.center_actions, 0, 1, Qt.AlignCenter)
        right_box = QHBoxLayout()
        right_box.setSpacing(12)
        right_box.addLayout(self.actions)
        right_box.addWidget(self.header_license)
        header.addLayout(right_box, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        self._outer.addWidget(header_widget)

        body_widget = QWidget()
        body_widget.setObjectName("pageBody")
        self.root = QVBoxLayout(body_widget)
        self.root.setContentsMargins(24, 16, 24, 18)
        self.root.setSpacing(16)
        self._outer.addWidget(body_widget, 1)

    def add_action(self, widget):
        self.actions.addWidget(widget)
        return widget

    def add_center_action(self, widget):
        self.center_actions.addWidget(widget)
        return widget

    def set_header_device_status(self, connected):
        enabled = bool(connected)
        if enabled:
            text = "● 设备已连接    |    自动检测: 运行中"
            color = "#15803D"
        else:
            text = "○ 设备未连接    |    自动检测: 未启动"
            color = "#64748B"
        self.header_status.setText(text)
        self.header_status.setStyleSheet(
            f"QLabel {{ color:{color}; font-size:10pt; font-weight:600; }}"
        )

    def set_header_license(self, text, activated=False):
        self.header_license.setText(str(text))
        color = "#15803D" if activated else "#DC2626"
        self.header_license.setStyleSheet(
            f"QLabel {{ color:{color}; font-size:9.5pt; font-weight:600; }}"
        )

    def notify(self, text):
        if self.win and hasattr(self.win, "toast"):
            self.win.toast(text)


def metric(value, key, accent=None):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    value_label = label(value, "metricValue")
    if accent:
        value_label.setStyleSheet(f"QLabel {{ color:{accent}; font-size:15pt; font-weight:700; }}")
    layout.addWidget(value_label)
    layout.addWidget(label(key, "metricKey"))
    return box


def status_dot(text, color="#18A874"):
    widget = QLabel(f"<span style='color:{color}; font-size:12pt'>●</span> {text}")
    widget.setTextFormat(Qt.RichText)
    return widget
