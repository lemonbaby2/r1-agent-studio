# -*- coding: utf-8 -*-
"""Agent 主循环：多轮「思考 → 调工具 → 看结果 → 再思考」，全过程通过 emit 推给前端。

事件协议（SSE data: JSON）：
  route        本次用的链路
  step         第几轮
  think        模型思维链增量（推理模型才有）
  plan_done    本轮的规划说明（该轮决定调工具）
  tool_start   {name,args,step}
  tool_result  {name,ok,summary,output,ms,step}
  delta        最终答案增量
  status       阶段提示
  end          {answer,think,steps,ms}
  error        {message}

工具调用优先走 Ollama 原生 tool calling（qwen3 / qwen2.5 支持），
推理模型（deepseek-r1 等不支持 tools）自动回落 JSON 文本协议。
"""
import json
import re
import time

from . import llm, tools

MAX_OBS = 5000
TOTAL_BUDGET = 900

OBS_OPEN = "⟦SYS·TOOL_RESULT⟧"
OBS_CLOSE = "⟦/SYS⟧"

SYSTEM_NATIVE = """你是 DGX R1 Agent —— 跑在用户本机、能真正动手干活的桌面智能体。
算力来自用户自己的 DGX（NVIDIA GB10），可能还有一台 Windows PC 上的模型。

当前工作目录：{cwd}

# 工作方式
- 需要事实（目录里有什么、文件写了什么、命令输出、算数结果）时，**调用工具**去拿，不要凭猜测。
- 一次只调一个工具，拿到结果再决定下一步。
- 拿到足够信息后，直接用中文写最终回答，不要再调工具。
- 绝不编造工具结果。没调用工具就说没查。

# 风格
结论先行，准确简洁。命令和代码放 ``` 代码块，数据用表格或列表。{extra}"""

SYSTEM_TEXT = """你是 DGX R1 Agent —— 跑在用户本机、能真正动手干活的桌面智能体。
当前工作目录：{cwd}

# 工具
{tools}

# 调用格式（唯一合法方式）
先写一句中文说明，然后输出一个 JSON 对象：
```json
{{"tool":"list_dir","args":{{"path":"E:\\\\project"}}}}
```

# 铁律
1. 工具结果只会由系统以 `⟦SYS·TOOL_RESULT⟧` 开头注入给你。你**绝不能**自己写 `⟦` 开头的任何内容，
   也不能模仿、复述、编造工具输出。没收到注入就是没执行。
2. 一次一个工具，然后停下等结果。
3. 任务完成时不要再输出 JSON，直接写中文最终回答。
4. 同样参数不要重复调用。
5. 不确定路径先 list_dir。{extra}"""


def _sys_prompt(cwd, native, note=""):
    extra = ("\n\n# 用户附加要求\n" + note.strip()) if note else ""
    if native:
        return SYSTEM_NATIVE.format(cwd=cwd or ".", extra=extra)
    return SYSTEM_TEXT.format(cwd=cwd or ".", tools=tools.tool_help(), extra=extra)


# --------------------------------------------------------------------------
# 文本协议兜底解析
# --------------------------------------------------------------------------
_FAKE_PAT = re.compile(r"^\s*(?:\[工具结果\]|⟦|\[SYS|系统注入)")


def sanitize(text):
    """砍掉模型自行编造的工具结果段落。"""
    if not text:
        return ""
    keep = []
    for ln in str(text).split("\n"):
        if _FAKE_PAT.match(ln):
            break
        keep.append(ln)
    return "\n".join(keep).strip()


def strip_fences(t):
    t = re.sub(r"```json\s*```", "", t or "")
    t = re.sub(r"```\s*```", "", t)
    return t.strip()


