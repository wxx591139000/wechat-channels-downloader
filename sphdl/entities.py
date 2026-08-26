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
    # 同一条 object_desc 下可用的全部清晰度版本：
    #   [{url, size, width, height, bitrate, label}]（含原始/全尺寸 full_url）
    # 旧版只取第一个 url；本版起保留全部，GUI 可切换清晰度后下载。
    renditions: list = field(default_factory=list)
    # 清晰度说明（来自 spec 档位阶梯，仅展示用）：如「微信提供 6 档规格…」
    quality_note: str = ""
    raw: Any = field(default=None, repr=False)
