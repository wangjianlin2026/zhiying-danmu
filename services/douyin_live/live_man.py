#!/usr/bin/python
# coding:utf-8

# @FileName:    liveMan.py
# @Time:        2024/1/2 21:51
# @Author:      bubu
# @Project:     douyinLiveWebFetcher

import codecs
import gzip
import hashlib
import os
import random
import re
import string
import subprocess
import threading
import time
import execjs
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import requests
import websocket
from py_mini_racer import MiniRacer

from .ac_signature import get__ac_signature
from .protobuf.douyin import *

from urllib3.util.url import parse_url


BASE_DIR = Path(__file__).resolve().parent


def _log_parse_error(method: str, exc: Exception):
    """生产环境也记录真实解析异常，避免弹幕被静默丢弃。"""
    try:
        path = BASE_DIR.parent.parent / "data" / "douyin_parse_error.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"{method}: {exc!r}\n"
            )
    except Exception:
        pass


def execute_js(js_file: str):
    """
    执行 JavaScript 文件
    :param js_file: JavaScript 文件路径
    :return: 执行结果
    """
    with open(js_file, 'r', encoding='utf-8') as file:
        js_code = file.read()
    
    ctx = execjs.compile(js_code)
    return ctx


@contextmanager
def patched_popen_encoding(encoding='utf-8'):
    original_popen_init = subprocess.Popen.__init__
    
    def new_popen_init(self, *args, **kwargs):
        kwargs['encoding'] = encoding
        original_popen_init(self, *args, **kwargs)
    
    with patch.object(subprocess.Popen, '__init__', new_popen_init):
        yield


def generateSignature(wss, script_file=None):
    """
    出现gbk编码问题则修改 python模块subprocess.py的源码中Popen类的__init__函数参数encoding值为 "utf-8"
    """
    if script_file is None:
        script_file = str(BASE_DIR / 'sign.js')
    params = ("live_id,aid,version_code,webcast_sdk_version,"
              "room_id,sub_room_id,sub_channel_id,did_rule,"
              "user_unique_id,device_platform,device_type,ac,"
              "identity").split(',')
    wss_params = urllib.parse.urlparse(wss).query.split('&')
    wss_maps = {i.split('=')[0]: i.split("=")[-1] for i in wss_params}
    tpl_params = [f"{i}={wss_maps.get(i, '')}" for i in params]
    param = ','.join(tpl_params)
    md5 = hashlib.md5()
    md5.update(param.encode())
    md5_param = md5.hexdigest()
    
    with codecs.open(script_file, 'r', encoding='utf8') as f:
        script = f.read()
    
    ctx = MiniRacer()
    ctx.eval(script)
    
    try:
        signature = ctx.call("get_sign", md5_param)
        return signature
    except Exception as e:
        print(e)
    
    # 以下代码对应js脚本为sign_v0.js
    # context = execjs.compile(script)
    # with patched_popen_encoding(encoding='utf-8'):
    #     ret = context.call('getSign', {'X-MS-STUB': md5_param})
    # return ret.get('X-Bogus')


def generateMsToken(length=182):
    """
    产生请求头部cookie中的msToken字段，其实为随机的107位字符
    :param length:字符位数
    :return:msToken
    """
    random_str = ''
    base_str = string.ascii_letters + string.digits + '-_'
    _len = len(base_str) - 1
    for _ in range(length):
        random_str += base_str[random.randint(0, _len)]
    return random_str


