"""数据模型"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class VideoData:
    """从 object_desc 中提取出的视频信息"""
    url: str
    decode_key: str = ""
    size: int = 0
    description: str = ""
    is_image: bool = False
    cover_url: str = ""
    # 稳定视频标识：media.md5sum（同一视频内容恒定，与 token/URL 无关）。
    # 用于去重——同一视频反复播放会产生新 token/URL，按 URL 去重会失效。
    video_id: str = ""
    raw: Any = field(default=None, repr=False)
