"""小鹅通清单持久化（重启工具后清单不丢）。

跟 `bilibili_store.py` 同一套理由与做法（客户原始诉求：重启后上一次解析的
清单是空的）。小鹅通一节就是一条，一门课往往十几节，清单丢了等于白解析。

存什么（一行一节课）：
  · appid / resource_id / product_id（重新解析要用的三件套）
  · 标题 / 时长 / 清晰度
  · 下载状态 + 落盘文件名 + 实测大小

⚠️ **不存 m3u8 地址**：sign 有时效，存了会让人误以为"地址在手随时能下"。
   真正下载时一律现取（一次 detail_info 请求，匿名即可）。

落盘位置：`<程序目录>/xiaoe_records.json`。原子写（先 .tmp 再 os.replace）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import BASE_DIR
from .logger import logger

STORE_FILE = BASE_DIR / "xiaoe_records.json"

#: 上限：一门课很少超过 500 节，1000 行足够
MAX_ENTRIES = 1000

_FIELDS = ("appid", "resource_id", "product_id", "title", "duration",
           "quality", "status", "file", "size")


def video_to_entry(video, status: str = "pending", file: str = "",
                   size: int = 0) -> dict:
    """XiaeoVideo → 可 JSON 化的 dict。"""
    return {
        "appid": video.appid or "",
        "resource_id": video.resource_id or "",
        "product_id": video.product_id or "",
        "title": video.title or "",
        "duration": int(video.duration or 0),
        "quality": video.quality or "",
        "status": status or "pending",
        "file": file or "",
        "size": int(size or 0),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def entry_to_video(e: dict):
    """dict → XiaeoVideo。坏数据返回 None（调用方跳过），不抛异常。"""
    from .xiaoe import XiaeoVideo

    try:
        appid = str(e.get("appid") or "")
        rid = str(e.get("resource_id") or "")
        if not appid or not rid:
            return None
        return XiaeoVideo(
            appid=appid,
            resource_id=rid,
            product_id=str(e.get("product_id") or ""),
            title=str(e.get("title") or rid),
            duration=int(e.get("duration") or 0),
            quality=str(e.get("quality") or ""),
        )
    except Exception as ex:                                # noqa: BLE001
        logger.warn(f"小鹅通清单条目无法还原，已跳过：{type(ex).__name__}: {ex}")
        return None


def save(entries) -> bool:
    """整表保存（dict 列表）。失败只记日志，绝不打断下载。"""
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
        logger.warn(f"小鹅通清单保存失败：{type(e).__name__}: {e}")
        return False


def load() -> list:
    """读回清单（dict 列表）。文件不存在/损坏 → 空列表。"""
    try:
        path = Path(STORE_FILE)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out = [e for e in items
               if isinstance(e, dict) and e.get("appid") and e.get("resource_id")]
        return out[:MAX_ENTRIES]
    except Exception as e:                                 # noqa: BLE001
        logger.warn(f"小鹅通清单读取失败（按空清单处理）：{type(e).__name__}: {e}")
        return []


def clear() -> bool:
    return save([])