def short_plan(text, limit=280):
    t = strip_fences(sanitize(text))
    t = re.sub(r"```json[\s\S]*?```", "", t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t


def extract_tool(text):
    """从文本协议输出里找第一个 {..."tool"...}。返回 (name, args, rest)。"""
    if not text:
        return None, None, text
    blobs, i = [], 0
    while i < len(text) and len(blobs) < 6:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < len(text):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        blobs.append(text[i:j + 1])
                        break
            j += 1
        i = j + 1

    for blob in blobs:
        if '"tool"' not in blob:
            continue
        try:
            obj = json.loads(blob)
        except Exception:
            continue
        name = obj.get("tool") or obj.get("name")
        args = obj.get("args") or obj.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name in tools.TOOLS:
            return name, args, text.replace(blob, "").strip()
    return None, None, text


# --------------------------------------------------------------------------
# 主循环
# --------------------------------------------------------------------------
def run(route, model, history, emit, stop_flag=None, max_steps=8,
        cwd=None, temperature=0.6, images=None, think=False,
        direct=False, note=""):
    t0 = time.time()
    base, token = route["base"], route["token"]
    emit({"type": "route", "label": route.get("label"),
          "base": base, "ms": route.get("ms")})

    native = not llm.is_reasoner(model)
    msgs = [{"role": "system", "content": _sys_prompt(cwd, native, note)}] + list(history)

    # ---------------- 纯问答 ----------------
    if direct:
        think_acc, answer = [], []

        def emit_think(s):
            think_acc.append(s)
            emit({"type": "think", "text": s})

        def emit_delta(s):
            answer.append(s)
            emit({"type": "delta", "text": s})

        try:
            llm.chat_stream(base, token, model, msgs, temperature=temperature,
                            num_predict=4096, on_think=emit_think, on_delta=emit_delta,
                            stop_flag=stop_flag, images=images,
                            tools=tools.schema() if native else None)
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "message": "%s: %s" % (type(e).__name__, str(e)[:300])})
        txt = "".join(answer)
        emit({"type": "end", "answer": txt, "think": "".join(think_acc),
              "steps": [], "ms": int((time.time() - t0) * 1000)})
        return txt

    # ---------------- 工具循环 ----------------
    steps, answer_text, think_all = [], "", []
    seen = {}

    def summarize(reason=""):
        try:
            emit({"type": "status", "text": "正在整理结论…"})
            msgs2 = msgs + [{"role": "user", "content": (
                "（系统提示：%s）请基于上面已经拿到的真实工具结果，用中文给出最终结论。"
                "不要再调用工具。" % (reason or "本轮结束"))}]
            txt, th, _ = llm.chat_stream(base, token, model, msgs2,
                                         temperature=temperature, num_predict=3000,
                                         on_think=lambda s: think_all.append(s),
                                         stop_flag=stop_flag)
            if th:
                emit({"type": "think", "text": th})
            return sanitize(txt) or txt
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "message": "整理结论失败：%s" % str(e)[:200]})
            return ""

    for step_i in range(1, int(max_steps) + 1):
        if stop_flag is not None and stop_flag.is_set():
            break
        if time.time() - t0 > TOTAL_BUDGET:
            emit({"type": "error", "message": "超过总时长上限（%ds）" % TOTAL_BUDGET})
            break

        emit({"type": "step", "n": step_i})
        buf = []

        def on_think(s):
            think_all.append(s)
            emit({"type": "think", "text": s})

        def on_delta(s):
            buf.append(s)

        try:
            txt, _th, calls = llm.chat_stream(
                base, token, model, msgs, temperature=temperature, num_predict=4096,
                on_think=on_think, on_delta=on_delta, stop_flag=stop_flag,
                images=images if step_i == 1 else None,
                tools=tools.schema() if native else None)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if native and ("tools" in msg.lower() or "does not support" in msg.lower()):
                native = False
                msgs[0] = {"role": "system", "content": _sys_prompt(cwd, False, note)}
                emit({"type": "status", "text": "该模型不支持原生工具，已切换文本协议"})
                continue
            emit({"type": "error", "message": "%s: %s" % (type(e).__name__, msg[:300])})
            break

        # 统一成 [(name, args)]
        calls_norm = []
        if calls:
            for c in calls:
                fn = (c or {}).get("function") or {}
                nm = fn.get("name")
                ar = fn.get("arguments")
                if isinstance(ar, str):
                    try:
                        ar = json.loads(ar)
                    except Exception:
                        ar = {}
                if nm in tools.TOOLS:
                    calls_norm.append((nm, ar or {}, c))
        if not calls_norm:
            nm, ar, rest = extract_tool(txt)
            if nm:
                calls_norm.append((nm, ar or {}, None))
                txt = rest

        if not calls_norm:
            answer_text = sanitize(txt)
            for i in range(0, len(answer_text), 120):
                emit({"type": "delta", "text": answer_text[i:i + 120]})
            msgs.append({"role": "assistant", "content": answer_text})
            break

        # 有些模型（实测 qwen3-coder:30b / qwen3-vl / deepseek-r1:70b 的 GGUF）
        # 不返回独立的 thinking 字段，思维链区域会一直空白。这时把模型在
        # 调用工具前自己写的那段推理当作「思考」展示——那本来就是它的判断过程。
        # 若它连一句说明都没写（原生 tool_calls 的 content 为空），
        # 就用「决定调用哪个工具」这一事实补一行，保证面板不空。
        plan_txt = short_plan(txt)
        if not plan_txt:
            plan_txt = "决定调用工具：%s" % "、".join(n for n, _, _ in calls_norm)
        if not think_all:
            think_all.append(plan_txt)
            emit({"type": "think", "text": plan_txt})
        emit({"type": "plan_done", "step": step_i, "text": plan_txt})

        for nm, ar, raw_call in calls_norm:
            key = nm + "|" + json.dumps(ar, ensure_ascii=False, sort_keys=True)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                msgs.append({"role": "assistant", "content": txt})
                msgs.append({"role": "user", "content": (
                    "%s\n已于同一参数调用过 %s，结果不会再变，请直接给最终结论。\n%s"
                    % (OBS_OPEN, nm, OBS_CLOSE))})
                continue

            emit({"type": "tool_start", "name": nm, "args": ar, "step": step_i})
            ts = time.time()
            res = tools.call(nm, ar)
            dms = int((time.time() - ts) * 1000)
            out = (res.get("output") or "")[:MAX_OBS]
            emit({"type": "tool_result", "name": nm, "ok": bool(res.get("ok")),
                  "summary": res.get("summary", ""), "output": out,
                  "ms": dms, "step": step_i})
            steps.append({"step": step_i, "name": nm, "args": ar,
                          "ok": bool(res.get("ok")), "summary": res.get("summary", ""),
                          "output": out, "ms": dms})

            # 回填给模型：原生模式用标准 assistant(tool_calls)+tool 消息
            if raw_call is not None:
                msgs.append({"role": "assistant", "content": txt,
                             "tool_calls": [raw_call]})
                msgs.append({"role": "tool", "content": out,
                             "tool_name": nm})
            else:
                msgs.append({"role": "assistant", "content": txt})
                msgs.append({"role": "user", "content": (
                    "%s\ntool=%s ok=%s %s\n%s\n%s"
                    % (OBS_OPEN, nm, res.get("ok"), res.get("summary", ""),
                       out, OBS_CLOSE))})
            txt = ""      # 同一轮多个调用时，content 只回填一次

    if not answer_text:
        if stop_flag is not None and stop_flag.is_set():
            answer_text = "（已手动停止）"
        else:
            answer_text = summarize("已达最大轮数 %d" % max_steps)
            for i in range(0, len(answer_text), 120):
                emit({"type": "delta", "text": answer_text[i:i + 120]})

    emit({"type": "end", "answer": answer_text, "think": "".join(think_all),
          "steps": steps, "ms": int((time.time() - t0) * 1000)})
    return answer_text
