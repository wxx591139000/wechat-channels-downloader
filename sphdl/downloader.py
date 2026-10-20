"""
视频下载器：
  - MP4: 单连接顺序下载 + 断点续传（适配微信 CDN 间歇性限速的实测行为）
  - M3U8: 顺序下载 ts 分片并合并
（改编自参考实现 ThinkerWen/wechat-downloader 的 video_downloader.py）
"""
import os
import threading
import time
from typing import Callable, Optional

import requests
import urllib3

from .config import USER_AGENT
from . import shared
from .logger import logger
from .util import mask_url

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DownloadCancelled(Exception):
    """用户主动停止下载（区别于下载失败，不重试）"""


def format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


class MpdDownloader:
    """单连接顺序下载器（适配微信视频号 CDN 的实测行为）。

    CDN 行为（2026-08-24 真机实测）：
      - 对非微信客户端的连接只做「间歇性」低速服务（约 10-20KB/s），
        连接经常被晾住（连续 60s+ 无数据）；
      - 多并发分片会互相争抢服务名额，出现分片饿死（整段 0 字节）；
      - 微信 webview（UA 含 MicroMessenger）播放时可获得 50-85KB/s。
    因此策略：
      1. 单连接顺序下载（不分片），避免分片饿死；
      2. 读超时放宽到 120s，中断后从已下载位置 Range 续传（最多 8 次）；
      3. 优先使用 addon 捕获到的微信 webview UA（shared 模块同进程共享）；
      4. 真实总大小优先取 HEAD 的 Content-Length，否则取 Range 探测的
         Content-Range（元数据 file_size 常对应更高清晰度，不可靠）。
    """

    READ_TIMEOUT = 120      # 秒：CDN 间歇服务，读超时必须放宽
    MAX_RESUMES = 8         # 断点续传次数
    CHUNK = 65536           # iter_content 块大小

    def __init__(self, url, save_path, expected_size=0,
                 progress_callback: Optional[Callable] = None,
                 stop_event: Optional[threading.Event] = None):
        self.url = url
        self.save_path = save_path
        self.expected_size = expected_size
        self.progress_callback = progress_callback
        self.stop_event = stop_event
        self._resp: Optional[requests.Response] = None
        self.total_size = 0
        self.headers = {
            # 微信 webview 的 UA 被 CDN 优先服务；捕获不到时用默认 UA
            "User-Agent": shared.get_wechat_ua() or USER_AGENT,
        }

    # ---------- 停止下载 ----------
    def cancel(self):
        """请求停止：置位事件 + 关闭当前响应（打断阻塞中的读取）。"""
        if self.stop_event is None:
            self.stop_event = threading.Event()
        self.stop_event.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass

    def _cancelled(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def start(self) -> bool:
        if self._cancelled():
            raise DownloadCancelled()
        if not self._get_file_info():
            return False
        if self.total_size > 0:
            logger.info(f"真实文件大小: {format_size(self.total_size)}（单连接顺序下载）")
        return self._download_streaming()

    def _get_file_info(self) -> bool:
        """HEAD 取大小；若 HEAD 无 Content-Length，用 Range 探测的
        Content-Range 头（bytes 0-0/TOTAL）拿真实总大小。"""
        if self._cancelled():
            raise DownloadCancelled()
        try:
            r = requests.head(self.url, headers=self.headers, timeout=20, verify=False)
            if r.status_code >= 400:
                logger.error(f"服务器拒绝访问 (HTTP {r.status_code})：链接可能已过期，请重新获取")
                return False
            self.total_size = int(r.headers.get("Content-Length", 0) or 0)
            if self.total_size > 0:
                return True
            # HEAD 未给大小 → Range 探测（顺带验证 Range 支持）
            h = dict(self.headers)
            h["Range"] = "bytes=0-0"
            r2 = requests.get(self.url, headers=h, timeout=20, stream=True, verify=False)
            cr = r2.headers.get("Content-Range", "")
            r2.close()
            if r2.status_code == 206 and "/" in cr:
                try:
                    self.total_size = int(cr.rsplit("/", 1)[1])
                    logger.info(f"从 Content-Range 取得真实大小: {format_size(self.total_size)}")
                    return True
                except ValueError:
                    pass
            if r2.status_code >= 400 and r2.status_code != 206:
                logger.error(f"Range 探测被拒 (HTTP {r2.status_code})：链接可能已过期")
                return False
        except DownloadCancelled:
            raise
        except Exception as e:
            logger.error(f"获取文件信息失败: {e}")
            return False
        self.total_size = self.expected_size
        if self.total_size > 0:
            logger.info(f"使用元数据大小: {format_size(self.total_size)}")
        else:
            logger.warn("无法获取文件大小，按流式下载（不知道总进度）")
        return True

    def _download_streaming(self) -> bool:
        """单连接顺序下载；网络中断/超时后从已下载字节处 Range 续传。
        用户停止时抛 DownloadCancelled（临时文件由 finally 清理）。"""
        tmp = self.save_path + ".tmp"
        pos = 0
        try:
            for attempt in range(self.MAX_RESUMES + 1):
                if self._cancelled():
                    raise DownloadCancelled()
                h = dict(self.headers)
                if pos > 0:
                    h["Range"] = f"bytes={pos}-"
                try:
                    r = requests.get(self.url, headers=h, stream=True,
                                     timeout=(20, self.READ_TIMEOUT), verify=False)
                    self._resp = r
                    if r.status_code == 416:
                        # 已下完（总大小估算偏大）→ 完成
                        return self._finalize(tmp, pos)
                    if r.status_code not in (200, 206):
                        if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                            logger.error(f"服务器拒绝下载 (HTTP {r.status_code})：链接可能已过期")
                            return False
                        time.sleep(3)
                        continue
                    if r.status_code == 200 and pos > 0:
                        # 服务端忽略 Range 从头返回 → 只能重新下载
                        pos = 0
                    mode = "r+b" if pos > 0 else "wb"
                    with open(tmp, mode) as f:
                        f.seek(pos)
                        last_progress = [0]
                        for chunk in r.iter_content(chunk_size=self.CHUNK):
                            if self._cancelled():
                                raise DownloadCancelled()
                            if not chunk:
                                continue
                            f.write(chunk)
                            pos += len(chunk)
                            if self.progress_callback:
                                self.progress_callback(pos, self.total_size)
                            elif self.total_size > 0:
                                pct = min(99, pos * 100 // self.total_size)
                                if pct >= last_progress[0] + 5:
                                    last_progress[0] = pct
                                    logger.info(f"下载中 {format_size(pos)} / {format_size(self.total_size)} ({pct}%)")
                    r.close()
                    self._resp = None
                    if self.total_size > 0 and pos < self.total_size:
                        # 流“正常”结束但数据不全（服务端提前停止服务，
                        # Content-Length 只声明了它给的量）→ 从断点继续
                        if attempt >= self.MAX_RESUMES:
                            logger.error(f"多次提前结束，仅收到 {format_size(pos)}/{format_size(self.total_size)}，放弃")
                            return False
                        logger.warn(f"流提前结束（{format_size(pos)}/{format_size(self.total_size)}），续传（{attempt + 1}/{self.MAX_RESUMES}）...")
                        time.sleep(1)
                        continue
                    # 正常读完
                    return self._finalize(tmp, pos)
                except DownloadCancelled:
                    raise
                except Exception as e:
                    self._resp = None
                    if attempt >= self.MAX_RESUMES:
                        logger.error(f"下载中断且续传用尽: {e}（已下载 {format_size(pos)}）")
                        return False
                    logger.warn(f"下载中断（{e}），{format_size(pos)} 处续传（第 {attempt + 1}/{self.MAX_RESUMES} 次）...")
                    time.sleep(2)
        finally:
            self._resp = None
            # ★ v2.2.0：失败/中断时**保留 .tmp**，不再删除。
            #
            # 为什么改（客户实测反馈 2026-10-20）：
            #   客户「停止嗅探」时，mitmproxy 进程退出会把正在走的连接 RST 掉
            #   → requests 抛 ConnectionError → 进续传分支。
            #   如果续传用尽（8 次）就返回 False，而旧代码在 finally 里
            #   **无条件删掉 .tmp** → 已下载的几百 MB 全丢，只能从头再下。
            #   客户看到的就是"下载停了、进度没了"。
            #
            # 保留 .tmp 的好处：
            #   · 下次点「重试失败」/「重新下载」时能用 Range 从断点续
            #   · 代理断开这种临时故障，续传基本都能成功
            # 代价：失败任务会留下 .tmp 占磁盘。
            #   → 由 _cleanup_stale_tmp() 在启动时清理超过 7 天的残留，
            #     并在「删除记录/删除文件+记录」时一并删掉对应 .tmp。
            if (not os.path.exists(self.save_path) and os.path.exists(tmp)
                    and self._keep_tmp()):
                logger.info(f"已保留断点文件以便续传: {os.path.basename(tmp)}"
                            f"（{format_size(pos)}）")
            elif os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        return False

    def _keep_tmp(self) -> bool:
        """失败时是否保留 .tmp。

        ★ 用户主动「停止下载」→ 不保留（用户明确不要了，别占磁盘）
        ★ 网络/代理等原因失败 → 保留（下次可续传）
        """
        # _cancelled() 为真 = 用户主动停；否则视为意外中断
        try:
            return not self._cancelled()
        except Exception:
            return True

    def _finalize(self, tmp: str, pos: int) -> bool:
        """下载完成：校验大小（若已知）→ 原子改名。"""
        if self.total_size > 0 and pos < self.total_size:
            logger.warn(
                f"实际 {format_size(pos)} < 元数据 {format_size(self.total_size)}"
                "——微信元数据 file_size 常对应更高清晰度，实际地址服务较低版本；"
                "文件本身完整可播。若想下更高清晰度，请用『清晰度』切换版本"
                "（有原始画质时）")
            # 仍交付（服务端实际就这么多；HEAD/Range 拿不到真实大小时才会走到这里）
        try:
            os.replace(tmp, self.save_path)
            return True
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            return False


def download_file(url: str, save_path: str, expected_size: int = 0,
                  progress_callback=None, stop_event=None) -> bool:
    """下载单个文件（MP4 等）。progress_callback(done, total) 可选，传 None 则打印日志。
    stop_event 置位时抛出 DownloadCancelled。"""
    def cb(done, total):
        if progress_callback:
            progress_callback(done, total)
        elif total > 0:
            pct = done * 100 // total
            logger.info(f"下载中 {format_size(done)} / {format_size(total)} ({pct}%)")
    return MpdDownloader(url, save_path, expected_size=expected_size,
                         progress_callback=cb, stop_event=stop_event).start()


def download_m3u8(m3u8_url: str, save_path: str, stop_event=None) -> bool:
    """下载 M3U8 流：拉取播放列表 → 逐片下载 ts → 合并（含相对路径处理）。
    stop_event 置位时抛出 DownloadCancelled。"""
    import posixpath
    session = requests.Session()
    session.headers["User-Agent"] = shared.get_wechat_ua() or USER_AGENT
    tmp = save_path + ".tmp"
    try:
        r = session.get(m3u8_url, timeout=30, verify=False)
        r.raise_for_status()
        base = posixpath.dirname(m3u8_url)
        ts_urls = []
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ts_urls.append(line if line.startswith("http") else posixpath.join(base, line))
        if not ts_urls:
            logger.error("M3U8 中没有找到 ts 分片")
            return False
        logger.info(f"M3U8: 共 {len(ts_urls)} 个分片，开始下载...")
        with open(tmp, "wb") as out:
            for i, u in enumerate(ts_urls):
                if stop_event is not None and stop_event.is_set():
                    raise DownloadCancelled()
                for attempt in range(3):
                    try:
                        seg = session.get(u, timeout=60, verify=False)
                        seg.raise_for_status()
                        out.write(seg.content)
                        break
                    except DownloadCancelled:
                        raise
                    except Exception:
                        if attempt == 2:
                            logger.error(f"分片 {i} 下载失败: {mask_url(u)}")
                            return False
                        time.sleep(1)
                if (i + 1) % 10 == 0 or i + 1 == len(ts_urls):
                    logger.info(f"分片 {i + 1}/{len(ts_urls)}")
        os.replace(tmp, save_path)
        return True
    except DownloadCancelled:
        raise
    except Exception as e:
        logger.error(f"M3U8 下载失败: {e}")
        return False
    finally:
        if not os.path.exists(save_path) and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
