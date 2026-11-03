"""验证 v3.1.5：断点续传（抖音 + 视频号）+ 抖音清单持久化 + 点赞数进文件名。

为什么用本地 HTTP 服务器而不是真去抖音/视频号下：
    要验的是「**从已有字节处续传**」这件事，必须能精确控制服务端行为
    （支持/不支持 Range、Content-Range 起点、总长度）。真站点给不了这种
    可重复的条件。文件内容用固定种子生成，逐字节比对才知道续传有没有错位。

覆盖：
    A. douyin_store 保存→读取 往返（字段不丢、原子写）
    B. douyin._http_to_file：已有 600KB → 只请求剩余部分，内容逐字节正确
    C. douyin.download 端到端：.part 续传 → .mp4 完整、.part 清理
    D. 视频号 MpdDownloader：已有 .tmp → Range 续传，内容正确
    E. pipeline 文件名：授权点赞 → 带「_赞1.2万_」；未授权 / 点赞为 0 → 不带
    F. pipeline 断点路径：同视频换 token 同一路径；换清晰度不同路径
    G. 真实 DouyinWindow 恢复上次清单（含「已完成」不勾选）
    H. 恢复清单里 play_url 过期 → 自动重新解析后接着下，且断点续传生效

用法：../.venv/Scripts/python.exe tests/verify_v315_resume_like.py
    退出码 0 = 全绿；1 = 有失败项。
"""
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="sphdl_v315_"))
os.environ["SPHDL_DATA_DIR"] = str(_TMP)
(_TMP / "config.json").write_text(json.dumps({"auto_download": False}),
                                  encoding="utf-8")

FAILS = []


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


