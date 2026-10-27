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
    # ★ v2.5.0：视频统计数据（客户需求：点赞/转发/推荐，并可按点赞排序）
    #   字段名不固定（微信不同版本叫法不同），parser 里做容错提取；
    #   提不到就是 0，界面显示「—」，绝不假装有数据。
    like_count: int = 0        # 点赞
    forward_count: int = 0     # 转发
    recommend_count: int = 0   # 推荐
    fav_count: int = 0         # 收藏（暂不展示，留作后续）
    comment_count: int = 0     # 评论总数（视频自己的评论数，不是抓到的条数）
    # 稳定视频标识：media.md5sum（同一视频内容恒定，与 token/URL 无关）。
    # 用于去重——同一视频反复播放会产生新 token/URL，按 URL 去重会失效。
    video_id: str = ""
    # ★ v2.2.16：视频号对象 nonce（objectNonceId / exportId）。
    #   评论 payload 里带的是这个 ID，用它才能把评论挂到**正确的视频**上；
    #   只靠「最后捕获的视频」会串视频（实测 72 条评论串到 3 个视频上）。
    nonce_id: str = ""
    # 同一条 object_desc 下可用的全部清晰度版本：
    #   [{url, size, width, height, bitrate, label}]（含原始/全尺寸 full_url）
    # 旧版只取第一个 url；本版起保留全部，GUI 可切换清晰度后下载。
    renditions: list = field(default_factory=list)
    # 清晰度说明（来自 spec 档位阶梯，仅展示用）：如「微信提供 6 档规格…」
    quality_note: str = ""
    raw: Any = field(default=None, repr=False)
