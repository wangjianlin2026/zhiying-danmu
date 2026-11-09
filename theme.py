APP_QSS = r"""
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC";
    font-size: 10pt;
    color: #1F2937;
}
QMainWindow, QWidget#root {
    background: #EEF2F7;
}
QWidget#pageHeader {
    background: #D9DEE5;
    border: none;
    border-bottom: 1px solid #C9CED6;
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
    margin: 0px;
}
QStackedWidget {
    border: none;
}
QWidget#scrollHolder {
    background: transparent;
}
QWidget#sidebar {
    background: #252A31;
    border-right: 1px solid #343941;
}
QWidget#sidebar QLabel {
    color: #F5F5F5;
    font-size: 9pt;
    font-weight: 500;
}
QLabel#brandMark {
    background: #2F6FED;
    color: white;
    border-radius: 10px;
    font-size: 14pt;
    font-weight: 700;
}
QLabel#brandName {
    color: #FFFFFF;
    font-size: 12pt;
    font-weight: 700;
}
QLabel#brandSub {
    color: #C9C9C9;
    font-size: 8.5pt;
    font-weight: 500;
}
QLabel#sideBrandLogo {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    background: transparent;
}
QLabel#sideBrandTitle {
    color: #FFFFFF;
    font-size: 11.5pt;
    font-weight: 800;
}
QLabel#sideBrandSub {
    color: #BDBDBD;
    font-size: 7.5pt;
    font-weight: 500;
}
QWidget#sidebar QLabel#navGroup {
    color: #FFFFFF;
    font-size: 11pt;
    font-weight: 700;
    padding: 14px 0 9px 12px;
    border-bottom: 1px solid #FFFFFF;
}
QFrame#navDivider {
    background: #FFFFFF;
    border: none;
    margin: 0 12px;
}
QWidget#sidebar QPushButton#nav {
    min-width: 0px;
    max-width: 10000px;
    min-height: 34px;
    max-height: 34px;
    padding-left: 56px;
    text-align: left;
    border: none;
    border-radius: 7px;
    background: transparent;
    color: #FFFFFF;
    font-size: 10pt;
    font-weight: 500;
}
QWidget#sidebar QPushButton#nav:hover {
    background: #30363F;
    color: #FFFFFF;
}
QWidget#sidebar QPushButton#nav:checked {
    background: #3A414B;
    color: #FFFFFF;
    font-weight: 600;
}
QWidget#sidebar QPushButton#sideExit {
    min-height: 36px;
    max-height: 36px;
    padding: 0 12px;
    border: 1px solid #3B424C;
    border-radius: 8px;
    background: #2E343D;
    color: #E5E7EB;
    font-size: 10pt;
    font-weight: 600;
}
QWidget#sidebar QPushButton#sideExit:hover {
    background: #3A414B;
    border-color: #4A515C;
    color: #FFFFFF;
}
QFrame#sideFooter {
    background: transparent;
    border: none;
    border-top: 1px solid #343941;
}
QWidget#sidebar QPushButton#sideHome {
    min-height: 40px;
    max-height: 40px;
    padding: 0px;
    border: 1px solid #3B424C;
    border-radius: 10px;
    background: #2E343D;
    text-align: left;
}
QWidget#sidebar QPushButton#sideHome:hover {
    background: #3A414B;
    border-color: #4A515C;
}
QWidget#sidebar QLabel#sideHomeIcon {
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    border-radius: 8px;
    background: #2B3A55;
    color: #8FB0FF;
    font-size: 12pt;
    font-weight: 800;
}
QWidget#sidebar QLabel#sideHomeText {
    background: transparent;
    color: #F3F4F6;
    font-size: 10pt;
    font-weight: 700;
}
QWidget#sidebar QPushButton#sideLicense {
    min-height: 46px;
    max-height: 46px;
    padding: 0px;
    border: 1px solid #3B424C;
    border-radius: 10px;
    background: #2E343D;
    text-align: left;
}
QWidget#sidebar QPushButton#sideLicense:hover {
    background: #3A414B;
    border-color: #4A515C;
}
QWidget#sidebar QLabel#sideLicenseAvatar {
    border-radius: 15px;
    background: #3B4250;
    color: #9EA7B7;
    font-size: 11pt;
    font-weight: 900;
}
QWidget#sidebar QLabel#sideLicenseTitle {
    background: transparent;
    color: #E5E7EB;
    font-size: 9.5pt;
    font-weight: 700;
}
QWidget#sidebar QLabel#sideLicenseSub {
    background: transparent;
    color: #9CA3AF;
    font-size: 7.5pt;
    font-weight: 500;
}
QLabel#sideHealth {
    color: #E5E5E5;
    font-size: 9pt;
    font-weight: 500;
}
QLabel#sideVersion {
    color: #BDBDBD;
    font-size: 8.5pt;
}
QFrame#card {
    background: white;
    border: 1px solid #E3E8EF;
    border-radius: 12px;
}
QLabel#cardTitle {
    font-size: 11pt;
    font-weight: 700;
    color: #FFFFFF;
    background: #111827;
    border: none;
    border-radius: 5px;
    padding: 3px 9px;
}
QLabel#cardSub, QLabel#muted {
    color: #64748B;
    font-size: 9pt;
}
QLabel#h1 {
    font-size: 15pt;
    font-weight: 700;
    color: #0F172A;
}
QLabel#pageSub {
    color: #64748B;
    font-size: 9pt;
}
QGroupBox#sourceGroup {
    background: #FFFFFF;
    border: 1px solid #E3E8EF;
    border-radius: 10px;
    margin-top: 26px;
    padding: 16px 14px 14px 14px;
    font-size: 10.5pt;
    font-weight: 700;
    color: #0F172A;
}
QGroupBox#sourceGroup::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 3px 10px;
    background: #111827;
    color: #FFFFFF;
    font-size: 10.5pt;
    font-weight: 700;
    border-radius: 5px;
}
QLabel#metricValue {
    font-size: 14pt;
    font-weight: 700;
    color: #0F172A;
}
QLabel#metricKey {
    color: #64748B;
    font-size: 9pt;
}
QPushButton {
    min-width: 0px;
    max-width: 10000px;
    max-height: 30px;
    min-height: 30px;
    padding: 0 12px;
    border-radius: 8px;
    border: 1px solid #E3E8EF;
    background: #FFFFFF;
    color: #334155;
    font-size: 10pt;
    font-weight: 600;
}
QPushButton:hover {
    border-color: #B8C7E6;
    background: #F7F9FC;
}
QPushButton:pressed {
    background: #EDF2FA;
    padding-top: 1px;
}
QPushButton#primary {
    background: #2F6FED;
    border-color: #2F6FED;
    color: white;
}
QPushButton#primary:hover {
    background: #3F7CF2;
    border-color: #3F7CF2;
}
QPushButton#primary:pressed {
    background: #235FD3;
}
QPushButton#ghost {
    background: #F7F9FC;
}
QPushButton#small {
    min-width: 0px;
    max-width: 10000px;
    max-height: 30px;
    min-height: 30px;
    padding: 0 10px;
    font-size: 10pt;
}
QScrollArea#pageScroll {
    background: transparent;
    border: none;
}
QScrollArea#pageScroll > QWidget > QWidget {
    background: transparent;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    min-height: 32px;
    max-height: 32px;
    border: 1px solid #DFE5EE;
    border-radius: 8px;
    padding: 0 10px;
    background: #FBFCFE;
    font-size: 10pt;
    selection-background-color: #DCE8FF;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #2F6FED;
    background: #FFFFFF;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    border: 1px solid #DFE5EE;
    border-radius: 6px;
    background: white;
    selection-background-color: #EAF1FF;
    selection-color: #182230;
}
QCheckBox, QRadioButton {
    spacing: 7px;
    color: #425066;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
}
QTableWidget, QListWidget, QTreeWidget {
    border: 1px solid #EDF0F5;
    border-radius: 8px;
    background: white;
    gridline-color: #F2F4F8;
    font-size: 10pt;
    selection-background-color: #EAF1FF;
    selection-color: #182230;
    outline: none;
}
QTableWidget::item {
    padding: 5px;
}
QHeaderView::section {
    background: #F7F9FC;
    color: #64748B;
    border: none;
    border-bottom: 1px solid #EDF0F5;
    padding: 6px;
    font-size: 9.5pt;
    font-weight: 600;
}
QScrollBar:vertical {
    width: 8px;
    background: transparent;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #C9D3E2;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    height: 8px;
    background: transparent;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #C9D3E2;
    border-radius: 4px;
    min-width: 24px;
}
QStatusBar {
    background: #FFFFFF;
    border-top: 1px solid #E3E8EF;
    color: #64748B;
    font-size: 9pt;
}
QToolTip {
    background: #182230;
    color: white;
    border: none;
    padding: 5px 8px;
    border-radius: 5px;
}
QTabWidget#settingsTabs::pane {
    border: 1px solid #E3E8EF;
    border-radius: 12px;
    background: white;
    top: -1px;
}
QTabWidget#settingsTabs QTabBar::tab {
    min-width: 108px;
    min-height: 32px;
    padding: 0 12px;
    margin-right: 6px;
    border: 1px solid #E3E8EF;
    border-radius: 8px;
    background: #F7F9FC;
    color: #64748B;
    font-size: 10pt;
    font-weight: 600;
}
QTabWidget#settingsTabs QTabBar::tab:selected {
    background: #2F6FED;
    border-color: #2F6FED;
    color: white;
}
QTabWidget#settingsTabs QTabWidget#settingsTabs::pane {
    border: 1px solid #E3E8EF;
    border-radius: 10px;
    background: #FFFFFF;
    top: -1px;
}
QTabWidget#settingsTabs QTabWidget#settingsTabs QTabBar::tab {
    min-width: 92px;
    min-height: 30px;
    padding: 0 10px;
    margin-right: 5px;
    border: 1px solid #E3E8EF;
    border-radius: 7px;
    background: #F7F9FC;
    color: #475569;
    font-size: 10pt;
    font-weight: 600;
}
QTabWidget#settingsTabs QTabWidget#settingsTabs QTabBar::tab:selected {
    background: #2F6FED;
    border-color: #2F6FED;
    color: white;
}
"""
