"""抖音清单持久化（关掉工具 / 重启电脑后清单不丢）。

为什么需要（2026-11-03 客户反馈）：
  「重新启动工具后，发现上一次解析出的抖音博主清单是空的」
  —— 抖音窗口的 `self.videos` 只在内存里，进程一退就没了。
  客户抓了 84 条、下了一半、关工具吃饭，回来一看清单空了，
  只能重新抓一遍（还要再开浏览器滚一遍主页，耗时且容易触发风控）。

存什么：
  · 作品元数据（aweme_id / 标题 / 作者 / 时长 / 大小 / 封面）
  · play_url（带签名的播放地址，**有有效期**）
  · 下载状态 + 已落盘文件名

⚠️ play_url 会过期：恢复出来的清单用于「看得见、知道下过哪些」，
   真正下载时若地址失效，会自动重新解析一次（见 douyin_ui._worker）。
   所以这里必须保留 aweme_id —— 它是重新解析的唯一入口。

落盘位置：`<程序目录>/douyin_records.json`（与 records.json 同级）。
原子写：先写 .tmp 再 os.replace，避免写一半断电把文件写坏。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import BASE_DIR
from .logger import logger

STORE_FILE = BASE_DIR / "douyin_records.json"

# 上限：抓取本身最多 2000 条/主页，清单留 4000 条足够（约 1~2 MB）
MAX_ENTRIES = 4000

# 兼容老文件 / 字段缺失
_FIELDS = ("aweme_id", "title", "author", "duration_ms", "play_url",
           "size", "width", "height", "cover", "audio_url")


def video_to_entry(v, status: str = "pending", file: str = "") -> dict:
    """DouyinVideo → 可 JSON 化的 dict（只存必要字段）。"""
    d = {k: getattr(v, k, None) for k in _FIELDS}
    d["title"] = d.get("title") or ""
    d["play_url"] = d.get("play_url") or ""
    d["size"] = int(d.get("size") or 0)
    d["duration_ms"] = int(d.get("duration_ms") or 0)
    d["status"] = status
    d["file"] = file or ""
    d["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return d


def entry_to_video(e: dict):
    """dict → DouyinVideo（坏数据不抛异常，返回 None）。"""
    from .douyin import DouyinVideo

    try:
        v = DouyinVideo()
        for k in _FIELDS:
            if k in ("duration_ms", "size", "width", "height"):
                try:
                    setattr(v, k, int(e.get(k) or 0))
                except Exception:
                    setattr(v, k, 0)
            else:
                setattr(v, k, str(e.get(k) or ""))
        return v
    except Exception as ex:                                # noqa: BLE001
        logger.warn(f"抖音清单条目无法还原，已跳过：{type(ex).__name__}: {ex}")
        return None


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
        logger.warn(f"抖音清单保存失败：{type(e).__name__}: {e}")
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
            if isinstance(e, dict) and (e.get("aweme_id") or e.get("play_url")):
                out.append(e)
        return out[:MAX_ENTRIES]
    except Exception as e:                                 # noqa: BLE001
        logger.warn(f"抖音清单读取失败（按空清单处理）：{type(e).__name__}: {e}")
        return []


def clear() -> bool:
    return save([])