# ────────────────── 本地 CDN（可控 Range 行为） ──────────────────
class _CDN:
    def __init__(self, size=1200 * 1024, support_range=True):
        rnd = random.Random(20261103)
        self.data = bytes(rnd.getrandbits(8) for _ in range(size))
        self.ranges = []
        self.support_range = support_range
        cdn = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(cdn.data)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

            def do_GET(self):
                rng = self.headers.get("Range")
                cdn.ranges.append(rng)
                start = 0
                if rng and cdn.support_range and rng.startswith("bytes="):
                    try:
                        start = int(rng.split("=", 1)[1].split("-")[0])
                    except Exception:
                        start = 0
                if start:
                    if start >= len(cdn.data):
                        self.send_response(416)
                        self.send_header("Content-Range",
                                         f"bytes */{len(cdn.data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    body = cdn.data[start:]
                    self.send_response(206)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Range",
                                     f"bytes {start}-{len(cdn.data) - 1}"
                                     f"/{len(cdn.data)}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(cdn.data)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    self.wfile.write(cdn.data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name="v"):
        return f"http://127.0.0.1:{self.port}/{name}.mp4"

    def stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass


# ────────────────── A. store 往返 ──────────────────
def test_store():
    print("\n[A] douyin_store 保存 / 读取往返")
    from sphdl import douyin_store
    from sphdl.douyin import DouyinVideo

    store = _TMP / "douyin_records_test.json"
    douyin_store.STORE_FILE = store
    vs = [DouyinVideo(aweme_id="7300000000000000001", title="沉浸式打乒乓球\n第二行",
                      author="老王", duration_ms=83000,
                      play_url="https://x/y.mp4?expire=1", size=1234567),
          DouyinVideo(aweme_id="7300000000000000002", title="第二条",
                      author="小李", duration_ms=12000,
                      play_url="https://x/z.mp4", size=88)]
    rows = [douyin_store.video_to_entry(vs[0], status="done", file="C:/a/b.mp4"),
            douyin_store.video_to_entry(vs[1])]
    ok = douyin_store.save(rows)
    check("保存成功", ok)
    check("文件已落盘且无残留 .tmp",
          store.exists() and not (store.with_name(store.name + ".tmp")).exists())
    got = douyin_store.load()
    check("读回 2 条", len(got) == 2, f"{len(got)} 条")
    if len(got) == 2:
        v0 = douyin_store.entry_to_video(got[0])
        check("字段往返一致（标题含换行也保留原文）",
              v0.aweme_id == vs[0].aweme_id and v0.title == vs[0].title
              and v0.size == vs[0].size and v0.duration_ms == vs[0].duration_ms
              and v0.play_url == vs[0].play_url, v0.title)
        check("状态/文件路径保留", got[0]["status"] == "done"
              and got[0]["file"] == "C:/a/b.mp4")

    store.write_text("{坏掉的 json", encoding="utf-8")
    check("坏文件不抛异常，按空清单处理", douyin_store.load() == [])


# ────────────────── B. 抖音续传底层 ──────────────────
def test_douyin_partial():
    print("\n[B] 抖音 _http_to_file：从已有字节续传")
    from sphdl.douyin import _http_to_file

    cdn = _CDN()
    try:
        part = _TMP / "b_douyin.part"
        have = 600 * 1024
        part.write_bytes(cdn.data[:have])

        got_progress = []
        ok, err, size = _http_to_file(
            cdn.url(), part,
            headers={"User-Agent": "t", "Referer": "https://www.douyin.com/"},
            on_progress=lambda g, t, d: got_progress.append((g, t)),
            expected_size=len(cdn.data))
        check("返回成功", ok, err or "")
        check("首个请求带 Range 且从断点开始",
              cdn.ranges and cdn.ranges[0] == f"bytes={have}-",
              str(cdn.ranges[:2]))
        check("最终文件字节数 == 源文件", size == len(cdn.data),
              f"{size} vs {len(cdn.data)}")
        check("内容逐字节一致（续传没写错位）",
              hashlib.sha256(part.read_bytes()).hexdigest()
              == hashlib.sha256(cdn.data).hexdigest())
        check("进度从已有字节起算（不是从 0）",
              got_progress and got_progress[0][0] > have,
              str(got_progress[:1]))
    finally:
        cdn.stop()


# ────────────────── C. 抖音 download 端到端 ──────────────────
def test_douyin_download():
    print("\n[C] douyin.download：.part 续传 → .mp4")
    from sphdl.douyin import DouyinVideo, download, safe_name

    cdn = _CDN()
    try:
        dest = _TMP / "dy_out"
        dest.mkdir(exist_ok=True)
        v = DouyinVideo(aweme_id="7300000000000000009", title="测试标题",
                        play_url=cdn.url(), size=len(cdn.data))
        base = safe_name(f"{v.title}_{v.aweme_id}")
        part = dest / f"{base}.part"
        part.write_bytes(cdn.data[:400 * 1024])

        ok, info = download(v, dest, should_stop=lambda: False)
        check("下载成功", ok, str(info)[:60])
        final = dest / f"{base}.mp4"
        check("生成 .mp4", final.exists())
        check("内容正确", final.exists() and hashlib.sha256(
            final.read_bytes()).hexdigest()
            == hashlib.sha256(cdn.data).hexdigest())
        check(".part 已被清理", not part.exists())
        check("确实用了 Range 续传",
              any((r or "").startswith("bytes=409600-") for r in cdn.ranges),
              str(cdn.ranges[:3]))

        # 再来一次：已完成 → 直接跳过，不再发请求
        n_before = len(cdn.ranges)
        ok2, info2 = download(v, dest, should_stop=lambda: False)
        check("已完成的再下一次 → 直接跳过（不重复下载）",
              ok2 and len(cdn.ranges) == n_before,
              f"新请求 {len(cdn.ranges) - n_before} 个")
    finally:
        cdn.stop()


# ────────────────── D. 视频号 MpdDownloader 续传 ──────────────────
def test_mpd_resume():
    print("\n[D] MpdDownloader：已有 .tmp → 跨进程续传")
    from sphdl.downloader import MpdDownloader

    cdn = _CDN()
    try:
        out = _TMP / "mpd_out.m4v"
        tmp = Path(str(out) + ".tmp")
        have = 300 * 1024
        tmp.write_bytes(cdn.data[:have])
        dl = MpdDownloader(cdn.url(), str(out), expected_size=len(cdn.data),
                           stop_event=threading.Event())
        ok = dl.start()
        check("下载成功", ok)
        check("首个请求带 Range", cdn.ranges and cdn.ranges[0] == f"bytes={have}-",
              str(cdn.ranges[:2]))
        check("最终文件内容正确",
              out.exists() and hashlib.sha256(out.read_bytes()).hexdigest()
              == hashlib.sha256(cdn.data).hexdigest())
        check(".tmp 已清理", not tmp.exists())
    finally:
        cdn.stop()


# ────────────────── E/F. pipeline 文件名与断点路径 ──────────────────
def test_pipeline_names():
    print("\n[E] pipeline：点赞数进文件名")
    from sphdl.entities import VideoData
    from sphdl.pipeline import Pipeline, like_text

    check("like_text 格式化", (like_text(3562) == "3562"
                              and like_text(12345) == "1.2万"
                              and like_text(100000) == "10万"
                              and like_text(0) == "0"),
          f"{like_text(3562)} / {like_text(12345)} / {like_text(100000)}")

    cdn = _CDN(size=300 * 1024)
    try:
        events = []

        def mk_pipe(enabled, tag):
            # ★ 每个用例独立目录：否则 ①③ 都落到 pl_1，glob 会把上一条也数进来
            return Pipeline(_TMP / f"pl_{tag}", on_event=lambda *a: events.append(a),
                            like_stats_getter=lambda: enabled)

        # ① 授权点赞 → 文件名带「赞1.2万」
        p = mk_pipe(True, "on")
        d1 = _TMP / "pl_on"
        p.submit(VideoData(url=cdn.url(), size=len(cdn.data),
                           description="羞答答的玫瑰", like_count=12345,
                           video_id="vid-aaa"))
        p.drain()
        files1 = sorted(x.name for x in d1.glob("*.mp4"))
        check("授权点赞 → 文件名含 _赞1.2万_",
              len(files1) == 1 and "_赞1.2万_" in files1[0], str(files1))

        # ② 未授权 → 不带点赞
        p0 = mk_pipe(False, "off")
        d0 = _TMP / "pl_off"
        p0.submit(VideoData(url=cdn.url(), size=len(cdn.data),
                            description="羞答答的玫瑰", like_count=12345,
                            video_id="vid-bbb"))
        p0.drain()
        files0 = sorted(x.name for x in d0.glob("*.mp4"))
        check("未授权 → 文件名不含赞",
              len(files0) == 1 and "_赞" not in files0[0], str(files0))

        # ③ 授权但点赞数为 0（提不到）→ 绝不写「赞0」
        p2 = mk_pipe(True, "zero")
        d2 = _TMP / "pl_zero"
        p2.submit(VideoData(url=cdn.url(), size=len(cdn.data),
                            description="没有点赞数据", like_count=0,
                            video_id="vid-ccc"))
        p2.drain()
        files2 = sorted(x.name for x in d2.glob("*.mp4"))
        check("点赞为 0 → 不加「赞0」",
              len(files2) == 1 and "_赞" not in files2[0], str(files2))

        print("\n[F] pipeline：断点路径只由内容标识决定")
        a = VideoData(url="https://finder.video.qq.com/251/20302/stodownload?encfilekey=AAA",
                      size=100, video_id="same")
        b = VideoData(url="https://finder.video.qq.com/251/20302/stodownload?encfilekey=BBB",
                      size=100, video_id="same")
        c = VideoData(url="https://finder.video.qq.com/251/20302/stodownload?encfilekey=AAA",
                      size=999, video_id="same")
        pa = Pipeline.raw_path_for(p, a)
        pb = Pipeline.raw_path_for(p, b)
        pc = Pipeline.raw_path_for(p, c)
        check("换 token 后断点路径不变（重启能续传）", pa == pb, str(pa.name))
        check("换清晰度（大小不同）→ 断点互不干扰", pa != pc)
        check("断点落在下载目录的 .part 子目录",
              pa.parent.name == ".part", str(pa))
    finally:
        cdn.stop()


# ────────────────── G/H. 抖音窗口恢复清单 ──────────────────
# ────────────────── I/J. 端到端续传的两个真实场景 ──────────────────
def test_pipeline_resume_across_restart():
    print("\n[I] 视频号跨重启续传：预置 .part/.tmp → 只请求剩余")
    from sphdl.entities import VideoData
    from sphdl.pipeline import Pipeline

    cdn = _CDN(size=900 * 1024)
    try:
        d = _TMP / "pl_resume"
        p = Pipeline(d, like_stats_getter=lambda: False)
        vd = VideoData(url=cdn.url(), size=len(cdn.data),
                       description="续传测试", video_id="vid-resume")
        raw = p.raw_path_for(vd)
        raw.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(raw) + ".tmp")
        have = 500 * 1024
        tmp.write_bytes(cdn.data[:have])          # 模拟"上次下到 500KB 就断了"

        p.submit(vd)
        p.drain()
        files = sorted(x.name for x in d.glob("*.mp4"))
        check("下载完成并落成 mp4", len(files) == 1, str(files))
        check("首个请求从断点开始",
              cdn.ranges and cdn.ranges[0] == f"bytes={have}-",
              str(cdn.ranges[:2]))
        check("内容逐字节正确",
              bool(files) and hashlib.sha256(
                  (d / files[0]).read_bytes()).hexdigest()
              == hashlib.sha256(cdn.data).hexdigest())
        check(".part 目录已清空（不残留断点）",
              not list(raw.parent.glob("*")), str(list(raw.parent.glob("*"))))
    finally:
        cdn.stop()


