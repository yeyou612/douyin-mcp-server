#!/usr/bin/env python3
"""
抖音无水印视频下载并提取文本的 MCP 服务器

该服务器提供以下功能：
1. 解析抖音分享链接获取无水印视频链接
2. 下载视频并提取音频
3. 从音频中提取文本内容
4. 自动清理中间文件
"""

import os
import re
import json
import requests
import tempfile
import asyncio
from pathlib import Path
from typing import Optional, Tuple
import ffmpeg
from tqdm.asyncio import tqdm
from urllib import request
from http import HTTPStatus
import dashscope
from groq import Groq
import inspect

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context


# 创建 MCP 服务器实例
mcp = FastMCP("Douyin MCP Server", 
              dependencies=["requests", "ffmpeg-python", "tqdm", "dashscope", "groq"])

# 请求头，模拟移动端访问
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
}

# 默认 API 配置
STT_PROVIDER = os.getenv('STT_PROVIDER', 'dashscope')
DEFAULT_GROQ_MODEL = os.getenv('GROQ_STT_MODEL', 'whisper-large-v3-turbo')
DEFAULT_DASHSCOPE_MODEL = os.getenv('DASHSCOPE_STT_MODEL', 'paraformer-v2')

def require_auth(func):
    expected = os.getenv('MCP_AUTH_TOKEN')
    if inspect.iscoroutinefunction(func):
        async def async_wrapper(*args, **kwargs):
            if expected:
                token = kwargs.get('auth_token')
                if token != expected:
                    raise Exception("鉴权失败: 提供的令牌无效")
            return await func(*args, **kwargs)
        return async_wrapper
    else:
        def sync_wrapper(*args, **kwargs):
            if expected:
                token = kwargs.get('auth_token')
                if token != expected:
                    raise Exception("鉴权失败: 提供的令牌无效")
            return func(*args, **kwargs)
        return sync_wrapper


class DouyinProcessor:
    """抖音视频处理器"""
    
    def __init__(self, api_key: str, model: Optional[str] = None, provider: Optional[str] = None):
        self.provider = (provider or STT_PROVIDER).lower()
        self.api_key = api_key
        if self.provider == 'groq':
            self.model = model or DEFAULT_GROQ_MODEL
        elif self.provider == 'dashscope':
            self.model = model or DEFAULT_DASHSCOPE_MODEL
        else:
            raise ValueError("无效的转写提供商，请设置 STT_PROVIDER 为 'dashscope' 或 'groq'")
        self.temp_dir = Path(tempfile.mkdtemp())
        if self.provider == 'groq':
            self.client = Groq(api_key=api_key)
        else:
            dashscope.api_key = api_key
    
    def __del__(self):
        """清理临时目录"""
        import shutil
        if hasattr(self, 'temp_dir') and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def parse_share_url(self, share_text: str) -> dict:
        """从分享文本中提取无水印视频链接"""
        # 提取分享链接
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")
        
        share_url = urls[0]
        share_response = requests.get(share_url, headers=HEADERS)
        video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        share_url = f'https://www.iesdouyin.com/share/video/{video_id}'
        
        # 获取视频页面内容
        response = requests.get(share_url, headers=HEADERS)
        response.raise_for_status()
        
        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析视频信息失败")

        # 解析JSON数据
        json_data = json.loads(find_res.group(1).strip())
        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"
        
        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        else:
            raise Exception("无法从JSON中解析视频或图集信息")

        data = original_video_info["item_list"][0]

        # 获取视频信息
        video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
        desc = data.get("desc", "").strip() or f"douyin_{video_id}"
        
        # 替换文件名中的非法字符
        desc = re.sub(r'[\\/:*?"<>|]', '_', desc)
        
        return {
            "url": video_url,
            "title": desc,
            "video_id": video_id
        }
    
    async def download_video(self, video_info: dict, ctx: Context) -> Path:
        """异步下载视频到临时目录"""
        filename = f"{video_info['video_id']}.mp4"
        filepath = self.temp_dir / filename
        
        ctx.info(f"正在下载视频: {video_info['title']}")
        
        response = requests.get(video_info['url'], headers=HEADERS, stream=True)
        response.raise_for_status()
        
        # 获取文件大小
        total_size = int(response.headers.get('content-length', 0))
        
        # 异步下载文件，显示进度
        with open(filepath, 'wb') as f:
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = downloaded / total_size
                        await ctx.report_progress(downloaded, total_size)
        
        ctx.info(f"视频下载完成: {filepath}")
        return filepath
    
    def extract_audio(self, video_path: Path) -> Path:
        """从视频文件中提取音频"""
        audio_path = video_path.with_suffix('.mp3')
        
        try:
            (
                ffmpeg
                .input(str(video_path))
                .output(str(audio_path), acodec='libmp3lame', q=0)
                .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
            )
            return audio_path
        except Exception as e:
            raise Exception(f"提取音频时出错: {str(e)}")
    
    def transcribe_audio_with_groq(self, audio_path: Path) -> str:
        try:
            with open(audio_path, "rb") as f:
                transcription = self.client.audio.transcriptions.create(
                    file=(audio_path.name, f.read()),
                    model=self.model,
                    response_format="json",
                    temperature=0.0
                )
            text = getattr(transcription, "text", None) or ""
            return text if text else "未识别到文本内容"
        except Exception as e:
            raise Exception(f"提取文字时出错: {str(e)}")
    
    def extract_text_from_video_url(self, video_url: str) -> str:
        try:
            task_response = dashscope.audio.asr.Transcription.async_call(
                model=self.model,
                file_urls=[video_url],
                language_hints=['zh', 'en']
            )
            transcription_response = dashscope.audio.asr.Transcription.wait(
                task=task_response.output.task_id
            )
            if transcription_response.status_code == HTTPStatus.OK:
                for transcription in transcription_response.output['results']:
                    url = transcription['transcription_url']
                    result = json.loads(request.urlopen(url).read().decode('utf8'))
                    temp_json_path = self.temp_dir / 'transcription.json'
                    with open(temp_json_path, 'w') as f:
                        json.dump(result, f, indent=4, ensure_ascii=False)
                    if 'transcripts' in result and len(result['transcripts']) > 0:
                        return result['transcripts'][0]['text']
                    else:
                        return "未识别到文本内容"
            else:
                raise Exception(f"转录失败: {transcription_response.output.message}")
        except Exception as e:
            raise Exception(f"提取文字时出错: {str(e)}")
    
    def cleanup_files(self, *file_paths: Path):
        """清理指定的文件"""
        for file_path in file_paths:
            if file_path.exists():
                file_path.unlink()


