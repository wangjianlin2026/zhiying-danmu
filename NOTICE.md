# 第三方组件与许可说明

## 本项目

智映AI · 小智AI弹幕自动回复系统  
Copyright (C) 2026 智映AI  
License: GNU Affero General Public License v3.0

## 抖音直播采集

`services/douyin_live/` 中的直播连接、签名与 protobuf 相关代码参考或衍生自：

- DouyinLiveWebFetcher: <https://github.com/saermart/DouyinLiveWebFetcher>
- 许可证：GNU Affero General Public License v3.0

`services/douyin_live/protobuf/douyin.py` 中的注释还引用了以下项目的 proto 定义：

- <https://github.com/scx567888/live-room-watcher>
- <https://github.com/HaoDong108/DouyinBarrageGrab>
- <https://github.com/jwwsjlm/douyinLive>

如相关项目另有许可证要求，请以其仓库声明为准。

## Python 与桌面依赖

本项目使用 PyQt5、requests、websockets、OpenCV、NumPy、Pillow、sounddevice、
pywin32、comtypes、WMI、PyExecJS、mini-racer、protobuf 等第三方库。
各依赖的版权与许可证归其原作者所有，使用前请阅读对应项目的 LICENSE。

## 免责声明

本项目按“现状”提供，不附带任何明示或暗示担保。使用者应自行确认其使用场景合法合规。