def test_douyin_part_name_fallback():
    print("\n[J] 标题变了也能接上断点（按 aweme_id 兜底）")
    from sphdl.douyin import DouyinVideo, download

    cdn = _CDN(size=600 * 1024)
    try:
        dest = _TMP / "dy_align"
        dest.mkdir(exist_ok=True)
        old = dest / "旧标题_888.part"
        have = 200 * 1024
        old.write_bytes(cdn.data[:have])

        v = DouyinVideo(aweme_id="888", title="新标题（作者改过文案）",
                        play_url=cdn.url(), size=len(cdn.data))
        ok, info = download(v, dest, should_stop=lambda: False)
        check("下载成功", ok, str(info)[:60])
        check("复用了旧标题的断点（Range 起点 = 已有字节）",
              cdn.ranges and cdn.ranges[0] == f"bytes={have}-",
              str(cdn.ranges[:2]))
        check("最终按新标题落盘",
              (dest / "新标题（作者改过文案）_888.mp4").exists(),
              str([x.name for x in dest.iterdir()]))
        check("旧 .part 已收尾清理", not old.exists())
    finally:
        cdn.stop()


class _FakeApp:
    def __init__(self, root, out_dir):
        import queue
        import tkinter as tk
        self.root = root
        self.ui_q = queue.Queue()
        self.dir_var = tk.StringVar(master=root, value=str(out_dir))
        self.logs = []

    def push_log(self, lv, msg):
        self.logs.append((lv, msg))


