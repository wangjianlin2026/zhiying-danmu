import os

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPixmap,
    QPen,
    QRadialGradient,
)
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


COVER_QSS = r"""
QWidget#coverPage {
    background: transparent;
}
QWidget#coverCanvas {
    background: transparent;
}
QScrollArea#coverScroll {
    background: transparent;
    border: none;
}
QScrollArea#coverScroll > QWidget > QWidget {
    background: transparent;
}
QLabel#covBrandLogo {
    min-width: 48px;
    max-width: 48px;
    min-height: 48px;
    max-height: 48px;
    border-radius: 13px;
    background: transparent;
}
QLabel#covBrandTitle {
    color: #1C2230;
    font-size: 17pt;
    font-weight: 800;
}
QLabel#covBrandSub {
    color: #71798B;
    font-size: 9pt;
}
QLabel#covSection {
    color: #3D4555;
    font-size: 10pt;
    font-weight: 800;
}
QLabel#covHero {
    color: #1C2230;
    font-size: 44pt;
    font-weight: 900;
}
QLabel#covSub {
    color: #3D4555;
    font-size: 11pt;
}
QFrame#covMod {
    background: #FFFFFF;
    border: 1px solid #E5E8EF;
    border-radius: 14px;
}
QLabel#covModIcon {
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
    border-radius: 10px;
    background: #EDEAFF;
    color: #4F46E5;
    font-size: 13pt;
    font-weight: 800;
}
QLabel#covModTitle {
    color: #1C2230;
    font-size: 10.5pt;
    font-weight: 800;
}
QLabel#covModSub {
    color: #71798B;
    font-size: 8.5pt;
}
QLabel#covChip {
    background: #FFFFFF;
    border: 1px solid #E5E8EF;
    border-radius: 14px;
    padding: 6px 12px;
    color: #3D4555;
    font-size: 8.5pt;
    font-weight: 600;
}
QFrame#covCard {
    background: #FFFFFF;
    border: 1px solid #D9DDE7;
    border-radius: 20px;
}
QLabel#covCardTitle {
    color: #1C2230;
    font-size: 15pt;
    font-weight: 800;
}
QLabel#covBadge {
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 8.5pt;
    font-weight: 700;
    background: #EEF1F7;
    color: #71798B;
}
QFrame#covStep {
    border: 1px solid #E5E8EF;
    border-radius: 14px;
    background: #F6F8FC;
}
QLabel#covStepDot {
    min-width: 16px;
    max-width: 16px;
    min-height: 16px;
    max-height: 16px;
    border-radius: 8px;
    background: #FFFFFF;
    color: #71798B;
    font-size: 7.5pt;
    font-weight: 900;
}
QLabel#covStepText {
    background: transparent;
    border: none;
    color: #71798B;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#covMessage {
    background: #F6F8FC;
    border: 1px solid #E5E8EF;
    border-radius: 10px;
    padding: 8px 11px;
    color: #3D4555;
    font-size: 8.5pt;
}
QLabel#covFieldTitle {
    color: #3D4555;
    font-size: 9pt;
    font-weight: 700;
}
QPlainTextEdit#covCode {
    border: 1px solid #D9DDE7;
    border-radius: 11px;
    padding: 8px 10px;
    background: #FBFCFE;
    color: #1C2230;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 9pt;
}
QPlainTextEdit#covCode:focus {
    border: 1px solid #4F46E5;
    background: #FFFFFF;
}
QPushButton#covEnter {
    min-height: 40px;
    max-height: 40px;
    border: 1px solid #4F46E5;
    border-radius: 11px;
    background: #4F46E5;
    color: #FFFFFF;
    font-size: 10pt;
    font-weight: 800;
}
QPushButton#covEnter:hover {
    background: #6158EA;
    border-color: #6158EA;
}
QPushButton#covActivate {
    min-height: 40px;
    max-height: 40px;
    border: 1px solid #E6E0F6;
    border-radius: 11px;
    background: #F3EFFF;
    color: #6B4FC9;
    font-size: 9.5pt;
    font-weight: 800;
}
QPushButton#covActivate:hover {
    background: #EDE7FF;
}
QPushButton#covCopy {
    min-height: 40px;
    max-height: 40px;
    border: 1px solid #D9DDE7;
    border-radius: 11px;
    background: #F7F8FC;
    color: #71798B;
    font-size: 9.5pt;
    font-weight: 800;
}
QPushButton#covCopy:hover {
    background: #EEF1F8;
}
QFrame#covContactBox {
    background: #F8F7FF;
    border: 1px solid #E6E1FF;
    border-radius: 12px;
}
QLabel#covContactIcon {
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    border-radius: 9px;
    background: #EDE9FE;
    color: #5B3FC4;
    font-size: 11pt;
    font-weight: 900;
}
QLabel#covContactTitle {
    color: #3D4555;
    font-size: 9pt;
    font-weight: 800;
}
QLabel#covContactHint {
    color: #858CA0;
    font-size: 7.8pt;
}
QPushButton#covContact {
    min-width: 92px;
    max-width: 92px;
    min-height: 34px;
    max-height: 34px;
    border: 1px solid #4F46E5;
    border-radius: 9px;
    background: #4F46E5;
    color: #FFFFFF;
    font-size: 8.8pt;
    font-weight: 800;
}
QPushButton#covContact:hover {
    background: #6158EA;
    border-color: #6158EA;
}
QLabel#covMachine {
    border-radius: 9px;
    padding: 8px;
    background: #F6F8FC;
    color: #71798B;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 8pt;
}
QLabel#covNote {
    color: #71798B;
    font-size: 8.5pt;
}
"""


