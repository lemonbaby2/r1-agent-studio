# REST API 一览

基址：`http://127.0.0.1:18600/`（可用 `R1_PORT` 改）。所有响应为 JSON（静态资源除外），UTF-8。

> 2026-11-12 在模拟全新用户环境下实测 **13/13 端点返回 200**，见 [启动核验报告](启动核验报告.md)。

## 静态资源（GET）

| 路径 | 说明 |
|---|---|
| `/` · `/index.html` | 单页应用入口 |
| `/app.css` · `/app.js` | 样式与逻辑 |
| `/favicon.ico` | 图标（未单独提供时回落首页，不影响使用） |

## 状态与配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活探针，返回 `{"ok":true,...}` |
| GET | `/api/state` | 版本、当前配置、模型列表、会话数、主机标识（开源版固定 `local`） |
| POST | `/api/config` | 更新配置（模型 / 温度 / `max_steps` / `cwd` / `system_note` / 窗口尺寸等） |
| POST | `/api/route/refresh` | 强制重新并行探测所有模型链路，返回选路结果 |
| POST | `/api/models` | 列出当前链路可用模型（含能力标签、是否推理型） |
| POST | `/api/selftest` | 链路 + 模型 + 工具三项自检，返回逐项明细与总耗时 |

`/api/selftest` 返回示例（未装 Ollama 时的**真实**结果）：

```json
{
  "ok": false,
  "items": [
    {"k": "链路探测", "ok": false, "v": "- - (Nonems)"},
    {"k": "  ↳ 本机 Ollama", "ok": false, "v": "2018ms"},
    {"k": "总耗时", "ok": false, "v": "2.0s · 无可用链路"}
  ]
}
```

## 会话

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sessions` | 会话列表（含消息、思考链、工具轨迹） |
| POST | `/api/sessions` | 新建 / 更新 / 删除会话 |

## 对话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 发起一轮 Agent 对话；body 含 `text` / `session_id` / `images` 等 |
| POST | `/api/stop` | 中止当前生成（置 stop flag） |

## 文件与工具（本地）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/fs?path=<dir>` | 列目录（受工作目录约束） |
| GET | `/api/file?path=<file>` | 读文件内容 |

> 文件的**写/执行**不走独立 HTTP 端点，而是由 Agent 通过工具调用在受护栏的环境里完成，
> 全过程记录在会话的 `steps` 里，便于复核。

## 配置注入（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `R1_HOST` | `127.0.0.1` | 监听地址 |
| `R1_PORT` | `18600` | 监听端口 |
| `R1_NO_BROWSER` | 自动打开 | `1` = 不打开浏览器 |
| `R1_GW_TOKEN` | 空 | 远程网关 Bearer Token |
| `R1_ROUTES` | 空 | 链路 JSON 数组（内联） |
| `R1_ROUTES_FILE` | `config/routes.json` | 链路 JSON 文件路径 |

## 调用示例

```bash
# 存活
curl -s http://127.0.0.1:18600/api/health

# 状态
curl -s http://127.0.0.1:18600/api/state

# 强制重选链路
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{}' http://127.0.0.1:18600/api/route/refresh

# 三项自检
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{}' http://127.0.0.1:18600/api/selftest
```

## 安全提示

服务默认只监听回环地址。**不要**把 `R1_HOST` 设成 `0.0.0.0` 后直接暴露到公网：
API 没有认证，`/api/chat` 与文件接口具备当前用户权限。远程访问请自行加反向代理 + 认证 + TLS。