def test_douyin_window_restore():
    print("\n[G] DouyinWindow：重启后恢复上次清单")
    import tkinter as tk
    from sphdl import douyin_store
    from sphdl.douyin import DouyinVideo

    store = _TMP / "dy_ui_store.json"
    douyin_store.STORE_FILE = store
    out_dir = _TMP / "dy_ui_out"
    out_dir.mkdir(exist_ok=True)

    done_file = out_dir / "已经下过.mp4"
    done_file.write_bytes(b"x" * 2048)

    douyin_store.save([
        douyin_store.video_to_entry(
            DouyinVideo(aweme_id="111", title="已经下过的作品", author="作者A",
                        duration_ms=30000, play_url="https://x/1.mp4", size=2048),
            status="done", file=str(done_file)),
        douyin_store.video_to_entry(
            DouyinVideo(aweme_id="222", title="还没下的作品", author="作者A",
                        duration_ms=61000, play_url="https://x/2.mp4", size=999)),
        douyin_store.video_to_entry(
            DouyinVideo(aweme_id="333", title="上次失败的", author="作者A",
                        duration_ms=5000, play_url="https://x/3.mp4", size=5),
            status="failed"),
    ])

    root = tk.Tk()
    root.withdraw()
    win = None
    try:
        from sphdl.douyin_ui import DouyinWindow
        app = _FakeApp(root, out_dir)
        win = DouyinWindow(app)
        for _ in range(15):
            root.update_idletasks()
            root.update()
            time.sleep(0.03)

        rows = win.tree.get_children()
        check("恢复了 3 行", len(rows) == 3, f"{len(rows)} 行")
        vals = [win.tree.item(r, "values") for r in rows]
        check("标题列显示原文（单行压平）",
              vals[0][1] == "已经下过的作品", str(vals[0][1]))
        check("已下载的显示「✅ 已完成」且不勾选",
              vals[0][5] == "✅ 已完成" and vals[0][0] == "☐",
              f"{vals[0][0]} / {vals[0][5]}")
        check("未下载的勾上，状态「待下载」",
              vals[1][0] == "☑" and vals[1][5] == "待下载")
        check("上次失败的标出来", "上次失败" in vals[2][5], vals[2][5])
        check("日志告知用户恢复了多少条",
              any("恢复" in m for _, m in app.logs), str(app.logs[:2]))

        # 长标题：列宽放不下时悬停应能拿到全文（v3.1.5 补，抖音标题很长）
        long_t = "沉浸式打乒乓球！第一视角看对错？用 Osmo Nano 记录打球日常" * 3
        iid_long = win.tree.insert("", "end", values=(
            "☑", long_t, "-", "-", "-", "待下载"))
        check("超宽标题 → 悬停返回完整全文",
              win._tip_text_for_cell(iid_long, 1) == long_t)
        check("短标题 → 不弹气泡",
              win._tip_text_for_cell(rows[0], 1) == "")
        win.tree.delete(iid_long)
    finally:
        try:
            if win is not None:
                win.win.destroy()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    print("\n[H] 清单里 play_url 过期 → 自动重新解析后接着下")
    import sphdl.douyin as DY
    import sphdl.gui as G  # noqa: F401  （确保 gui 能导入，窗口依赖它）
    from sphdl.douyin_ui import DouyinWindow

    cdn = _CDN(size=500 * 1024)
    root = tk.Tk()
    root.withdraw()
    win = None
    orig_dl = DY.download
    try:
        app = _FakeApp(root, out_dir)
        win = DouyinWindow(app)
        for _ in range(10):
            root.update_idletasks()
            root.update()
            time.sleep(0.02)

        # 只留一条待下载，勾上
        for iid in list(win.tree.get_children()):
            win.tree.delete(iid)
        win.videos.clear()
        win._rows.clear()
        win._meta.clear()

        from sphdl.douyin import DouyinVideo

        stale = DouyinVideo(aweme_id="444", title="过期链接",
                            play_url="http://127.0.0.1:1/dead.mp4", size=len(cdn.data))
        fresh = DouyinVideo(aweme_id="444", title="过期链接",
                            play_url=cdn.url(), size=len(cdn.data))
        idx = 0
        win.videos.append((f"https://www.douyin.com/video/444", stale))
        iid = win.tree.insert("", "end", values=(
            "☑", "过期链接", "作者", "0:30", "480 KB", "待下载"))
        win._rows[iid] = idx
        win._set_meta(idx, "pending")

        calls = {"n": 0}
        real_dl = orig_dl

        def fake_download(video, dest, on_progress=None, should_stop=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return False, "HTTP 403（地址可能已过期，断点已保留）"
            return real_dl(video, dest, on_progress=on_progress,
                           should_stop=should_stop)

        DY.download = fake_download
        win._re_resolve = lambda v: fresh

        import tkinter.messagebox as MB
        orig_yes = MB.askyesno
        MB.askyesno = lambda *a, **k: True
        try:
            win._on_download()
            deadline = time.time() + 40
            got = None
            while time.time() < deadline:
                root.update_idletasks()
                root.update()
                try:
                    ev = app.ui_q.get(timeout=0.2)
                except Exception:
                    continue
                # ★ 按真实 GUI 的分发方式来投递（gui._dispatch 就是这个映射），
                #   否则只是把事件取出来看一眼，窗口里的状态行当然不会变。
                if ev[0] == "dy_dl_start":
                    win.apply_dl_start(ev[1], ev[2], ev[3])
                elif ev[0] == "dy_dl_prog":
                    win.apply_dl_prog(ev[1], ev[2], ev[3], ev[4], ev[5])
                elif ev[0] == "dy_dl_done":
                    win.apply_dl_done(ev[1], ev[2], ev[3], ev[4], ev[5])
                elif ev[0] == "dy_all_done":
                    got = ev
                    break
        finally:
            MB.askyesno = orig_yes

        check("下载线程正常收尾（未卡死）", got is not None, str(got))
        check("失败→重解析→再下一次成功",
              got is not None and got[1] == 1 and got[2] == 0, str(got))
        mp4s = list((out_dir / "抖音").glob("*.mp4"))
        check("抖音目录里生成了 mp4",
              len(mp4s) == 1 and mp4s[0].stat().st_size == len(cdn.data),
              str([m.name for m in mp4s]))
        check("状态标记为已完成",
              win._meta_of(0)[0] == "done", str(win._meta_of(0)))
        check("已下载内容逐字节正确",
              bool(mp4s) and hashlib.sha256(mp4s[0].read_bytes()).hexdigest()
              == hashlib.sha256(cdn.data).hexdigest())
    finally:
        DY.download = orig_dl
        cdn.stop()
        try:
            if win is not None:
                win.win.destroy()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("=" * 68)
    print("v3.1.5 验证：续传 / 清单持久化 / 点赞数进文件名")
    print("临时目录:", _TMP)
    print("=" * 68)
    test_store()
    test_douyin_partial()
    test_douyin_download()
    test_mpd_resume()
    test_pipeline_names()
    test_pipeline_resume_across_restart()
    test_douyin_part_name_fallback()
    test_douyin_window_restore()

    print("\n" + "=" * 68)
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   ·", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
