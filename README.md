# DeepSeek2API

简体中文 | [English](README-en.md)

通过 Playwright 自动化 DeepSeek 官网聊天页，提供兼容 OpenAI Chat Completions 格式的本地 API 服务。

## 功能

- 提供 `POST /v1/chat/completions` 接口
- 兼容 OpenAI 风格请求体（`model`、`messages`、`stream`）
- 支持流式与非流式输出
- 自动切换网页端模型、深度思考与智能搜索开关

## 环境要求

- Python 3.10+
- DeepSeek 账号 Cookie 与 userToken

## 安装

```bash
# 安装依赖（推荐使用 uv）
uv sync

# 安装 Playwright Chromium
playwright install chromium
```

## 配置

在项目根目录创建 `.env` 文件：

```env
cookie=填入你的 ds_cookie_preference
userToken=填入你 localStorage 中的 userToken

# 可选：自定义端口，默认 8000
PORT=8000

# 可选：浏览器可视化模式
# 当设置为 0、false、no、off、n、f 等（不区分大小写）时，
# 浏览器将以可视化模式启动（headless=False），方便首次登录或调试
HEADLESS=false
```

### 凭证获取方式

1. 使用浏览器登录 [chat.deepseek.com](https://chat.deepseek.com)
2. 打开开发者工具（F12）→ Application / Storage
3. 复制 `cookie` 和 `localStorage` 中的 `userToken` 中的 `value` 字段

> ⚠️ 注意：当前版本通过环境变量管理凭证，请勿将包含真实凭证的 `.env` 文件提交到仓库。

## 启动服务

```bash
uv run main.py
```

默认监听：`http://0.0.0.0:8000`

首次启动时，若未设置 `HEADLESS=false`，Playwright 将以无头模式运行，可能需要先以可视化模式启动并完成验证码。

## 接口说明

### 请求

`POST /v1/chat/completions`

示例：

```json
{
  "model": "deepseek-fast-thinking-search",
  "messages": [
    {"role": "user", "content": "你好，介绍一下你自己"}
  ],
  "stream": true
}
```

### `model` 解析规则

- 包含 `expert`：切换到专家模式
- 包含 `thinking`：开启“深度思考”
- 包含 `search`：开启“智能搜索”

例如：

- `deepseek-fast`：普通模式
- `deepseek-expert`：专家模式
- `deepseek-expert-thinking-search`：专家 + 深度思考 + 智能搜索

## 项目结构

```
.
├── main.py      # 入口文件：读取配置、组装组件、启动服务
├── api.py       # FastAPI 路由：OpenAI 兼容接口
├── browser.py   # Playwright 浏览器自动化
└── .env         # 环境变量配置
```

## 已知限制

- 使用单浏览器页串行处理请求（有请求锁）
- 页面结构变化可能导致自动化失效
- 依赖 DeepSeek 官方网页接口与登录状态

## 许可证

如仓库未单独声明，请按仓库所有者约定使用。
