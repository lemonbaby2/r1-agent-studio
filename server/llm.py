# -*- coding: utf-8 -*-
"""大模型客户端 + 多链路自动选路（开源脱敏版）。

设计原则
--------
1. **默认只连本机**：开箱即用时仅探测 ``http://127.0.0.1:11434``（Ollama 默认端口），
   不内置任何内网地址、公网域名、令牌或账号。
2. **配置注入**：需要接入远程网关时，用环境变量 ``R1_ROUTES``（JSON 字符串）
   或 ``config/routes.json`` 提供链路；令牌用 ``R1_GW_TOKEN`` 注入。
   配置文件已加入 .gitignore，绝不入库。
3. **并行探测、择快而用**：所有链路同时探测 /api/tags，选响应最快的可达链路；
   上一轮选中的链路优先复用，避免每次都重新选路。

链路配置格式（JSON 数组）::

    [
      {"id": "local", "label": "本机 Ollama",
       "base": "http://127.0.0.1:11434", "auth": false},
      {"id": "remote", "label": "我的网关",
       "base": "https://your-gateway.example.com/node/dgx", "auth": true}
    ]

``auth: true`` 的链路会带 ``Authorization: Bearer $R1_GW_TOKEN``。
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# 常量：全部来自环境变量，无任何内置凭据
# --------------------------------------------------------------------------
GW_TOKEN = os.environ.get("R1_GW_TOKEN", "").strip()

DEFAULT_ROUTES = [
    {"id": "local", "label": "本机 Ollama",
     "base": "http://127.0.0.1:11434", "auth": False},
]

_CONFIG_CANDIDATES = [
    os.environ.get("R1_ROUTES_FILE", ""),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "config", "routes.json"),
]

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_UA = {"User-Agent": "r1-agent-studio/1.1", "Accept": "application/json"}


def _load_routes():
    """按优先级加载链路：环境变量 R1_ROUTES > 配置文件 > 本机默认。"""
    raw = os.environ.get("R1_ROUTES", "").strip()
    if not raw:
        for p in _CONFIG_CANDIDATES:
            if not p or not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = f.read()
                break
            except OSError:
                continue
    if not raw:
        return [dict(x) for x in DEFAULT_ROUTES]
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("routes must be a JSON array")
        out = []
        for it in data:
            base = str(it.get("base", "")).strip()
            if not base:
                continue
            out.append({
                "id": str(it.get("id", "custom")),
                "label": str(it.get("label", "自定义链路")),
                "base": base.rstrip("/"),
                "auth": bool(it.get("auth", False)),
            })
        return out or [dict(x) for x in DEFAULT_ROUTES]
    except (ValueError, TypeError):
        return [dict(x) for x in DEFAULT_ROUTES]


ROUTES = _load_routes()


def opener():
    return _OPENER


def headers(token=None, extra=None):
    h = dict(_UA)
    if token:
        h["Authorization"] = "Bearer " + token
    if extra:
        h.update(extra)
    return h


# --------------------------------------------------------------------------
# 链路探测
# --------------------------------------------------------------------------
def probe(base, token, timeout=2.5):
    """GET {base}/api/tags，返回 (ok, ms)。不做异常外抛。"""
    t0 = time.time()
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/tags",
                                     headers=headers(token))
        with _OPENER.open(req, timeout=timeout) as r:
            r.read(64)
            ok = 200 <= r.status < 300
        return ok, int((time.time() - t0) * 1000)
    except Exception:
        return False, int((time.time() - t0) * 1000)


def auto_route(force=False, saved=None):
    """并行探所有链路，返回 {base, token, label, ms, candidates, at}。

    saved：上次选中的 base，命中且探测通过时优先复用。
    """
    out = {"base": None, "token": None, "label": None, "ms": None,
           "candidates": [], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    lock = threading.Lock()
    results = []

    def work(item):
        tok = GW_TOKEN if item.get("auth") else None
        ok, ms = probe(item["base"], tok, 2.5)
        with lock:
            results.append({"id": item["id"], "label": item["label"],
                            "base": item["base"], "token": tok,
                            "ok": ok, "ms": ms})

    ths = [threading.Thread(target=work, args=(it,), daemon=True)
           for it in ROUTES]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=6)

    results.sort(key=lambda x: (not x["ok"], x["ms"] if x["ok"] else 0))
    out["candidates"] = results

    pick = None
    if saved:
        for r in results:
            if r["ok"] and r["base"].rstrip("/") == saved.rstrip("/"):
                pick = r
                break
    if pick is None:
        pick = next((r for r in results if r["ok"]), None)

    if pick:
        out.update(base=pick["base"], token=pick["token"],
                   label=pick["label"], ms=pick["ms"])
    return out


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
def list_models(base, token, timeout=8):
    req = urllib.request.Request(base.rstrip("/") + "/api/tags",
                                 headers=headers(token))
    with _OPENER.open(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    return d.get("models") or []


def caps_of(name):
    n = (name or "").lower()
    tags = []
    if "vl" in n or "vision" in n or "llava" in n or "minicpm-v" in n:
        tags.append("视觉")
    if "r1" in n or "think" in n or "qwq" in n or "reason" in n:
        tags.append("推理")
    if "coder" in n or "code" in n:
        tags.append("代码")
    return tags


def is_reasoner(name):
    n = (name or "").lower()
    return any(k in n for k in ("r1", "qwq", "reason", "think"))


# --------------------------------------------------------------------------
# 流式对话（Ollama 原生 /api/chat，NDJSON）
# --------------------------------------------------------------------------
def chat_stream(base, token, model, messages, temperature=0.6,
                num_predict=4096, on_think=None, on_delta=None,
                stop_flag=None, timeout=600, images=None, tools=None):
    """流式生成。返回 (正文, 思维链, 原生工具调用列表)。

    images：base64 字符串列表（仅视觉模型），会附加到最后一条 user 消息。
    tools：Ollama 原生工具 schema 列表；模型支持时会返回 message.tool_calls。
    """
    url = base.rstrip("/") + "/api/chat"
    msgs = [dict(m) for m in messages]
    if images:
        if msgs and msgs[-1].get("role") == "user":
            msgs[-1]["images"] = list(images)
        else:
            msgs.append({"role": "user", "content": "", "images": list(images)})

    payload = {
        "model": model,
        "messages": msgs,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": int(num_predict)},
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers(token, {"Content-Type": "application/json"}))
    out, think, calls = [], [], []
    with _OPENER.open(req, timeout=timeout) as r:
        for raw in r:
            if stop_flag is not None and stop_flag.is_set():
                break
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("error"):
                raise RuntimeError(str(obj["error"])[:300])
            msg = obj.get("message") or {}
            th = msg.get("thinking")
            ct = msg.get("content")
            tcs = msg.get("tool_calls")
            if th:
                think.append(th)
                if on_think:
                    on_think(th)
            if ct:
                out.append(ct)
                if on_delta:
                    on_delta(ct)
            if tcs:
                calls.extend(tcs)
            if obj.get("done"):
                break
    return "".join(out), "".join(think), calls


def chat_once(base, token, model, messages, temperature=0.3, num_predict=2048,
              timeout=600, images=None):
    txt, _, _ = chat_stream(base, token, model, messages, temperature=temperature,
                            num_predict=num_predict, timeout=timeout, images=images)
    return txt