CONTACT_DIALOG_QSS = r"""
QDialog#contactDialog {
    background: #F7F8FC;
}
QLabel#contactTitle {
    color: #1C2230;
    font-size: 17pt;
    font-weight: 900;
}
QLabel#contactSubTitle {
    color: #71798B;
    font-size: 9pt;
}
QFrame#contactQrCard {
    background: #FFFFFF;
    border: 1px solid #D9DDE7;
    border-radius: 16px;
}
QLabel#contactSteps {
    background: #F8F7FF;
    border: 1px solid #E6E1FF;
    border-radius: 12px;
    padding: 12px 14px;
    color: #3D4555;
    font-size: 9.5pt;
}
QLabel#contactMachine {
    background: #FFFFFF;
    border: 1px solid #E5E8EF;
    border-radius: 10px;
    padding: 9px 11px;
    color: #4B5563;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 8pt;
}
QPushButton#contactCopy {
    min-height: 38px;
    border: 1px solid #4F46E5;
    border-radius: 10px;
    background: #4F46E5;
    color: #FFFFFF;
    font-size: 9.5pt;
    font-weight: 800;
}
QPushButton#contactCopy:hover {
    background: #6158EA;
}
QPushButton#contactClose {
    min-height: 38px;
    border: 1px solid #D9DDE7;
    border-radius: 10px;
    background: #FFFFFF;
    color: #5B6172;
    font-size: 9.5pt;
    font-weight: 800;
}
QPushButton#contactClose:hover {
    background: #EEF1F8;
}
"""


