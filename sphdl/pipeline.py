"""
视频处理管线：嗅探到视频 → 后台线程下载 → 解密 → 保存到下载目录
"""
import queue
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .decryptor import decrypt_file, is_encrypted
from .downloader import MpdDownloader, DownloadCancelled, download_m3u8
from .entities import VideoData
from .logger import logger


def sanitize_name(name: str) -> str:
    """清理文件名中的非法字符"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:40]


class Pipeline:
    def __init__(self, download_dir, wait: bool = False, on_event=None):
        """on_event(event, vd, *args) 可选事件回调（GUI 用）：
           start / progress(vd, done, total) / decrypting / done(vd, path) / failed(vd, msg)
        """
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._queue: "queue.Queue[Optional[VideoData]]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.wait = wait
        self.on_event = on_event
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

    def _process(self, vd: VideoData):
        # 绑定当前任务（供 cancel() 精确定位），并复位停止事件
        self._current = vd
        self._stop_event.clear()
        self._current_dl = None
        try:
            self._emit("start", vd)
            ts = time.strftime("%m%d_%H%M%S")
            base = sanitize_name(vd.description) if vd.description else "wechat_video"
            ext = ".png" if vd.is_image else ".mp4"

            # 1) 下载
            if vd.url.endswith(".m3u8") or "format=hls" in vd.url:
                logger.info("检测到 M3U8 流媒体，开始下载分片...")
                raw = self.download_dir / f"{base}_{ts}.mp4.part"
                ok = download_m3u8(vd.url, str(raw), stop_event=self._stop_event)
            else:
                raw = self.download_dir / f"{base}_{ts}.enc"
                logger.info(f"开始下载 ({vd.size / 1024 / 1024:.1f} MB): {vd.description or vd.url[:70]}")
                def _progress(done, total):
                    self._emit("progress", vd, done, total)
                dl = MpdDownloader(vd.url, str(raw), expected_size=vd.size,
                                   progress_callback=_progress,
                                   stop_event=self._stop_event)
                self._current_dl = dl
                ok = dl.start()
            if not ok:
                logger.error(f"下载失败: {vd.url[:80]}")
                self._emit("failed", vd, "下载失败（链接可能已过期）")
                return

            # 2) 解密
            head = raw.read_bytes()[:64]
            if vd.decode_key and is_encrypted(head):
                self._emit("decrypting", vd)
                final = self.download_dir / f"{base}_{ts}{ext}"
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
                final = self.download_dir / f"{base}_{ts}{ext}"
                raw.replace(final)
                logger.ok(f"下载完成（无需解密）: {final}  ({final.stat().st_size / 1024 / 1024:.1f} MB)")
                self._emit("done", vd, str(final))
        finally:
            self._current = None
            self._current_dl = None
