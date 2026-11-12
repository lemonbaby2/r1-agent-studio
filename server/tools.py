# -*- coding: utf-8 -*-
"""Agent 可用工具（每一条都会在界面上作为「执行步骤」展示）。

设计原则：
  1. 每个工具都返回 {ok, summary, output}，summary 一行给卡片标题，output 全文给折叠区；
  2. 命令执行有硬黑名单（只匹配真正的破坏性写法，避免误杀 --format=csv 这类正常参数）；
  3. 输出统一截断到 6000 字符，防止一次 cat 大文件把上下文撑爆。
"""
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

MAX_OUT = 6000
MAX_READ = 24000

# 破坏性命令黑名单：只匹配真实危险写法
_DANGER = [
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bdiskpart\b", "磁盘分区工具"),
    (r"\bmkfs\.", "格式化文件系统"),
    (r"\bdd\s+if=.*of=/dev/", "裸设备写入"),
    (r"\bshutdown\b", "关机/重启"),
    (r"\bremove-item\b.*-recurse.*(?:c:\\|windows)", "递归删系统目录"),
    (r"\brm\s+-rf\s+/(\s|$)", "删根目录"),
    (r"\bdel\s+/s\s+/q\s+[a-z]:\\\\?(?:\s|$)", "整盘删除"),
    (r"\brd\s+/s\s+/q\s+[a-z]:\\\\?(?:\s|$)", "整盘删除"),
    (r"\bbcdedit\b", "改启动项"),
    (r"\bvssadmin\b.*delete", "删卷影副本"),
    (r"\bcipher\s+/w", "擦除磁盘空间"),
]
_DANGER_RE = [(re.compile(p, re.I), why) for p, why in _DANGER]


def guard(cmd):
    """返回 None 表示放行，否则返回拒绝原因。"""
    for rx, why in _DANGER_RE:
        if rx.search(cmd or ""):
            return why
    return None


def _trunc(s, n=MAX_OUT):
    s = s or ""
    if len(s) <= n:
        return s
    return s[:n] + "\n…（已截断，共 %d 字符）" % len(s)


