# N8N 与 MCP 使用指南

## 服务信息
- 基础地址：`https://frfhwcxbyyjq.ap-southeast-1.clawcloudrun.com`
- 端点：
  - `GET /healthz` 健康检查（`douyin_mcp_server/server.py:479`）
  - `POST /video-info` 解析视频信息（`douyin_mcp_server/server.py:508`）
  - `POST /download-link` 获取无水印下载链接（`douyin_mcp_server/server.py:496`）
  - `POST /extract-text` 提取视频文本（`douyin_mcp_server/server.py:483`）
- 鉴权：所有 `POST` 请求必须携带请求头 `Authorization: Bearer <MCP_AUTH_TOKEN>`

## 环境变量建议（避免硬编码）
- `BASE_URL`：服务基础地址（例如 `https://frfhwcxbyyjq.ap-southeast-1.clawcloudrun.com`）
- `MCP_AUTH_TOKEN`：服务鉴权令牌（不要写入仓库，仅本地/CI保管）
- 在命令中统一通过环境变量注入，满足“禁止硬编码”要求。

## cURL 示例（可直接在 N8N 的 HTTP Request 节点中使用“Import cURL”）

### Linux/macOS  https://frfhwcxbyyjq.ap-southeast-1.clawcloudrun.com https://mcpdy.zeabur.app/

```bash
# 健康检查
curl -sS -X GET "https://frfhwcxbyyjq.ap-southeast-1.clawcloudrun.com/healthz"

# 解析视频基本信息
curl -sS -X POST "https://frfhwcxbyyjq.ap-southeast-1.clawcloudrun.com/video-info" \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/"}'

# 获取无水印下载链接
curl -sS -X POST "$BASE_URL/download-link" \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/"}'

# 提取视频文本（模型留空按服务端默认）
curl -sS -X POST "https://mcpdy.zeabur.app/extract-text" \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/","model":""}'
```

### Windows PowerShell

```powershell
# 请先设置环境变量：$env:BASE_URL 与 $env:MCP_AUTH_TOKEN

# 健康检查
Invoke-WebRequest -Uri "$env:BASE_URL/healthz" -UseBasicParsing | Select-Object -ExpandProperty Content

# 解析视频基本信息
$body = '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/"}'
Invoke-WebRequest -Uri "$env:BASE_URL/video-info" -Method POST `
  -Headers @{ Authorization = "Bearer $env:MCP_AUTH_TOKEN"; "Content-Type" = "application/json" } `
  -Body $body -UseBasicParsing | Select-Object -ExpandProperty Content

# 获取无水印下载链接
$body = '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/"}'
Invoke-WebRequest -Uri "$env:BASE_URL/download-link" -Method POST `
  -Headers @{ Authorization = "Bearer $env:MCP_AUTH_TOKEN"; "Content-Type" = "application/json" } `
  -Body $body -UseBasicParsing | Select-Object -ExpandProperty Content

# 提取视频文本
$body = '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/","model":""}'
Invoke-WebRequest -Uri "$env:BASE_URL/extract-text" -Method POST `
  -Headers @{ Authorization = "Bearer $env:MCP_AUTH_TOKEN"; "Content-Type" = "application/json" } `
  -Body $body -UseBasicParsing | Select-Object -ExpandProperty Content
```

## 在 N8N 中的节点配置建议
- 节点类型：`HTTP Request`
- Method：`POST`（健康检查用 `GET`）
- URL：`${BASE_URL}/video-info` 等
- Headers：
  - `Authorization: Bearer {{$env.MCP_AUTH_TOKEN}}`（推荐使用 n8n 的环境变量或 Credentials）
  - `Content-Type: application/json`
- Body：选择 `JSON`，示例：`{"share_link":"{{$json.share_link}}","model":""}`
- Response：选择 `JSON`，后续节点可直接从返回体读取 `data.download_url` 或 `text`

## MCP 使用方式（桌面客户端如 Claude Desktop）

### 安装与启动
- 安装包后会生成入口脚本：`douyin-mcp-server`
- 本地启动：`douyin-mcp-server`

### 客户端配置示例（Claude Desktop）
```json
{
  "mcpServers": {
    "douyin-mcp": {
      "command": "uvx",
      "args": ["douyin-mcp-server"],
      "env": {
        "STT_PROVIDER": "groq",
        "GROQ_API_KEY": "your-groq-api-key-here",
        "MCP_AUTH_TOKEN": "your-token"
      }
    }
  }
}
```

