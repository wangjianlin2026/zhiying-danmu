from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from widgets import make_btn


class BaseDialog(QDialog):
    def __init__(self, parent=None, title="", width=460):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setStyleSheet("QDialog { background:#FFFFFF; }")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 14)
        self.body.setSpacing(12)

    def add_buttons(self, ok_text="保存", cancel_text="取消"):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel = make_btn(cancel_text)
        cancel.clicked.connect(self.reject)
        ok = make_btn(ok_text, primary=True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.body.addLayout(row)
        return ok, cancel


class DeviceDialog(BaseDialog):
    """添加/编辑设备弹窗，字段与原版一致。"""

    def __init__(self, parent=None, title="添加设备", devices=None, current=0):
        super().__init__(parent, title, 500)
        self.devices = list(devices or [])
        self.selector = None

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        if devices is not None:
            self.selector = QComboBox()
            self.selector.addItems([item.get("name", "") for item in self.devices])
            if self.devices:
                self.selector.setCurrentIndex(
                    min(max(0, int(current)), len(self.devices) - 1)
                )
            self.selector.currentIndexChanged.connect(self._load_selected)
            form.addRow("选择设备:", self.selector)

        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_edit = QLineEdit("8888")
        self.mac_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        form.addRow("设备名称:", self.name_edit)
        form.addRow("IP地址:", self.host_edit)
        form.addRow("端口:", self.port_edit)
        form.addRow("MAC地址:", self.mac_edit)
        form.addRow("设备描述:", self.desc_edit)
        self.body.addLayout(form)

        self.add_buttons()
        if self.selector is not None and self.devices:
            self._load_selected(self.selector.currentIndex())
        self.name_edit.setFocus()

    def _load_selected(self, index):
        if not self.devices or index < 0 or index >= len(self.devices):
            return
        device = self.devices[index]
        self.name_edit.setText(str(device.get("name", "")))
        self.host_edit.setText(str(device.get("host", "")))
        self.port_edit.setText(str(device.get("port", "")))
        self.mac_edit.setText(str(device.get("mac", "")))
        self.desc_edit.setText(str(device.get("description", "")))

    def values(self):
        return {
            "name": self.name_edit.text().strip(),
            "host": self.host_edit.text().strip(),
            "port": self.port_edit.text().strip(),
            "mac": self.mac_edit.text().strip(),
            "description": self.desc_edit.text().strip(),
        }


class DeleteDeviceDialog(BaseDialog):
    """删除设备弹窗，先选设备再按原版文案二次确认。"""

    def __init__(self, parent=None, devices=None, current=0):
        super().__init__(parent, "删除设备", 460)
        self.devices = list(devices or [])

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        self.selector = QComboBox()
        self.selector.addItems([item.get("name", "") for item in self.devices])
        if self.devices:
            self.selector.setCurrentIndex(
                min(max(0, int(current)), len(self.devices) - 1)
            )
        form.addRow("选择要删除的设备:", self.selector)
        self.body.addLayout(form)

        ok, _cancel = self.add_buttons("删除", "取消")
        ok.clicked.disconnect()
        ok.clicked.connect(self._confirm)

    def _confirm(self):
        index = self.selector.currentIndex()
        if index < 0 or index >= len(self.devices):
            QMessageBox.warning(self, "错误", "请先选择要删除的设备")
            return
        device = self.devices[index]
        box = QMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"确定要删除设备 '{device.get('name', '')}' 吗？\n\n"
            f"IP: {device.get('host', '')}:{device.get('port', '')}"
        )
        delete = box.addButton("删除", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is delete:
            self.accept()


class TextEditDialog(BaseDialog):
    """通用文本编辑弹窗：话题、消息、逼单、关键词、数字模式共用。"""

    def __init__(
        self,
        parent=None,
        title="编辑",
        label_text="内容:",
        value="",
        hint="",
        multiline=False,
        width=520,
    ):
        super().__init__(parent, title, width)
        self.body.addWidget(QLabel(label_text))
        if multiline:
            self.editor = QTextEdit()
            self.editor.setPlainText(value)
            self.editor.setMinimumHeight(110)
        else:
            self.editor = QLineEdit(value)
        self.body.addWidget(self.editor)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet("QLabel { color:#64748B; font-size:9pt; }")
            self.body.addWidget(hint_label)
        self.add_buttons()
        self.editor.setFocus()

    def value(self):
        if isinstance(self.editor, QTextEdit):
            return self.editor.toPlainText().strip()
        return self.editor.text().strip()


class TopicEditDialog(BaseDialog):
    """话题编辑弹窗：同时修改话题内容和所属分类。"""

    def __init__(
        self,
        parent=None,
        title="编辑话题",
        value="",
        categories=None,
        category="",
    ):
        super().__init__(parent, title, 520)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.editor = QLineEdit(value)
        self.editor.setMaxLength(15)
        self.category_combo = QComboBox()
        for item in categories or []:
            self.category_combo.addItem(str(item), str(item))
        index = self.category_combo.findData(str(category))
        if index < 0:
            index = self.category_combo.findText(str(category))
        if index >= 0:
            self.category_combo.setCurrentIndex(index)

        form.addRow("话题内容:", self.editor)
        form.addRow("所属分类:", self.category_combo)
        self.body.addLayout(form)
        hint = QLabel("话题内容最多 15 字；保存后立即生效。")
        hint.setStyleSheet("QLabel { color:#64748B; font-size:9pt; }")
        self.body.addWidget(hint)
        self.add_buttons()
        self.editor.setFocus()

    def value(self):
        return self.editor.text().strip()

    def category(self):
        return str(self.category_combo.currentData() or "").strip()