class ContactMerchantDialog(QDialog):
    """展示商家微信二维码和获取授权码的操作说明。"""

    def __init__(self, parent, qr_path, machine_code):
        super().__init__(parent)
        self.setObjectName("contactDialog")
        self.setWindowTitle("联系商家获取授权码")
        self.setModal(True)
        self.setFixedWidth(470)
        self.setStyleSheet(CONTACT_DIALOG_QSS)
        self._machine_code = str(machine_code or "").strip()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title = QLabel("联系商家获取授权码")
        title.setObjectName("contactTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("微信扫码添加商家 · 按本机机器码签发专属授权码")
        subtitle.setObjectName("contactSubTitle")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        qr_card = QFrame()
        qr_card.setObjectName("contactQrCard")
        qr_layout = QVBoxLayout(qr_card)
        qr_layout.setContentsMargins(14, 14, 14, 10)
        qr_layout.setSpacing(7)
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(qr_path)
        if pixmap.isNull():
            qr_label.setText("二维码图片缺失，请联系售后重新获取")
            qr_label.setStyleSheet("color:#D64545; font-size:10pt;")
            qr_label.setFixedHeight(300)
        else:
            qr_label.setPixmap(
                pixmap.scaled(
                    300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        qr_layout.addWidget(qr_label)
        qr_hint = QLabel("长按或截图后使用微信扫一扫")
        qr_hint.setObjectName("contactSubTitle")
        qr_hint.setAlignment(Qt.AlignCenter)
        qr_layout.addWidget(qr_hint)
        layout.addWidget(qr_card)

        steps = QLabel(
            "<b>获取授权码步骤</b><br>"
            "1. 微信扫码添加商家。<br>"
            "2. 点击下方「复制机器码」，把机器码发给商家。<br>"
            "3. 商家按机器码生成专属授权码并发给你。<br>"
            "4. 关闭本窗口，把授权码粘贴到封面授权框，点击「激活授权」。"
        )
        steps.setObjectName("contactSteps")
        steps.setTextFormat(Qt.RichText)
        steps.setWordWrap(True)
        layout.addWidget(steps)

        self.machine_label = QLabel(
            f"本机机器码\n{self._machine_code or '读取失败，请回到封面重新打开'}"
        )
        self.machine_label.setObjectName("contactMachine")
        self.machine_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.machine_label.setWordWrap(True)
        layout.addWidget(self.machine_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_btn = QPushButton("复制机器码")
        self.copy_btn.setObjectName("contactCopy")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_machine_code)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("contactClose")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(self.copy_btn, 1)
        actions.addWidget(close_btn, 1)
        layout.addLayout(actions)

    def _copy_machine_code(self):
        QApplication.clipboard().setText(self._machine_code)
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("复制机器码"))


class CoverPage(QWidget):
    """启动封面：左边介绍产品，右边完成授权并进入工作台。"""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.setObjectName("coverPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(COVER_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("coverScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        canvas = QWidget()
        canvas.setObjectName("coverCanvas")
        canvas.setAttribute(Qt.WA_TranslucentBackground, True)
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(48, 34, 48, 34)
        canvas_layout.setSpacing(0)

        wrap = QWidget()
        wrap.setMinimumWidth(980)
        wrap.setMaximumWidth(1180)
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        wrap_layout = QHBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setSpacing(46)

        left = QWidget()
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addLayout(self._build_brand())
        left_layout.addSpacing(26)

        hero = QLabel(
            "小智<span style='color:#4F46E5'>AI</span> 弹幕<br>"
            "<span style='color:#7C3AED'>自动回复</span>系统"
        )
        hero.setObjectName("covHero")
        hero.setTextFormat(Qt.RichText)
        hero.setWordWrap(True)
        left_layout.addWidget(hero)
        left_layout.addSpacing(14)

        slogan = QLabel("让每一条弹幕，都变成直播间的回应。")
        slogan.setObjectName("covSub")
        slogan.setWordWrap(True)
        left_layout.addWidget(slogan)
        left_layout.addSpacing(7)

        sub = QLabel(
            "弹幕采集、话题互动、逼单话术、小智语音，"
            "一套多平台直播互动工作台全部搞定。"
        )
        sub.setObjectName("covNote")
        sub.setWordWrap(True)
        left_layout.addWidget(sub)
        left_layout.addSpacing(18)

        section = QLabel("核心功能")
        section.setObjectName("covSection")
        left_layout.addWidget(section)
        left_layout.addSpacing(10)

        mods = QGridLayout()
        mods.setHorizontalSpacing(10)
        mods.setVerticalSpacing(10)
        modules = [
            ("播", "多平台弹幕采集", "抖音已接入，更多平台预留"),
            ("类", "智能分类互动", "按消息类型自动匹配话术"),
            ("题", "话题自动轮播", "按间隔发送，持续带节奏"),
            ("单", "逼单话术控制", "手动 / 定时发送，支持间隔"),
            ("声", "小智语音播报", "硬件喇叭 / 软件语音双通道"),
            ("控", "设备与授权管理", "扫描连接、状态监控、到期提醒"),
        ]
        for index, (mark, title, desc) in enumerate(modules):
            mods.addWidget(
                self._module_card(mark, title, desc), index // 2, index % 2
            )
        left_layout.addLayout(mods)
        left_layout.addSpacing(20)

        chips = QHBoxLayout()
        chips.setSpacing(9)
        for text in (
            "● 抖音 已接入",
            "○ 快手 / 小红书 / 视频号 预留",
            "● 本地运行，不上传",
        ):
            chip = QLabel(text)
            chip.setObjectName("covChip")
            chips.addWidget(chip)
        chips.addStretch(1)
        left_layout.addLayout(chips)
        left_layout.addStretch(1)
        wrap_layout.addWidget(left, 1)

        self.card = self._build_card()
        wrap_layout.addWidget(self.card, 0, Qt.AlignVCenter)

        canvas_layout.addStretch(1)
        canvas_layout.addWidget(wrap, 0, Qt.AlignHCenter)
        canvas_layout.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll)

        self.refresh()

    def _build_brand(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("covBrandLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "assets",
            "zhiying-logo.png",
        )
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            logo.setPixmap(
                pixmap.scaled(
                    48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        row.addWidget(logo)

        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel("智映AI")
        title.setObjectName("covBrandTitle")
        subtitle = QLabel("多平台直播互动工作台")
        subtitle.setObjectName("covBrandSub")
        text.addWidget(title)
        text.addWidget(subtitle)
        row.addLayout(text)
        row.addStretch(1)
        return row

    def _module_card(self, mark, title, desc):
        card = QFrame()
        card.setObjectName("covMod")
        card.setMinimumHeight(58)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(11)

        icon = QLabel(mark)
        icon.setObjectName("covModIcon")
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("covModTitle")
        desc_label = QLabel(desc)
        desc_label.setObjectName("covModSub")
        text.addWidget(title_label)
        text.addWidget(desc_label)
        layout.addLayout(text, 1)
        return card

    def _build_card(self):
        card = QFrame()
        card.setObjectName("covCard")
        card.setFixedWidth(420)
        card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(16, 24, 40, 48))
        card.setGraphicsEffect(shadow)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.card_title = QLabel("进入工作台")
        self.card_title.setObjectName("covCardTitle")
        head.addWidget(self.card_title)
        head.addStretch(1)
        self.badge = QLabel("检测授权中")
        self.badge.setObjectName("covBadge")
        self.badge.setFixedHeight(24)
        self.badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        head.addWidget(self.badge)
        layout.addLayout(head)

        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_engine = self._make_step("1", "软件就绪")
        self.step_license = self._make_step("2", "授权可用")
        steps.addWidget(self.step_engine)
        steps.addWidget(self.step_license)
        steps.addStretch(1)
        layout.addLayout(steps)

        self.message = QLabel("正在读取授权状态…")
        self.message.setObjectName("covMessage")
        self.message.setWordWrap(False)
        self.message.setFixedHeight(31)
        self.message.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(self.message)

        contact_box = QFrame()
        contact_box.setObjectName("covContactBox")
        contact_box.setFixedHeight(52)
        contact_row = QHBoxLayout(contact_box)
        contact_row.setContentsMargins(10, 7, 9, 7)
        contact_row.setSpacing(9)
        contact_icon = QLabel("商")
        contact_icon.setObjectName("covContactIcon")
        contact_icon.setAlignment(Qt.AlignCenter)
        contact_row.addWidget(contact_icon)
        contact_text = QVBoxLayout()
        contact_text.setSpacing(0)
        contact_title = QLabel("没有授权码？")
        contact_title.setObjectName("covContactTitle")
        contact_hint = QLabel("联系商家，按机器码开通")
        contact_hint.setObjectName("covContactHint")
        contact_text.addWidget(contact_title)
        contact_text.addWidget(contact_hint)
        contact_row.addLayout(contact_text, 1)
        self.contact_btn = QPushButton("联系商家")
        self.contact_btn.setObjectName("covContact")
        self.contact_btn.setCursor(Qt.PointingHandCursor)
        self.contact_btn.clicked.connect(self._show_contact_dialog)
        contact_row.addWidget(self.contact_btn)
        layout.addWidget(contact_box)

        field_title = QLabel("授权码")
        field_title.setObjectName("covFieldTitle")
        layout.addWidget(field_title)
        self.code_edit = QPlainTextEdit()
        self.code_edit.setObjectName("covCode")
        self.code_edit.setPlaceholderText(
            "已购买？把销售商发来的授权码整串粘到这里"
        )
        self.code_edit.setFixedHeight(92)
        layout.addWidget(self.code_edit)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.enter_btn = QPushButton("进入工作台")
        self.enter_btn.setObjectName("covEnter")
        self.enter_btn.clicked.connect(self._enter_workspace)
        self.activate_btn = QPushButton("激活授权")
        self.activate_btn.setObjectName("covActivate")
        self.activate_btn.clicked.connect(self._activate_license)
        self.copy_btn = QPushButton("复制机器码")
        self.copy_btn.setObjectName("covCopy")
        self.copy_btn.clicked.connect(self._copy_machine_code)
        for button in (self.enter_btn, self.activate_btn, self.copy_btn):
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            actions.addWidget(button, 1)
        layout.addLayout(actions)

        self.machine_label = QLabel("机器码读取中…")
        self.machine_label.setObjectName("covMachine")
        self.machine_label.setAlignment(Qt.AlignCenter)
        self.machine_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.machine_label.setWordWrap(False)
        self.machine_label.setFixedHeight(30)
        layout.addWidget(self.machine_label)

        note = QLabel("本地校验授权，不联网；机器码绑定本机，换机需重新授权。")
        note.setObjectName("covNote")
        note.setWordWrap(False)
        layout.addWidget(note)
        return card

    def _make_step(self, dot_text, text):
        frame = QFrame()
        frame.setObjectName("covStep")
        frame.setFixedHeight(28)
        frame.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        row = QHBoxLayout(frame)
        row.setContentsMargins(5, 4, 13, 4)
        row.setSpacing(7)
        dot = QLabel(dot_text)
        dot.setObjectName("covStepDot")
        dot.setAlignment(Qt.AlignCenter)
        label = QLabel(text)
        label.setObjectName("covStepText")
        row.addWidget(dot)
        row.addWidget(label)
        frame._dot = dot
        frame._text = label
        return frame

    def _set_step(self, frame, dot_text, text, state):
        frame._dot.setText(dot_text)
        frame._text.setText(text)
        if state == "done":
            frame.setStyleSheet(
                "QFrame#covStep { border:1px solid #BFE3C8; border-radius:14px; "
                "background:#E5F7EF; }"
                "QLabel#covStepDot { min-width:16px; max-width:16px; "
                "min-height:16px; max-height:16px; border-radius:8px; "
                "background:#0E9F6E; color:#FFFFFF; font-size:7.5pt; "
                "font-weight:900; }"
                "QLabel#covStepText { background:transparent; border:none; "
                "color:#0E9F6E; font-size:8.5pt; font-weight:700; }"
            )
        else:
            frame.setStyleSheet(
                "QFrame#covStep { border:1px solid #E5E8EF; border-radius:14px; "
                "background:#F6F8FC; }"
                "QLabel#covStepDot { min-width:16px; max-width:16px; "
                "min-height:16px; max-height:16px; border-radius:8px; "
                "background:#FFFFFF; color:#71798B; font-size:7.5pt; "
                "font-weight:900; }"
                "QLabel#covStepText { background:transparent; border:none; "
                "color:#71798B; font-size:8.5pt; font-weight:700; }"
            )

    def refresh(self):
        status = {}
        try:
            status = self.win.license_status() or {}
        except Exception as exc:
            status = {"activated": False, "message": str(exc)}

        activated = bool(status.get("activated"))
        self.machine_label.setText(
            f"机器码  {getattr(self.win, 'machine_code', '') or '读取失败'}"
        )
        self._set_step(self.step_engine, "✓", "软件就绪", "done")

        if activated:
            expiry = str(status.get("expiry_date", "") or "").strip()
            expiry = expiry[:10] if expiry else "长期"
            days = status.get("remaining_days", 0)
            extra = "" if days == 999999 else f"，剩余 {days} 天"
            self.badge.setText("已授权")
            self.card_title.setText("欢迎回来")
            self.badge.setStyleSheet(
                "QLabel { border-radius:12px; padding:4px 10px; "
                "font-size:8.5pt; font-weight:700; background:#E5F7EF; "
                "color:#0E9F6E; }"
            )
            self._set_step(self.step_license, "✓", "授权可用", "done")
            self.message.setText(
                f"已授权 · 到期 {expiry}{extra}"
            )
        else:
            reason = str(status.get("message", "") or "未找到有效授权")
            self.card_title.setText("进入工作台")
            self.badge.setText("未授权")
            self.badge.setStyleSheet(
                "QLabel { border-radius:12px; padding:4px 10px; "
                "font-size:8.5pt; font-weight:700; background:#FDECEC; "
                "color:#D64545; }"
            )
            self._set_step(self.step_license, "2", "授权待激活", "pending")
            self.message.setText(
                f"未检测到有效授权（{reason}）"
            )

    def _enter_workspace(self):
        status = {}
        try:
            status = self.win.license_status() or {}
        except Exception:
            pass
        if not status.get("activated"):
            QMessageBox.warning(
                self,
                "需要授权",
                "当前未检测到有效授权，请先粘贴授权码并点击「激活授权」。",
            )
            self.code_edit.setFocus()
            return
        self.win.enter_workspace()

    def _activate_license(self):
        code = self.code_edit.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "提示", "请先粘贴授权码")
            self.code_edit.setFocus()
            return
        result = self.win.activate_license(code)
        if result.get("valid"):
            self.code_edit.clear()
            self.refresh()
            main_page = getattr(self.win, "pages", {}).get("main")
            if main_page is not None and hasattr(main_page, "refresh"):
                main_page.refresh()
            QMessageBox.information(
                self, "授权成功", result.get("message", "激活成功")
            )
        else:
            QMessageBox.warning(
                self, "授权失败", result.get("message", "授权码无效")
            )

    def _copy_machine_code(self):
        QApplication.clipboard().setText(
            str(getattr(self.win, "machine_code", "") or "")
        )
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("复制机器码"))

    def _show_contact_dialog(self):
        qr_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "assets",
            "contact_qr_wangjianlin.jpg",
        )
        dialog = ContactMerchantDialog(
            self, qr_path, getattr(self.win, "machine_code", "")
        )
        dialog.exec_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        background = QLinearGradient(0, 0, 0, rect.height())
        background.setColorAt(0.0, QColor("#EEF1F8"))
        background.setColorAt(1.0, QColor("#E5EAF3"))
        painter.fillRect(rect, background)

        glow_one = QRadialGradient(110, 40, 620)
        glow_one.setColorAt(0.0, QColor(99, 102, 241, 86))
        glow_one.setColorAt(0.62, QColor(99, 102, 241, 26))
        glow_one.setColorAt(1.0, QColor(99, 102, 241, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow_one)
        painter.drawEllipse(-250, -310, 900, 900)

        glow_two = QRadialGradient(
            rect.width() - 100, rect.height() + 120, 560
        )
        glow_two.setColorAt(0.0, QColor(124, 58, 237, 58))
        glow_two.setColorAt(0.64, QColor(124, 58, 237, 18))
        glow_two.setColorAt(1.0, QColor(124, 58, 237, 0))
        painter.setBrush(glow_two)
        painter.drawEllipse(
            rect.width() - 660, rect.height() - 500, 900, 900
        )

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(79, 70, 229, 15), 1))
        for x in range(0, rect.width(), 34):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), 34):
            painter.drawLine(0, y, rect.width(), y)
        painter.end()
