"""
视频处理管线：嗅探到视频 → 后台线程下载 → 解密 → 保存到下载目录
"""
import queue
import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from .decryptor import decrypt_file, is_encrypted
from .downloader import MpdDownloader, DownloadCancelled, download_m3u8
from .entities import VideoData
from .logger import logger
from .util import mask_url


def sanitize_name(name: str) -> str:
    """清理文件名中的非法字符"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:40]


def like_text(n: int) -> str:
    """点赞数的文件名写法：12345 → 1.2万；3562 → 3562。

    为什么不用「10万+」：文件名要能排序、能一眼比大小，
    带「+」反而看不出多少。整数就直接给整数。
    """
    try:
        n = int(n)
    except Exception:
        return "0"
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿".replace(".0亿", "亿")
    if n >= 10000:
        v = n / 10000
        return f"{v:.1f}万".replace(".0万", "万") if v < 100 else f"{int(v)}万"
    return str(n)


def _stable_url_part(url: str) -> str:
    """URL 里**不带签名**的部分（netloc + path）。

    视频号 CDN 的 query 里是 encfilekey 之类的临时凭证，每捕获一次都会变；
    只有 netloc+path 对同一个文件稳定 —— 用它做断点文件名才不会一次一个样。
    """
    try:
        u = urlsplit(url or "")
        return f"{u.netloc}{u.path}"
    except Exception:
        return (url or "")[:200]


class Pipeline:
    def __init__(self, download_dir, wait: bool = False, on_event=None,
                 like_stats_getter=None):
        """on_event(event, vd, *args) 可选事件回调（GUI 用）：
           start / progress(vd, done, total) / decrypting / done(vd, path) / failed(vd, msg)

        like_stats_getter: 可选无参回调，返回 True = 当前授权含「点赞增强包」。
           ★ v3.1.5 客户需求：开启点赞功能时，下载的 mp4 文件名里带点赞数。
        """
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._queue: "queue.Queue[Optional[VideoData]]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.wait = wait
        self.on_event = on_event
        self.like_stats_getter = like_stats_getter
        # 停止下载：_stop_event 打断当前任务；_cancelled_urls 剔除排队任务
        self._stop_event = threading.Event()
        self._cancelled_urls: set = set()
        self._current: Optional[VideoData] = None
        self._current_dl: Optional[MpdDownloader] = None

    def _emit(self, event, vd, *args):
        if self.on_event:
            try:
                self.on_event(event, vd, *args)
            except Exception:
                pass

    def submit(self, vd: VideoData):
        self._queue.put(vd)

    def drain(self):
        """等待队列清空并处理完毕（手动模式用）"""
        self._queue.join()
        self._queue.put(None)  # 哨兵：让工作线程退出
        self._thread.join(timeout=3600)

    def _loop(self):
        while True:
            vd = self._queue.get()
            try:
                if vd is None:
                    return
                # 排队中被用户停止 → 直接剔除，不执行
                if vd.url in self._cancelled_urls:
                    self._cancelled_urls.discard(vd.url)
                    self._emit("cancelled", vd, "排队中已停止")
                    continue
                self._process(vd)
            except DownloadCancelled:
                self._emit("cancelled", vd, "用户停止下载")
            except Exception as e:
                logger.error(f"管线异常: {e}")
            finally:
                self._queue.task_done()

    def cancel(self, url: str) -> bool:
        """请求停止某 URL 的下载。
        - 正在下载：置位 stop_event 并关闭底层连接（尽快生效）
        - 排队中：加入 _cancelled_urls，轮到它时直接剔除
        返回 True 表示命中（当前任务或排队任务），False 表示未找到。
        """
        cur = self._current
        if cur is not None and cur.url == url:
            self._stop_event.set()
            if self._current_dl is not None:
                self._current_dl.cancel()
            return True
        # 排队中：标记，_loop 轮到它时剔除
        self._cancelled_urls.add(url)
        return True

    def cancel_all(self) -> int:
        """★ 停止**全部**：打断当前任务 + 清空内部排队。返回清掉的排队数。

        ⚠️ 为什么需要（2026-10-23 客户报障）：
          「点击『停止所有下载』后，序号 500 那行还在一直下载」
          根因：`_stop_all_downloads()` 是**按 URL 逐个 cancel**，
          只对"界面上还在 pending/downloading/decrypting 的记录"生效；
          而 pipeline 内部队列里可能还有记录已被改状态/已不在列表的任务，
          这些没人去 cancel → 队列继续往下跑，看起来像"停不住"。

          这里直接从队列层面清空，不依赖界面状态，才是确定的停。
        """
        self._stop_event.set()
        if self._current_dl is not None:
            try:
                self._current_dl.cancel()
            except Exception:
                pass
        n = 0
        while True:
            try:
                vd = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._queue.task_done()
            except Exception:
                pass
            if vd is None:
                # 哨兵不能被吞掉，放回队尾让工作线程正常退出
                try:
                    self._queue.put(None)
                except Exception:
                    pass
                break
            self._cancelled_urls.discard(getattr(vd, "url", ""))
            n += 1
        return n

    # ---------------- 文件名 / 断点路径 ----------------
    def _like_count_for(self, vd: VideoData) -> int:
        """当前这条要写进文件名的点赞数（0 = 不写）。"""
        try:
            if not self.like_stats_getter or not self.like_stats_getter():
                return 0
            return int(getattr(vd, "like_count", 0) or 0)
        except Exception:
            return 0

    def raw_path_for(self, vd: VideoData) -> Path:
        """下载中的临时文件路径（**稳定命名，不含时间戳**）。

        ★ v3.1.5（客户：下载到一半要能续传）：
          原来叫 `{标题}_{时间戳}_{uid}.enc`，时间戳每次都变 →
          「上次下到 60% 的那个文件」永远对不上，续传等于没有。
          现在只由「视频标识 + CDN 路径 + 大小」决定：
            · 重启工具、换 token 重新捕获 → 仍命中同一个断点
            · 换清晰度（大小不同）→ 各自独立，不会串文件
        """
        partdir = self.download_dir / ".part"
        try:
            partdir.mkdir(parents=True, exist_ok=True)
        except Exception:
            partdir = self.download_dir
        src = f"{vd.video_id or ''}|{_stable_url_part(vd.url)}|{int(vd.size or 0)}"
        key = hashlib.md5(src.encode("utf-8")).hexdigest()[:16]
        return partdir / f"{key}.part"

    def _process(self, vd: VideoData):
        # 绑定当前任务（供 cancel() 精确定位），并复位停止事件
        self._current = vd
        self._stop_event.clear()
        self._current_dl = None
        try:
            self._emit("start", vd)
            ts = time.strftime("%m%d_%H%M%S")
            # 文件名唯一化：同一秒内捕获的多个视频/同一描述不再互相覆盖
            # （2026-08-26 审查发现：原文件名只有 描述+秒级时间戳）
            uid = hashlib.md5((vd.video_id or vd.url or "").encode("utf-8")).hexdigest()[:8]
            base = sanitize_name(vd.description) if vd.description else "wechat_video"
            ext = ".png" if vd.is_image else ".mp4"

            # ★ v3.1.5：授权含「点赞增强包」→ 文件名带点赞数
            #   `标题_赞1.2万_1103_092000_ab12cd34.mp4`
            #   提不到点赞数（0）就不加，绝不写「赞0」误导人。
            like_n = self._like_count_for(vd)
            tag = f"_赞{like_text(like_n)}" if like_n > 0 else ""
            final = self.download_dir / f"{base}{tag}_{ts}_{uid}{ext}"
            raw = self.raw_path_for(vd)

            # 1) 下载
            if vd.url.endswith(".m3u8") or "format=hls" in vd.url:
                logger.info("检测到 M3U8 流媒体，开始下载分片...")
                ok = download_m3u8(vd.url, str(raw), stop_event=self._stop_event)
            else:
                logger.info(f"开始下载 ({vd.size / 1024 / 1024:.1f} MB): {vd.description or mask_url(vd.url)}")
                def _progress(done, total):
                    self._emit("progress", vd, done, total)
                dl = MpdDownloader(vd.url, str(raw), expected_size=vd.size,
                                   progress_callback=_progress,
                                   stop_event=self._stop_event)
                self._current_dl = dl
                ok = dl.start()
            if not ok:
                logger.error(f"下载失败: {mask_url(vd.url)}")
                self._emit("failed", vd, "下载失败（链接可能已过期）")
                return

            # 2) 解密
            head = raw.read_bytes()[:64]
            if vd.decode_key and is_encrypted(head):
                self._emit("decrypting", vd)
                out = decrypt_file(str(raw), vd.decode_key, str(final))
                if out is None:
                    logger.error("解密失败：文件头未变成 ftyp。请确认 decode_key 与 url 来自同一次 API 响应")
                    final.unlink(missing_ok=True)
                    self._emit("failed", vd, "解密失败（key 与 url 不匹配？）")
                    return
                raw.unlink(missing_ok=True)
                logger.ok(f"下载并解密完成: {final}  ({final.stat().st_size / 1024 / 1024:.1f} MB)")
                self._emit("done", vd, str(final))
            else:
                raw.replace(final)
                logger.ok(f"下载完成（无需解密）: {final}  ({final.stat().st_size / 1024 / 1024:.1f} MB)")
                self._emit("done", vd, str(final))
        finally:
            self._current = None
            self._current_dl = None
