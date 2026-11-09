"""B 站清单持久化（关掉工具 / 重启电脑后清单不丢）。

跟 `douyin_store.py` 同一套理由与同一套做法（2026-11-03 客户的原始诉求：
「重新启动工具后，上一次解析出的清单是空的」）。B 站一次解析可能出
几十个分P（一场比赛 8 个 P、一部番剧 24 个 P），清单丢了等于白解析。

存什么（**一行一个分P**，因为下载单位就是分P）：
  · bvid / aid / 视频标题 / UP 主
  · 分P 序号 / cid / 分P 标题 / 时长
  · 下载状态 + 已落盘文件名 + 实测清晰度/大小

⚠️ **不存播放地址**：B 站直链约 2 小时失效，而重取只要一次 playurl
   （匿名就能调）。存了反而会让人误以为"地址在手随时能下"。
   真正下载时一律现取。

落盘位置：`<程序目录>/bilibili_records.json`（与 records.json 同级）。
原子写：先写 .tmp 再 os.replace，避免写一半断电把文件写坏。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import BASE_DIR
from .logger import logger

STORE_FILE = BASE_DIR / "bilibili_records.json"

#: 上限：单视频分P 很少超过 100，1000 行足够（约 300 KB）
MAX_ENTRIES = 1000

_FIELDS = ("bvid", "aid", "video_title", "owner", "part_index", "cid",
           "part_title", "duration", "status", "file", "quality", "size")


def part_to_entry(video, part, status: str = "pending", file: str = "",
                  quality: int = 0, size: int = 0) -> dict:
    """(BiliVideo, BiliPart) → 可 JSON 化的 dict。"""
    return {
        "bvid": video.bvid or "",
        "aid": int(video.aid or 0),
        "video_title": video.title or "",
        "owner": video.owner or "",
        "part_index": int(part.index or 1),
        "cid": int(part.cid or 0),
        "part_title": part.title or "",
        "duration": int(part.duration or 0),
        "status": status or "pending",
        "file": file or "",
        "quality": int(quality or 0),
        "size": int(size or 0),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def entry_to_pair(e: dict):
    """dict → (BiliVideo, BiliPart)。坏数据返回 (None, None)，不抛异常。"""
    from .bilibili import BiliPart, BiliVideo

    try:
        v = BiliVideo(
            bvid=str(e.get("bvid") or ""),
            aid=int(e.get("aid") or 0),
            title=str(e.get("video_title") or ""),
            owner=str(e.get("owner") or ""),
        )
        p = BiliPart(
            index=int(e.get("part_index") or 1),
            cid=int(e.get("cid") or 0),
            title=str(e.get("part_title") or v.title or f"P{int(e.get('part_index') or 1)}"),
            duration=int(e.get("duration") or 0),
            bvid=v.bvid,
            quality=int(e.get("quality") or 0),
            size=int(e.get("size") or 0),
        )
        if not v.bvid or not p.cid:
            return None, None
        return v, p
    except Exception as ex:                                # noqa: BLE001
        logger.warn(f"B 站清单条目无法还原，已跳过：{type(ex).__name__}: {ex}")
        return None, None


def save(entries) -> bool:
    """整表保存（entries 是 dict 列表）。失败只记日志，绝不打断下载。"""
    try:
        rows = list(entries or [])[:MAX_ENTRIES]
        path = Path(STORE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = {"version": 1,
                   "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "count": len(rows),
                   "items": rows}
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except Exception as e:                                 # noqa: BLE001
        logger.warn(f"B 站清单保存失败：{type(e).__name__}: {e}")
        return False


def load() -> list:
    """读回清单（dict 列表）。文件不存在/损坏 → 返回空列表。"""
    try:
        path = Path(STORE_FILE)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out = []
        for e in items:
            if isinstance(e, dict) and e.get("bvid") and e.get("cid"):
                out.append(e)
        return out[:MAX_ENTRIES]
    except Exception as e:                                 # noqa: BLE001
        logger.warn(f"B 站清单读取失败（按空清单处理）：{type(e).__name__}: {e}")
        return []


def clear() -> bool:
    return save([])