@mcp.tool()
@require_auth
def get_douyin_download_link(share_link: str, auth_token: Optional[str] = None) -> str:
    """
    获取抖音视频的无水印下载链接
    
    参数:
    - share_link: 抖音分享链接或包含链接的文本
    
    返回:
    - 包含下载链接和视频信息的JSON字符串
    """
    try:
        processor = DouyinProcessor("")  # 获取下载链接不需要API密钥
        video_info = processor.parse_share_url(share_link)
        
        return json.dumps({
            "status": "success",
            "video_id": video_info["video_id"],
            "title": video_info["title"],
            "download_url": video_info["url"],
            "description": f"视频标题: {video_info['title']}",
            "usage_tip": "可以直接使用此链接下载无水印视频"
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"获取下载链接失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@mcp.tool()
@require_auth
async def extract_douyin_text(
    share_link: str,
    model: Optional[str] = None,
    ctx: Context = None,
    auth_token: Optional[str] = None
) -> str:
    """
    从抖音分享链接提取视频中的文本内容
    
    参数:
    - share_link: 抖音分享链接或包含链接的文本
    - model: 语音识别模型（可选，默认根据 STT_PROVIDER 选择）
    
    返回:
    - 提取的文本内容
    
    注意: 通过环境变量 STT_PROVIDER 选择 'dashscope' 或 'groq'。分别需要：
    - 当为 dashscope 时：DASHSCOPE_API_KEY（可选模型 DASHSCOPE_STT_MODEL，默认 paraformer-v2）
    - 当为 groq 时：GROQ_API_KEY（可选模型 GROQ_STT_MODEL，默认 whisper-large-v3-turbo）
    """
    try:
        provider = os.getenv('STT_PROVIDER', 'dashscope').lower()
        if provider == 'groq':
            api_key = os.getenv('GROQ_API_KEY')
            if not api_key:
                raise ValueError("未设置环境变量 GROQ_API_KEY，请在配置中添加 Groq API 密钥")
            processor = DouyinProcessor(api_key, model, provider='groq')
        else:
            api_key = os.getenv('DASHSCOPE_API_KEY')
            if not api_key:
                raise ValueError("未设置环境变量 DASHSCOPE_API_KEY，请在配置中添加阿里云百炼API密钥")
            processor = DouyinProcessor(api_key, model, provider='dashscope')
        
        # 解析视频链接
        ctx.info("正在解析抖音分享链接...")
        video_info = processor.parse_share_url(share_link)
        
        if provider == 'groq':
            video_path = await processor.download_video(video_info, ctx)
            audio_path = processor.extract_audio(video_path)
            ctx.info("正在从音频中提取文本...")
            text_content = processor.transcribe_audio_with_groq(audio_path)
            processor.cleanup_files(video_path, audio_path)
        else:
            ctx.info("正在从视频中提取文本...")
            text_content = processor.extract_text_from_video_url(video_info['url'])
        
        ctx.info("文本提取完成!")
        return text_content
        
    except Exception as e:
        ctx.error(f"处理过程中出现错误: {str(e)}")
        raise Exception(f"提取抖音视频文本失败: {str(e)}")


@mcp.tool()
@require_auth
def parse_douyin_video_info(share_link: str, auth_token: Optional[str] = None) -> str:
    """
    解析抖音分享链接，获取视频基本信息
    
    参数:
    - share_link: 抖音分享链接或包含链接的文本
    
    返回:
    - 视频信息（JSON格式字符串）
    """
    try:
        processor = DouyinProcessor("")  # 不需要API密钥来解析链接
        video_info = processor.parse_share_url(share_link)
        
        return json.dumps({
            "video_id": video_info["video_id"],
            "title": video_info["title"],
            "download_url": video_info["url"],
            "status": "success"
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, ensure_ascii=False, indent=2)


@mcp.resource("douyin://video/{video_id}")
def get_video_info(video_id: str) -> str:
    """
    获取指定视频ID的详细信息
    
    参数:
    - video_id: 抖音视频ID
    
    返回:
    - 视频详细信息
    """
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}"
    try:
        processor = DouyinProcessor("")
        video_info = processor.parse_share_url(share_url)
        return json.dumps(video_info, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"获取视频信息失败: {str(e)}"


@mcp.prompt()
def douyin_text_extraction_guide() -> str:
    """抖音视频文本提取使用指南"""
    return """
# 抖音视频文本提取使用指南

## 功能说明
这个MCP服务器可以从抖音分享链接中提取视频的文本内容，以及获取无水印下载链接。

## 环境变量配置
请确保设置了以下环境变量：
- `STT_PROVIDER`: 选择 'dashscope' 或 'groq'（默认 'dashscope'）
- 当为 dashscope：
  - `DASHSCOPE_API_KEY`: 阿里云百炼 API 密钥
  - `DASHSCOPE_STT_MODEL`（可选，默认 `paraformer-v2`）
- 当为 groq：
  - `GROQ_API_KEY`: Groq API 密钥
  - `GROQ_STT_MODEL`（可选，默认 `whisper-large-v3-turbo`）
- 鉴权：
  - `MCP_AUTH_TOKEN`: 可选的服务访问令牌；若设置，调用工具时需提供匹配的 `auth_token` 参数

## 使用步骤
1. 复制抖音视频的分享链接
2. 在Claude Desktop配置中设置 `STT_PROVIDER` 以及相应提供商的 API 密钥
3. 使用相应的工具进行操作
4. 如开启鉴权，调用工具需提供 `auth_token` 参数

## 工具说明
- `extract_douyin_text`: 完整的文本提取流程（需要API密钥，若设置鉴权需提供auth_token）
- `get_douyin_download_link`: 获取无水印视频下载链接（无需API密钥，若设置鉴权需提供auth_token）
- `parse_douyin_video_info`: 仅解析视频基本信息（若设置鉴权需提供auth_token）
- `douyin://video/{video_id}`: 获取指定视频的详细信息

## Claude Desktop 配置示例
```json
{
  "mcpServers": {
    "douyin-mcp": {
      "command": "uvx",
      "args": ["douyin-mcp-server"],
      "env": {
        "STT_PROVIDER": "groq",
        "GROQ_API_KEY": "your-groq-api-key-here"
      }
    }
  }
}
```

## 注意事项
- 需要提供有效的 Groq API 密钥（通过环境变量）
- 使用 Groq 的 whisper-large-v3-turbo 或百炼的 paraformer-v2 进行语音识别（分别可通过环境变量配置）
- 支持大部分抖音视频格式
- 获取下载链接无需API密钥
 - 若设置了 `MCP_AUTH_TOKEN`，所有工具调用需提供匹配的 `auth_token` 参数
"""


def main():
    """启动MCP服务器"""
    mcp.run()


if __name__ == "__main__":
    main()