class DouyinLiveWebFetcher:
    
    def __init__(self, live_id, abogus_file=None, on_event=None):
        """
        直播间弹幕抓取对象
        :param live_id: 直播间的直播id，打开直播间web首页的链接如：https://live.douyin.com/261378947940，
                        其中的261378947940即是live_id
        """
        self.abogus_file = abogus_file or str(BASE_DIR / 'a_bogus.js')
        self.on_event = on_event
        self.__ttwid = None
        self.__room_id = None
        self.session = requests.Session()
        self.live_id = live_id
        self.host = "https://www.douyin.com/"
        self.live_url = "https://live.douyin.com/"
        # 抖音连击礼物每个事件都带累计数量，不合并会连续刷屏念礼物。
        self._gift_last: dict[tuple, dict] = {}
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
        self.headers = {
            'User-Agent': self.user_agent
        }
    
    def start(self):
        self._connectWebSocket()
    
    def stop(self):
        ws = getattr(self, "ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    
    @property
    def ttwid(self):
        """
        产生请求头部cookie中的ttwid字段，访问抖音网页版直播间首页可以获取到响应cookie中的ttwid
        :return: ttwid
        """
        if self.__ttwid:
            return self.__ttwid
        headers = {
            "User-Agent": self.user_agent,
        }
        try:
            response = self.session.get(self.live_url, headers=headers)
            response.raise_for_status()
        except Exception as err:
            print("【X】Request the live url error: ", err)
        else:
            self.__ttwid = response.cookies.get('ttwid')
            return self.__ttwid
    
    @property
    def room_id(self):
        """
        根据直播间的地址获取到真正的直播间roomId，有时会有错误，可以重试请求解决
        :return:room_id
        """
        if self.__room_id:
            return self.__room_id

        # 优先走 webcast 房间信息接口：页面 HTML 经常被验证码中间页拦截，
        # 接口仍能正常返回 roomId，这是掉弹幕最常见的原因。
        try:
            api_room_id = self._room_id_from_api()
        except Exception as err:
            print("【X】Room enter api error: ", err)
            api_room_id = ""
        if api_room_id:
            self.__room_id = api_room_id
            return self.__room_id

        url = self.live_url + self.live_id
        headers = {
            "User-Agent": self.user_agent,
            "cookie": f"ttwid={self.ttwid}&msToken={generateMsToken()}; __ac_nonce=0123407cc00a9e438deb4",
        }
        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
        except Exception as err:
            print("【X】Request the live room url error: ", err)
        else:
            match = re.search(r'roomId\\":\\"(\d+)\\"', response.text)
            if match is None or len(match.groups()) < 1:
                print("【X】No match found for roomId")
                # 停播/需要验证时页面里没有 roomId，不能继续 match.group 导致崩溃。
                raise ValueError("未能获取直播间 roomId（直播间可能已结束或触发验证）")

            self.__room_id = match.group(1)

            return self.__room_id

    def _room_id_from_api(self):
        """从 webcast 房间信息接口取 roomId，绕开 HTML 验证码拦截。"""
        api = (
            "https://live.douyin.com/webcast/room/web/enter/?aid=6383"
            "&app_name=douyin_web&live_id=1&device_platform=web"
            "&language=zh-CN&enter_from=web_live&cookie_enabled=true"
            "&screen_width=1536&screen_height=864&browser_language=zh-CN"
            "&browser_platform=Win32&browser_name=Edge&browser_version=140.0.0.0"
            f"&web_rid={self.live_id}"
        )
        headers = {
            "User-Agent": self.user_agent,
            "Referer": "https://live.douyin.com/",
            "cookie": (
                f"ttwid={self.ttwid}; msToken={generateMsToken()}; "
                "__ac_nonce=0123407cc00a9e438deb2"
            ),
        }
        response = self.session.get(api, headers=headers, timeout=10)
        response.raise_for_status()
        payload = response.json() or {}
        rooms = ((payload.get("data") or {}).get("data")) or []
        if not rooms:
            return ""
        room = rooms[0] or {}
        return str(room.get("id_str") or room.get("id") or "")
    
    def get_ac_nonce(self):
        """
        获取 __ac_nonce
        """
        resp_cookies = self.session.get(self.host, headers=self.headers).cookies
        return resp_cookies.get("__ac_nonce")
    
    def get_ac_signature(self, __ac_nonce: str = None) -> str:
        """
        获取 __ac_signature
        """
        __ac_signature = get__ac_signature(self.host[8:], __ac_nonce, self.user_agent)
        self.session.cookies.set("__ac_signature", __ac_signature)
        return __ac_signature
    
    def get_a_bogus(self, url_params: dict):
        """
        获取 a_bogus
        """
        url = urllib.parse.urlencode(url_params)
        ctx = execute_js(self.abogus_file)
        _a_bogus = ctx.call("get_ab", url, self.user_agent)
        return _a_bogus
    
    def get_room_status(self):
        """
        获取直播间开播状态:
        room_status: 2 直播已结束
        room_status: 0 直播进行中
        """
        msToken = generateMsToken()
        nonce = self.get_ac_nonce()
        signature = self.get_ac_signature(nonce)
        url = ('https://live.douyin.com/webcast/room/web/enter/?aid=6383'
               '&app_name=douyin_web&live_id=1&device_platform=web&language=zh-CN&enter_from=page_refresh'
               '&cookie_enabled=true&screen_width=5120&screen_height=1440&browser_language=zh-CN&browser_platform=Win32'
               '&browser_name=Edge&browser_version=140.0.0.0'
               f'&web_rid={self.live_id}'
               f'&room_id_str={self.room_id}'
               '&enter_source=&is_need_double_stream=false&insert_task_id=&live_reason=&msToken=' + msToken)
        query = parse_url(url).query
        params = {i[0]: i[1] for i in [j.split('=') for j in query.split('&')]}
        a_bogus = self.get_a_bogus(params)  # 计算a_bogus,成功率不是100%，出现失败时重试即可
        url += f"&a_bogus={a_bogus}"
        headers = self.headers.copy()
        headers.update({
            'Referer': f'https://live.douyin.com/{self.live_id}',
            'Cookie': f'ttwid={self.ttwid};__ac_nonce={nonce}; __ac_signature={signature}',
        })
        resp = self.session.get(url, headers=headers)
        data = resp.json().get('data')
        if data:
            room_status = data.get('room_status')
            user = data.get('user')
            user_id = user.get('id_str')
            nickname = user.get('nickname')
            print(f"【{nickname}】[{user_id}]直播间：{['正在直播', '已结束'][bool(room_status)]}.")
    
    def _connectWebSocket(self):
        """
        连接抖音直播间websocket服务器，请求直播间数据
        """
        wss = ("wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/?app_name=douyin_web"
               "&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
               "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web&cookie_enabled=true"
               "&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32"
               "&browser_name=Mozilla"
               "&browser_version=5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,"
               "%20like%20Gecko)%20Chrome/126.0.0.0%20Safari/537.36"
               "&browser_online=true&tz_name=Asia/Shanghai"
               "&cursor=d-1_u-1_fh-7392091211001140287_t-1721106114633_r-1"
               f"&internal_ext=internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:7319483754668557238"
               f"|first_req_ms:1721106114541|fetch_time:1721106114633|seq:1|wss_info:0-1721106114633-0-0|"
               f"wrds_v:7392094459690748497"
               f"&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3&endpoint=live_pc&support_wrds=1"
               f"&user_unique_id=7319483754668557238&im_path=/webcast/im/fetch/&identity=audience"
               f"&need_persist_msg_count=15&insert_task_id=&live_reason=&room_id={self.room_id}&heartbeatDuration=0")
        
        signature = generateSignature(wss)
        wss += f"&signature={signature}"
        
        headers = {
            "cookie": f"ttwid={self.ttwid}",
            'user-agent': self.user_agent,
        }
        self.ws = websocket.WebSocketApp(wss,
                                         header=headers,
                                         on_open=self._wsOnOpen,
                                         on_message=self._wsOnMessage,
                                         on_error=self._wsOnError,
                                         on_close=self._wsOnClose)
        try:
            # 抖音用自定义心跳包，不能用 websocket 层的 ping/pong：
            # 实测服务端不回 pong，会误判断线（00:32:56 ping/pong timed out）。
            self.ws.run_forever()
        except Exception:
            self.stop()
            raise
    
    def _sendHeartbeat(self):
        """
        发送心跳包
        """
        while True:
            try:
                heartbeat = PushFrame(payload_type='hb').SerializeToString()
                self.ws.send(heartbeat, websocket.ABNF.OPCODE_PING)
                print("【√】发送心跳包")
            except Exception as e:
                print("【X】心跳包检测错误: ", e)
                # 心跳发不出去通常说明连接已半死，主动关闭让主循环重连
                try:
                    self.ws.close()
                except Exception:
                    pass
                break
            else:
                time.sleep(5)
    
    def _wsOnOpen(self, ws):
        """
        连接建立成功
        """
        print("【√】WebSocket连接成功.")
        threading.Thread(target=self._sendHeartbeat, daemon=True).start()
    
    def _wsOnMessage(self, ws, message):
        """
        接收到数据
        :param ws: websocket实例
        :param message: 数据
        """
        
        # 单帧解析失败不能影响后续弹幕接收。
        try:
            package = PushFrame().parse(message)
            response = Response().parse(gzip.decompress(package.payload))
        except Exception as exc:
            _log_parse_error("PushFrame", exc)
            return
        
        # 返回直播间服务器链接存活确认消息，便于持续获取数据
        if response.need_ack:
            ack = PushFrame(log_id=package.log_id,
                            payload_type='ack',
                            payload=response.internal_ext.encode('utf-8')
                            ).SerializeToString()
            ws.send(ack, websocket.ABNF.OPCODE_BINARY)
        
        # 根据消息类别解析消息体
        for msg in response.messages_list:
            method = msg.method
            debug = bool(os.environ.get("DOUYIN_DEBUG"))
            if debug:
                try:
                    with open(BASE_DIR.parent.parent / "data" / "douyin_debug.log", "a", encoding="utf-8") as fh:
                        fh.write(f"{time.strftime('%H:%M:%S')} METHOD {method}\n")
                except Exception:
                    pass
            handler = {
                    'WebcastChatMessage': self._parseChatMsg,  # 聊天消息
                    'WebcastGiftMessage': self._parseGiftMsg,  # 礼物消息
                    'WebcastLikeMessage': self._parseLikeMsg,  # 点赞消息
                    'WebcastMemberMessage': self._parseMemberMsg,  # 进入直播间消息
                    'WebcastSocialMessage': self._parseSocialMsg,  # 关注消息
                    'WebcastRoomUserSeqMessage': self._parseRoomUserSeqMsg,  # 直播间统计
                    'WebcastFansclubMessage': self._parseFansclubMsg,  # 粉丝团消息
                    'WebcastControlMessage': self._parseControlMsg,  # 直播间状态消息
                    'WebcastEmojiChatMessage': self._parseEmojiChatMsg,  # 聊天表情包消息
                    'WebcastRoomStatsMessage': self._parseRoomStatsMsg,  # 直播间统计信息
                    'WebcastRoomMessage': self._parseRoomMsg,  # 直播间信息
                    'WebcastRoomRankMessage': self._parseRankMsg,  # 直播间排行榜信息
                    'WebcastRoomStreamAdaptationMessage': self._parseRoomStreamAdaptationMsg,  # 直播间流配置
                }.get(method)
            if handler is None:
                # 抖音协议会持续增加新类型，未知类型直接跳过，不能调用 None。
                continue
            try:
                handler(msg.payload)
            except Exception as exc:
                _log_parse_error(method, exc)
                if debug:
                    try:
                        with open(BASE_DIR.parent.parent / "data" / "douyin_debug.log", "a", encoding="utf-8") as fh:
                            fh.write(f"{time.strftime('%H:%M:%S')} ERROR {method}: {exc!r}\n")
                    except Exception:
                        pass
    
    def _wsOnError(self, ws, error):
        print("WebSocket error: ", error)
    
    def _wsOnClose(self, ws, *args):
        print("WebSocket connection closed.")
    
    def _emit_event(self, type_id, data):
        if not self.on_event:
            return
        try:
            self.on_event({"Type": type_id, "Data": data})
        except Exception:
            pass

    def _parseChatMsg(self, payload):
        """聊天消息"""
        message = ChatMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        content = message.content
        self._emit_event(3, {
            "User": {"NickName": user_name, "Id": user_id},
            "Content": content,
        })
        print(f"【聊天msg】[{user_id}]{user_name}: {content}")
    
    def _parseGiftMsg(self, payload):
        """礼物消息"""
        message = GiftMessage().parse(payload)
        user_name = message.user.nick_name
        gift_name = message.gift.name
        gift_cnt = message.combo_count
        key = (str(message.user.id), gift_name)
        now = time.time()
        last = self._gift_last.get(key)
        repeat_end = bool(getattr(message, "repeat_end", 0))
        if last is not None and now - last["at"] < 3.0 and not repeat_end:
            # 连击中间态只更新计数，等 repeat_end 或时间窗结束再报一次。
            last["at"] = now
            last["count"] = gift_cnt
            return
        self._gift_last[key] = {"at": now, "count": gift_cnt}
        if len(self._gift_last) > 200:
            cutoff = now - 10.0
            self._gift_last = {
                item_key: item_value
                for item_key, item_value in self._gift_last.items()
                if item_value["at"] >= cutoff
            }
        self._emit_event(5, {
            "User": {"NickName": user_name, "Id": message.user.id},
            "Content": f"送出了 {gift_name}x{gift_cnt}",
            "GiftName": gift_name,
            "GiftCount": gift_cnt,
            "DiamondCount": getattr(message.gift, "diamond_count", 0),
        })
        print(f"【礼物msg】{user_name} 送出了 {gift_name}x{gift_cnt}")
    
    def _parseLikeMsg(self, payload):
        '''点赞消息'''
        message = LikeMessage().parse(payload)
        user_name = message.user.nick_name
        count = message.count
        self._emit_event(4, {
            "User": {"NickName": user_name, "Id": message.user.id},
            "Content": f"点了{count}个赞",
            "Count": count,
            "Total": getattr(message, "total", 0),
        })
        print(f"【点赞msg】{user_name} 点了{count}个赞")
    
    def _parseMemberMsg(self, payload):
        '''进入直播间消息'''
        message = MemberMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        # 抖音的 gender 可能是 0/1/2(未知)，直接下标会 IndexError 导致整条进场事件被丢弃
        gender = {0: "女", 1: "男"}.get(message.user.gender, "未知")
        self._emit_event(1, {
            "User": {"NickName": user_name, "Id": user_id},
            "Content": "进入了直播间",
        })
        print(f"【进场msg】[{user_id}][{gender}]{user_name} 进入了直播间")
    
    def _parseSocialMsg(self, payload):
        '''关注 / 分享消息。

        抖音网页协议会把分享行为放在 WebcastSocialMessage 里：分享时
        share_type/share_target 有值，普通关注两者为空。之前这里一律
        当成关注，导致设置页的“分享”分类永远收不到事件。
        '''
        message = SocialMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        share_type = int(getattr(message, "share_type", 0) or 0)
        share_target = str(getattr(message, "share_target", "") or "").strip()
        if share_type or share_target:
            self._emit_event(6, {
                "User": {"NickName": user_name, "Id": user_id},
                "Content": "分享了直播间",
                "ShareTarget": share_target or "抖音",
            })
            print(f"【分享msg】[{user_id}]{user_name} 分享了直播间 -> {share_target or '抖音'}")
            return
        self._emit_event(2, {
            "User": {"NickName": user_name, "Id": user_id},
            "Content": "关注了主播",
        })
        print(f"【关注msg】[{user_id}]{user_name} 关注了主播")
    
    def _parseRoomUserSeqMsg(self, payload):
        '''直播间统计'''
        message = RoomUserSeqMessage().parse(payload)
        current = message.total
        total = message.total_pv_for_anchor
        self._emit_event(7, {
            "User": {"NickName": "系统"},
            "Content": f"当前观看人数: {current}, 累计观看人数: {total}",
        })
        print(f"【统计msg】当前观看人数: {current}, 累计观看人数: {total}")
    
    def _parseFansclubMsg(self, payload):
        '''粉丝团消息'''
        message = FansclubMessage().parse(payload)
        content = message.content
        self._emit_event(9, {
            "User": {"NickName": getattr(message.user, "nick_name", "系统")},
            "Content": content,
            "Level": 0,
            "FansClubName": "粉丝团",
        })
        print(f"【粉丝团msg】 {content}")
    
    def _parseEmojiChatMsg(self, payload):
        '''聊天表情包消息'''
        message = EmojiChatMessage().parse(payload)
        emoji_id = message.emoji_id
        user = message.user
        common = message.common
        default_content = message.default_content
        self._emit_event(3, {
            "User": {"NickName": getattr(user, "nick_name", "未知用户")},
            "Content": default_content or f"[表情:{emoji_id}]",
        })
        print(f"【聊天表情包id】 {emoji_id},user：{user},common:{common},default_content:{default_content}")
    
    def _parseRoomMsg(self, payload):
        message = RoomMessage().parse(payload)
        common = message.common
        room_id = common.room_id
        print(f"【直播间msg】直播间id:{room_id}")
    
    def _parseRoomStatsMsg(self, payload):
        message = RoomStatsMessage().parse(payload)
        display_long = message.display_long
        self._emit_event(10, {
            "User": {"NickName": "系统"},
            "Content": display_long,
        })
        print(f"【直播间统计msg】{display_long}")
    
    def _parseRankMsg(self, payload):
        message = RoomRankMessage().parse(payload)
        ranks_list = message.ranks_list
        print(f"【直播间排行榜msg】{ranks_list}")
    
    def _parseControlMsg(self, payload):
        '''直播间状态消息'''
        message = ControlMessage().parse(payload)
        
        if message.status == 3:
            self._emit_event(8, {
                "User": {"NickName": "系统"},
                "Content": "直播间已结束",
            })
            print("直播间已结束")
            self.stop()
    
    def _parseRoomStreamAdaptationMsg(self, payload):
        message = RoomStreamAdaptationMessage().parse(payload)
        adaptationType = message.adaptation_type
        print(f'直播间adaptation: {adaptationType}')