def _run(args, cwd=None, timeout=120, shell=False, input_text=None):
    """统一子进程执行：GBK 优先，失败回落 utf-8。"""
    t0 = time.time()
    try:
        p = subprocess.run(
            args, cwd=cwd, shell=shell, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            input=input_text.encode("gbk", "replace") if input_text else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        def dec(b):
            if not b:
                return ""
            for enc in ("gbk", "utf-8"):
                try:
                    return b.decode(enc)
                except Exception:
                    continue
            return b.decode("utf-8", "replace")
        out, err = dec(p.stdout), dec(p.stderr)
        return p.returncode, out, err, int((time.time() - t0) * 1000)
    except subprocess.TimeoutExpired:
        return -9, "", "命令超时（%ds）" % timeout, int((time.time() - t0) * 1000)
    except FileNotFoundError as e:
        return -1, "", "找不到可执行文件：%s" % e, int((time.time() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        return -1, "", "%s: %s" % (type(e).__name__, e), int((time.time() - t0) * 1000)


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------
def t_list_dir(path=".", show_hidden=False):
    path = os.path.abspath(os.path.expanduser(path or "."))
    if not os.path.isdir(path):
        return {"ok": False, "summary": "目录不存在：%s" % path, "output": ""}
    rows = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if not show_hidden and e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        rows.append({"name": e.name, "dir": True, "size": 0})
                    else:
                        rows.append({"name": e.name, "dir": False,
                                     "size": e.stat().st_size})
                except Exception:
                    rows.append({"name": e.name, "dir": False, "size": 0})
    except PermissionError:
        return {"ok": False, "summary": "无权限读取：%s" % path, "output": ""}

    rows.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    lines = []
    for r in rows[:400]:
        if r["dir"]:
            lines.append("  <DIR>  " + r["name"])
        else:
            lines.append("%8s  %s" % (_human(r["size"]), r["name"]))
    if len(rows) > 400:
        lines.append("… 共 %d 项，只列前 400" % len(rows))
    return {"ok": True,
            "summary": "%s 下 %d 项" % (path, len(rows)),
            "output": "\n".join(lines) or "（空目录）"}


def _human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f%s" % (n, u)) if u == "B" else ("%.1f%s" % (n, u))
        n /= 1024.0
    return str(n)


def t_read_file(path, start=1, lines=200):
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(p):
        return {"ok": False, "summary": "文件不存在：%s" % p, "output": ""}
    size = os.path.getsize(p)
    if size > 8 * 1024 * 1024:
        return {"ok": False, "summary": "文件过大（%.1f MB），请改用 run_cmd 分段读" % (size / 1e6), "output": ""}
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            text = None
    if text is None:
        return {"ok": False, "summary": "二进制或未知编码文件：%s" % p, "output": ""}
    all_lines = text.splitlines()
    start = max(1, int(start or 1))
    lines = max(1, min(int(lines or 200), 2000))
    seg = all_lines[start - 1:start - 1 + lines]
    body = "\n".join("%5d| %s" % (start + i, ln) for i, ln in enumerate(seg))
    return {"ok": True,
            "summary": "%s 共 %d 行，显示 %d-%d" % (os.path.basename(p), len(all_lines),
                                               start, start + len(seg) - 1),
            "output": _trunc(body or "（空文件）", MAX_READ)}


def t_write_file(path, content=""):
    p = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    old = ""
    if os.path.isfile(p):
        try:
            old = open(p, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            old = ""
        try:
            open(p + ".bak", "w", encoding="utf-8").write(old)
        except Exception:
            pass
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(content or "")
    return {"ok": True,
            "summary": "已写入 %s（%d 字符%s）" % (os.path.basename(p), len(content or ""),
                                            "，原文件备份为 .bak" if old else ""),
            "output": "路径：%s\n大小：%d 字节" % (p, os.path.getsize(p))}


def t_run_cmd(cmd, cwd=None, timeout=120):
    why = guard(cmd)
    if why:
        return {"ok": False, "summary": "已拦截：%s" % why,
                "output": "命令被安全策略拒绝（%s）：\n%s" % (why, cmd)}
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": False, "summary": "空命令", "output": ""}
    wd = os.path.abspath(os.path.expanduser(cwd)) if cwd else os.getcwd()
    if not os.path.isdir(wd):
        wd = os.getcwd()
    if os.name == "nt":
        rc, out, err, ms = _run(["cmd", "/c", cmd], cwd=wd, timeout=int(timeout or 120))
    else:
        rc, out, err, ms = _run(["/bin/bash", "-lc", cmd], cwd=wd, timeout=int(timeout or 120))
    body = ""
    if out.strip():
        body += out.rstrip() + "\n"
    if err.strip():
        body += ("\n[stderr]\n" if body else "") + err.rstrip() + "\n"
    body += "\n[退出码 %d · 耗时 %d ms · 工作目录 %s]" % (rc, ms, wd)
    return {"ok": rc == 0, "summary": "退出码 %d · %d ms" % (rc, ms),
            "output": _trunc(body)}


def _repl_tail(code):
    """模型常按 REPL 习惯写：最后一行只写一个表达式（如 `total_lines`），
    指望解释器自动回显。当脚本跑就没有回显，结果全丢——实测出现
    「统计行数」返回空、答案变成 0 行。这里把末尾表达式改写成 print(...)。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    if not tree.body:
        return code
    last = tree.body[-1]
    if not isinstance(last, ast.Expr):
        return code
    src = ast.get_source_segment(code, last)
    if not src or src.lstrip().startswith(("print(", "print (", "sys.stdout")):
        return code
    lines = code.splitlines()
    start, end = last.lineno - 1, last.end_lineno   # end 为 1-based 闭区间
    indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
    wrapped = indent + "print(%s)" % src.strip()
    return "\n".join(lines[:start] + [wrapped] + lines[end:]) + "\n"


def t_run_python(code, cwd=None, timeout=120):
    """执行一段 Python：写临时文件 → 用当前解释器跑 → 回显源码与输出。"""
    code = code or ""
    if not code.strip():
        return {"ok": False, "summary": "空代码", "output": ""}
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="r1step_")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(_repl_tail(code))
    try:
        py = sys.executable
        # 打包成 exe 时 sys.executable 是 exe 本身 → 走 --runpy 自举
        args = [py, "--runpy", tmp] if getattr(sys, "frozen", False) else [py, tmp]
        wd = os.path.abspath(os.path.expanduser(cwd)) if cwd else os.getcwd()
        rc, out, err, ms = _run(args, cwd=wd, timeout=int(timeout or 120))
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    body = "```python\n" + code.strip()[:2000] + "\n```\n\n"
    if out.strip():
        body += out.rstrip() + "\n"
    if err.strip():
        body += ("\n[stderr]\n" if out.strip() else "") + err.rstrip() + "\n"
    body += "\n[退出码 %d · 耗时 %d ms]" % (rc, ms)
    return {"ok": rc == 0, "summary": "Python 退出码 %d · %d ms" % (rc, ms),
            "output": _trunc(body)}


def t_search_text(pattern, path=".", glob="*", limit=60):
    p = os.path.abspath(os.path.expanduser(path or "."))
    if not os.path.isdir(p):
        return {"ok": False, "summary": "目录不存在：%s" % p, "output": ""}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "summary": "正则不合法：%s" % e, "output": ""}
    hits, scanned = [], 0
    skip = {"node_modules", ".git", "__pycache__", ".venv", "venv",
            "dist", "build", ".idea", ".vs"}
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d.lower() not in skip]
        for fn in files:
            if not fnmatch.fnmatch(fn, glob or "*"):
                continue
            fp = os.path.join(root, fn)
            try:
                if os.path.getsize(fp) > 2 * 1024 * 1024:
                    continue
                scanned += 1
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append("%s:%d: %s" % (fp, i, line.strip()[:200]))
                            if len(hits) >= limit:
                                raise StopIteration
            except StopIteration:
                break
            except Exception:
                continue
        if len(hits) >= limit:
            break
    return {"ok": True,
            "summary": "命中 %d 处（扫描 %d 个文件）" % (len(hits), scanned),
            "output": _trunc("\n".join(hits) or "（无命中）")}


def t_fetch_url(url, max_chars=4000):
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "summary": "只支持 http/https", "output": ""}
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dgx-r1-studio/1.0"})
        with op.open(req, timeout=20) as r:
            raw = r.read(600000)
            ctype = r.headers.get("Content-Type", "")
        text = raw.decode("utf-8", "ignore")
        if "html" in ctype.lower() or text.lstrip().startswith("<"):
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n", text)
        n = max(500, min(int(max_chars or 4000), 12000))
        return {"ok": True, "summary": "抓取 %d 字符（%s）" % (len(text), ctype[:40]),
                "output": _trunc(text.strip(), n)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "summary": "抓取失败：%s" % type(e).__name__,
                "output": str(e)[:400]}


# --------------------------------------------------------------------------
# 工具表（同时用于生成系统提示里的工具说明）
# --------------------------------------------------------------------------
TOOLS = {
    "list_dir": {
        "fn": t_list_dir,
        "desc": "列出目录内容（文件/子目录/大小）",
        "args": {"path": "目录绝对路径"},
    },
    "read_file": {
        "fn": t_read_file,
        "desc": "按行读取文本文件",
        "args": {"path": "文件路径", "start": "起始行，默认1", "lines": "行数，默认200"},
    },
    "write_file": {
        "fn": t_write_file,
        "desc": "写文件（会自动备份 .bak）",
        "args": {"path": "文件路径", "content": "完整内容"},
    },
    "run_cmd": {
        "fn": t_run_cmd,
        "desc": "执行一条 Windows 命令（cmd /c），返回 stdout/stderr/退出码",
        "args": {"cmd": "命令行", "cwd": "工作目录（可选）", "timeout": "超时秒数，默认120"},
    },
    "run_python": {
        "fn": t_run_python,
        "desc": "执行一段 Python 代码并返回输出（适合算数、处理文件、调接口）",
        "args": {"code": "完整 Python 源码", "cwd": "工作目录（可选）", "timeout": "超时秒数"},
    },
    "search_text": {
        "fn": t_search_text,
        "desc": "在目录里按正则搜索文件内容（类似 grep）",
        "args": {"pattern": "正则", "path": "目录", "glob": "文件名通配，默认*", "limit": "条数"},
    },
    "fetch_url": {
        "fn": t_fetch_url,
        "desc": "抓取网页或接口内容并转纯文本",
        "args": {"url": "http/https 地址", "max_chars": "最大字符数"},
    },
}


def tool_help():
    """生成给模型看的工具清单文本。"""
    lines = []
    for name, t in TOOLS.items():
        args = ", ".join("%s(%s)" % (k, v) for k, v in t["args"].items())
        lines.append("- %s : %s —— 参数 %s" % (name, t["desc"], args))
    return "\n".join(lines)


def _sch(name, desc, props, required):
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


def schema():
    """Ollama 原生 tool calling 用的 JSON Schema（比文本协议稳得多）。"""
    S = {"type": "string"}
    I = {"type": "integer"}
    return [
        _sch("list_dir", "列出目录内容，返回文件名、类型、大小。不确定路径时先用它。",
             {"path": dict(S, description="目录绝对路径")}, ["path"]),
        _sch("read_file", "按行读取文本文件内容。",
             {"path": dict(S, description="文件绝对路径"),
              "start": dict(I, description="起始行号，从 1 开始"),
              "lines": dict(I, description="读取行数，默认 200")}, ["path"]),
        _sch("write_file", "把内容写入文件（已存在则先备份 .bak）。",
             {"path": dict(S, description="文件绝对路径"),
              "content": dict(S, description="完整文件内容")}, ["path", "content"]),
        _sch("run_cmd", "执行一条 Windows 命令（cmd /c），返回 stdout/stderr 和退出码。",
             {"cmd": dict(S, description="命令行，例如 dir 或 ipconfig"),
              "cwd": dict(S, description="工作目录，可省略"),
              "timeout": dict(I, description="超时秒数，默认 120")}, ["cmd"]),
        _sch("run_python", "执行一段 Python 代码并返回输出。适合计算、处理文件、调接口。",
             {"code": dict(S, description="完整可运行的 Python 源码"),
              "cwd": dict(S, description="工作目录，可省略"),
              "timeout": dict(I, description="超时秒数，默认 120")}, ["code"]),
        _sch("search_text", "在目录里按正则搜索文件内容（类似 grep）。",
             {"pattern": dict(S, description="正则表达式"),
              "path": dict(S, description="搜索目录"),
              "glob": dict(S, description="文件名通配，如 *.py"),
              "limit": dict(I, description="最多返回条数")}, ["pattern", "path"]),
        _sch("fetch_url", "抓取网页或接口内容，自动转纯文本。",
             {"url": dict(S, description="http/https 地址"),
              "max_chars": dict(I, description="最多返回字符数")}, ["url"]),
    ]


def call(name, args):
    t = TOOLS.get(name)
    if not t:
        return {"ok": False, "summary": "未知工具：%s" % name,
                "output": "可用工具：%s" % ", ".join(TOOLS)}
    args = args or {}
    try:
        return t["fn"](**{k: v for k, v in args.items()
                          if k in t["args"]})
    except TypeError as e:
        return {"ok": False, "summary": "参数不匹配", "output": str(e)[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "summary": "工具异常：%s" % type(e).__name__,
                "output": str(e)[:500]}
