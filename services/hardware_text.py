"""硬件文本通道的短句适配。

小智硬件只接受极短的意图文本：实测 15 个纯汉字可正常对话，16 字带标点
就会触发 "Detect is only for wake words"。话题/逼单原文是面向直播间的
长文案，必须转成一句短指令，让小智自己展开说，而不是硬截断。
"""
from __future__ import annotations

import re

# 必须和 hardware_speaker.MAX_CHARS 保持一致（13）。之前这里是 15，
# 生成的内容超出 13 后又被发送层硬切，句尾/昵称就会被砍掉，听起来
# 就是“说着说着掉字”。统一到 13 后由这里按语义压缩，不再二次硬截断。
MAX_PROMPT_CHARS = 13
PREFIX = "请说："

_STRIP_CHARS = "，。！？!?、,.：:；;…~～\"'“”‘’（）()【】[]《》<> "

# 按语义把长文案压成一句小智能听懂的短指令。顺序有讲究：先匹配更具体的。
_RULES = (
    (("理性消费", "按需购买"), "提醒大家理性消费"),
    (("划走", "别走", "停留", "留下来", "别急着"), "提醒大家别划走"),
    (("第一次来", "新进来", "新来的", "新朋友", "初次见"), "欢迎新来的朋友"),
    (("关注", "不迷路"), "提醒大家点关注"),
    (("粉丝团", "加团"), "提醒大家加粉丝团"),
    (("点赞", "顶上去"), "提醒大家点点赞"),
    (("公屏", "扣字", "打在"), "提醒大家公屏互动"),
    (("送人", "送朋友"), "说说能不能送人"),
    (("售后", "保障", "退换"), "说说售后保障"),
    (("库存", "抓紧", "最后"), "提醒大家抓紧下单"),
    (("下单", "入手", "拍下", "购物车", "划算", "优惠", "福利"), "提醒大家抓紧下单"),
    (("规格", "怎么选", "哪类人", "适合"), "说说怎么选规格"),
    (("怎么用", "用法", "技巧", "使用要注意"), "讲讲产品怎么用"),
    (("卖点", "打动", "最厉害"), "讲讲产品卖点"),
    (("价格", "多少钱", "值不值"), "说说价格值不值"),
    (("产品", "主角", "介绍"), "介绍主打产品"),
    (("天气",), "聊聊今天天气"),
    (("冷知识", "小知识"), "说个冷知识"),
    (("未来", "科技"), "聊聊未来科技"),
    (("人工智能", "AI"), "聊聊人工智能"),
    (("自我介绍",), "简单自我介绍一下"),
    (("打招呼", "问好"), "跟大家打个招呼"),
    (("氛围",), "聊聊直播间氛围"),
    (("问题", "回答"), "回答观众的问题"),
    (("总结",), "总结今天重点"),
    (("关注",), "提醒大家点关注"),
)


def _clean(text: str) -> str:
    body = str(text or "").strip()
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"@\S+", "", body)
    body = re.sub(r"\s+", "", body)
    body = re.sub(r"^以随机的方式", "", body)
    body = re.sub(r"^(随机|随便)", "", body)
    return body.strip(_STRIP_CHARS)


def _core_clause(body: str) -> str:
    """长文案取最像“动作指令”的一小句，避免硬切在半个词上。"""
    clauses = [
        item.strip(_STRIP_CHARS)
        for item in re.split(r"[，。！？!?；;\n]+", body)
        if item.strip(_STRIP_CHARS)
    ]
    if not clauses:
        return body
    verbs = (
        "提醒", "介绍", "说说", "讲讲", "聊聊", "回答", "引导", "欢迎",
        "感谢", "总结", "问", "让", "给",
    )
    for clause in clauses:
        if any(word in clause for word in verbs):
            return clause
    return clauses[0]


def build_prompt(text: str, limit: int = MAX_PROMPT_CHARS) -> str:
    """把任意话题/逼单/弹幕文本转成不超过 limit 字的硬件指令。"""
    body = _clean(text)
    if not body:
        return ""

    # 已经够短就原样用；末尾标点已去掉，避免 16 字触顶。
    if len(body) <= limit:
        return body

    for keywords, instruct in _RULES:
        if any(word in body for word in keywords):
            room = max(1, limit)
            return instruct[:room].strip(_STRIP_CHARS)

    core = _core_clause(body)
    core = core.strip(_STRIP_CHARS)
    return core[:limit].strip(_STRIP_CHARS)


def build_hardware_message(text: str, limit: int = MAX_PROMPT_CHARS) -> str:
    """返回带“请说：”前缀的完整硬件消息，总长受 limit 约束。"""
    room = max(1, limit - len(PREFIX))
    body = build_prompt(text, room)
    if not body:
        return ""
    return f"{PREFIX}{body}"