### 工具概览（供 MCP 客户端调用）
- `get_douyin_download_link(share_link, auth_token?)`：获取无水印下载链接
- `parse_douyin_video_info(share_link, auth_token?)`：解析视频基本信息
- `extract_douyin_text(share_link, model?, auth_token?)`：提取视频文本
- 资源：`douyin://video/{video_id}`：通过视频 ID 获取信息

## 常见问题
- 401 鉴权失败：确认请求头 `Authorization: Bearer <MCP_AUTH_TOKEN>` 已正确设置
- 链接解析失败：确保使用抖音“复制链接”的短链（`https://v.douyin.com/.../`）或完整分享文案
- 返回乱码：服务端为 UTF-8 JSON；如终端显示异常，改用 JSON 解析或合适的字符集查看

## 安全提示
- 不要在仓库内写入明文令牌或密钥
- 建议通过环境变量或 n8n 凭据管理注入 `MCP_AUTH_TOKEN` 与 API Key

## 测试结果
- 测试环境：`Windows PowerShell`、`Python 3.11`、`ffmpeg` 可用
- 服务设置：`STT_PROVIDER=groq`、`ENABLE_HTTP=1`、`PORT=8090`、`MCP_AUTH_TOKEN` 已设置
- 本地地址：`http://127.0.0.1:8090`

- 健康检查
  - 命令：`curl.exe -sS -X GET "http://127.0.0.1:8090/healthz"`
  - 结果：`{"status":"ok","provider":"groq","keys":{"groq":true,"dashscope":false},"auth_required":true,"http_enabled":true,"ffmpeg":true,...}`

- 解析视频信息
  - 命令：
    - `Invoke-WebRequest -Uri "http://127.0.0.1:8090/video-info" -Method POST -Headers @{ Authorization = "Bearer 123123"; "Content-Type" = "application/json" } -Body '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/"}' -UseBasicParsing | Select-Object -ExpandProperty Content`
  - 结果摘要：
    - `status: success`
    - `data.video_id: 7586910579307187491`
    - `data.download_url: https://aweme.snssdk.com/aweme/v1/play/?video_id=...&ratio=720p&line=0`
    - 说明：PowerShell 文本显示可能出现中文乱码，建议改用 `Invoke-RestMethod` 或设置 `UTF-8` 输出编码。

- 获取无水印下载链接
  - 命令：
    - `Invoke-WebRequest -Uri "http://127.0.0.1:8090/download-link" -Method POST -Headers @{ Authorization = "Bearer 123123"; "Content-Type" = "application/json" } -Body '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/"}' -UseBasicParsing | Select-Object -ExpandProperty Content`
  - 结果摘要：
    - `status: success`
    - `data.download_url` 返回有效视频直链

- 提取视频文本（Groq）
  - 命令：
    - `Invoke-WebRequest -Uri "http://127.0.0.1:8090/extract-text" -Method POST -Headers @{ Authorization = "Bearer 123123"; "Content-Type" = "application/json" } -Body '{"share_link":"https://v.douyin.com/OGdvYIvZKuI/","model":"whisper-large-v3-turbo"}' -UseBasicParsing | Select-Object -ExpandProperty Content`
  - 结果摘要：
    - `status: success`
    - `text` 字段返回完整识别文本（PowerShell 输出中文可能乱码）
  - 注意：Groq 模式需要有效 `GROQ_API_KEY` 且本机已安装 `ffmpeg`。若只需文本、且不希望本地转码，可将 `STT_PROVIDER` 切换为 `dashscope`（需 `DASHSCOPE_API_KEY`）。

### PowerShell 字符编码建议
- 设置为 UTF-8：``[Console]::OutputEncoding = [System.Text.Encoding]::UTF8``
- 使用 `Invoke-RestMethod` 获取结构化 JSON：`Invoke-RestMethod -Uri ... | ConvertTo-Json -Depth 6`

## Groq 模式优化（适合受限平台）
- 目标：仅使用 Groq，降低下载与转码开销，减少超时
- 环境变量建议：
  - `VIDEO_RATIO=360p`（降低视频码率与体积）
  - `AUDIO_BITRATE=64k`、`AUDIO_SAMPLE_RATE=16000`、`AUDIO_CHANNELS=1`（减小音频体积）
  - `GROQ_USE_STREAM_TRANSCODE=0`（默认关闭流式转码；部分平台对直读视频源有限制）
  - `GROQ_STREAM_DURATION_SEC=45`（仅在开启流式时生效，用于限制转码时长）
- 远端调用保持不变：`/extract-text` 的 `share_link` 传入短链即可
