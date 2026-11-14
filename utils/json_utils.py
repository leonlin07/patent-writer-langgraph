"""
从 LLM 输出中稳健提取 JSON 的工具

背景：LLM 返回的结构化内容往往是"看起来像 JSON"的文本，直接用
``re.search(r'\\{[\\s\\S]*\\}')`` + ``json.loads`` 极易失败，典型原因：

1. 字符串值里含**未转义的换行/制表符**（多段落字段几乎是必然）
2. 字符串值里含未转义的双引号
3. 外层裹了 ```json ... ``` 代码围栏
4. 对象/数组末尾有多余逗号（trailing comma）
5. 贪婪正则把正文里其它花括号也吞进来

失败后各 Agent 会静默退回正则兜底，导致丢掉 LLM 的结构化理解——
这是本工程原先"内容变空/质量骤降"的重要诱因之一。

对外只暴露一个函数：``extract_json(text)``。
"""
import json
import re
from typing import Any, Optional


def _strip_fences(text: str) -> str:
    """去掉 ```json ... ``` 之类的代码围栏"""
    text = text.strip()
    m = re.search(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n?```$", text)
    if m:
        return m.group(1)
    # 退化情况：开头有围栏但结尾缺失
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    return text


def _slice_balanced(text: str) -> Optional[str]:
    """
    用括号配对扫描取出第一个完整的 {...} 片段。

    相比贪婪正则，它能正确处理字符串内部的花括号，例如
    ``{"a": "if (x) { return }"}``。
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    esc = False

    for i in range(start, len(text)):
        ch = text[i]

        if esc:                      # 上一个字符是转义符
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None  # 括号未闭合（常见于 max_tokens 截断）


def _repair(text: str, lenient: bool = False) -> str:
    """
    修正常见违规：字符串内裸控制符、行尾多余逗号。

    Args:
        text: 待修复文本
        lenient: 是否启用"串内未转义双引号"的宽松修复。
                 仅在 strict 解析失败后启用，绝不影响合法 JSON。
    """
    # 判断某个字符是否表示"字符串到此结束"的结构符
    DELIM = set(",:]}\n")

    out = []
    in_str = False
    esc = False

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if esc:
            out.append(ch)
            esc = False
            i += 1
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            esc = True
            i += 1
            continue

        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
                i += 1
                continue

            # 已在字符串内：需要判断这个引号是"结束"还是"内容"
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            nxt = text[j] if j < n else ""

            if nxt in DELIM or nxt == "":
                # 后面是结构符/结尾 -> 真正的闭合引号
                in_str = False
                out.append(ch)
            elif lenient and nxt == '"':
                # 后面还是引号 -> 本引号是内容，真正的闭合在后面
                out.append('\\"')
            elif lenient:
                # 后面是普通字符 -> 视为内容中的引号
                out.append('\\"')
            else:
                out.append(ch)
            i += 1
            continue

        if in_str:
            # 字符串内部的裸控制字符必须转义，否则 json 直接报错
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 0x20:
                out.append(" ")
            else:
                out.append(ch)
        else:
            out.append(ch)
        i += 1

    repaired = "".join(out)
    # 去掉对象/数组末尾多余逗号： {"a":1,} -> {"a":1}
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired


def extract_json(text: str) -> Optional[Any]:
    """
    从 LLM 输出中提取 JSON 对象/数组。

    Args:
        text: LLM 原始输出

    Returns:
        解析成功的 Python 对象；彻底失败返回 None
    """
    if not text or not text.strip():
        return None

    candidate = _strip_fences(text)

    # 1) 直接尝试
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # 2) 括号配对切片
    sliced = _slice_balanced(candidate)
    if sliced:
        for cand in (sliced, _repair(sliced), _repair(sliced, lenient=True)):
            try:
                return json.loads(cand)
            except Exception:
                continue

    # 3) 兜底：贪婪正则 + 修复（保留原行为，作为最后手段）
    m = re.search(r"\{[\s\S]*\}", candidate)
    if m:
        for cand in (m.group(), _repair(m.group()), _repair(m.group(), lenient=True)):
            try:
                return json.loads(cand)
            except Exception:
                continue

    return None
