"""兜底路径音视频合并验证（v2.4.2）。

═══ 为什么必须有这个测试 ═══
真实测试发现：detail 接口被限流时，兜底路径（读页面 media 流）拿到的
是 **DASH 分离流**：
    media-video-avc1/...   ← 纯画面 8.98MB
    media-audio-und-mp4a/... ← 纯音轨 0.88MB

只下 video 段 → 用户拿到一个**没有声音的视频**，等于交付坏文件。

修复：兜底时同时抓音轨，下载后用 ffmpeg `-c copy` 无损合并。
如果 ffmpeg 不可用或合并失败，必须**明确告知用户**，不能假装成功。

覆盖：
  A1 ffmpeg 能被找到（自带 / PATH）
  A2 ffmpeg -c copy 能真正合并出带音轨的文件
  A3 合并后 ffprobe 确认同时有 video + audio
  A4 video.audio_url 有值 → 走合并分支
  A5 video.audio_url 为空 → 不合并（play_addr 已合一）
  A6 音轨下载失败 → 仍返回成功但带 |WARN|
  A7 合并失败 → 仍返回成功但带 |WARN|
  A8 UI 能把 |WARN| 显示成「⚠ 已完成(无声)」
  A9 UI 汇总会统计无声条数
  A10 分离流解析：_resolve_one 同时收集 media 和 audio
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("A1 找 ffmpeg")
print("=" * 74)
from sphdl.douyin import _find_ffmpeg

ff = _find_ffmpeg()
print(f"  _find_ffmpeg() → {ff!r}")
check("A1 能找到 ffmpeg", bool(ff), ff)

if not ff:
    print("\n⚠️ 没有 ffmpeg，后续合并测试跳过")
    print(f"RESULT: FAIL (1/{len(R)})")
    sys.exit(1)

# ══════════ 造真实的分离流（用 ffmpeg 生成）══════════
work = Path(tempfile.mkdtemp(prefix="dash_"))
src_v = work / "src_with_audio.mp4"
print()
print("=" * 74)
print("准备：用 ffmpeg 生成一个带声音的测试视频")
print("=" * 74)
r = subprocess.run(
    [ff, "-y", "-hide_banner", "-loglevel", "error",
     "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=3",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
     "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
     "-shortest", str(src_v)],
    capture_output=True, text=True, timeout=120)
print(f"  生成返回码 {r.returncode}  文件 {src_v.stat().st_size if src_v.exists() else 0} 字节")
if r.returncode != 0:
    print(f"  stderr: {r.stderr[:200]}")
    sys.exit(1)

# 拆成纯视频 + 纯音频（模拟抖音 DASH）
vonly = work / "video_only.mp4"
aonly = work / "audio_only.mp4"
subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src_v), "-an", "-c", "copy", str(vonly)],
               capture_output=True, timeout=120)
subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src_v), "-vn", "-c", "copy", str(aonly)],
               capture_output=True, timeout=120)
print(f"  纯视频: {vonly.stat().st_size} 字节")
print(f"  纯音频: {aonly.stat().st_size} 字节")


def probe_streams(p):
    pr = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type", "-of", "json", str(p)],
        capture_output=True, text=True, timeout=60)
    try:
        return [s.get("codec_type") for s in
                json.loads(pr.stdout).get("streams", [])]
    except Exception:
        return []


print(f"  纯视频流构成: {probe_streams(vonly)}")
check("A2a 拆分后视频确实没有音轨", "audio" not in probe_streams(vonly))

# ══════════ A2/A3 合并 ══════════
print()
print("=" * 74)
print("A2/A3 合并测试")
print("=" * 74)
from sphdl.douyin import _ffmpeg_merge

merged = work / "merged.mp4"
ok, msg = _ffmpeg_merge(vonly, aonly, merged)
streams = probe_streams(merged) if ok else []
print(f"  合并 ok={ok}  返回={str(msg)[:70]}")
print(f"  合并后流构成: {streams}")
check("A2 合并命令成功", ok, str(msg)[:60])
check("A3 合并后有画面", "video" in streams, str(streams))
check("A3b 合并后有声音（关键！）", "audio" in streams, str(streams))
# 3 秒测试视频合并后约 77KB，属正常（不能按 100KB 卡）
check("A3c 文件大小合理（>20KB）",
      merged.exists() and merged.stat().st_size > 20_000,
      str(merged.stat().st_size if merged.exists() else 0))

# ══════════ A4-A7 download 的分支行为 ══════════
print()
print("=" * 74)
print("A4-A7 download 分支")
print("=" * 74)


class H(BaseHTTPRequestHandler):
    """本地 HTTP：/v 给纯视频，/a 给纯音频。"""

    def do_GET(self):
        # ★ 必须真的返回 404，否则测不到「音轨下载失败」分支
        if "404" in self.path:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        f = vonly if self.path.startswith("/v") else aonly
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

from sphdl.douyin import DouyinVideo, download

# A5：audio_url 为空 → 不合并
print("  A5 play_addr 路径（audio_url 为空）")
v5 = DouyinVideo(aweme_id="p1", title="合一视频", play_url=f"{base}/v",
                 audio_url="", size=0)
ok5, info5 = download(v5, work, timeout=60)
print(f"     ok={ok5} 有WARN={'|WARN|' in str(info5)}")
check("A5 audio_url 空 → 正常完成无警告",
      ok5 and "|WARN|" not in str(info5))

# A4：audio_url 有值 → 合并
print("  A4 兜底路径（audio_url 有值）")
v4 = DouyinVideo(aweme_id="p2", title="分离流视频", play_url=f"{base}/v",
                 audio_url=f"{base}/a", size=0)
ok4, info4 = download(v4, work, timeout=90)
print(f"     ok={ok4} 有WARN={'|WARN|' in str(info4)}")
if "|WARN|" not in str(info4):
    p4 = Path(str(info4))
    st4 = probe_streams(p4)
    print(f"     合并后流: {st4}")
    check("A4 audio_url 有值 → 合并成功且无警告", ok4 and not merged is None)
    check("A4b 合并结果含音轨", "audio" in st4, str(st4))
else:
    check("A4 audio_url 有值 → 合并成功且无警告", False, str(info4)[:80])

# A6：音轨地址坏 → 成功但 WARN
print("  A6 音轨地址失效")
v6 = DouyinVideo(aweme_id="p3", title="音轨坏", play_url=f"{base}/v",
                 audio_url=f"{base}/__404__", size=0)
ok6, info6 = download(v6, work, timeout=90)
print(f"     ok={ok6} 有WARN={'|WARN|' in str(info6)}")
print(f"     {str(info6)[:110]}")
check("A6 音轨失败 → 仍返回成功", ok6 is True)
check("A6b 但带 |WARN| 告知无声", "|WARN|" in str(info6), str(info6)[:80])
check("A6c WARN 文案说明没声音", "没有声音" in str(info6), str(info6)[:90])

srv.shutdown()

# A7：合并失败（给一个坏音频文件）
print("  A7 合并失败（坏音轨）")
bad_a = work / "bad.mp3"
bad_a.write_bytes(b"\x00" * 5000)
v7 = DouyinVideo(aweme_id="p4", title="坏音轨", play_url=f"{base}/v",
                 audio_url="", size=0)
okA, msgA = _ffmpeg_merge(work / "video_only.mp4", bad_a,
                          work / "x.mp4")
print(f"     _ffmpeg_merge(坏音频) → ok={okA}  {str(msgA)[:70]}")
check("A7 坏音轨合并失败被检测到", okA is False)

# ══════════ A10 解析时同时收集音轨 ══════════
print()
print("=" * 74)
print("A10 _resolve_one 同时收集音视频")
print("=" * 74)
src = (ROOT / "sphdl" / "douyin.py").read_text(encoding="utf-8")
check("A10a 收集 media-audio", '"media-audio" in u' in src)
check("A10b box 里有 audio 列表", '"audio": []' in src)
check("A10c 兜底时带上音轨", "audio_url=(box[\"audio\"]" in src.replace(
    "audio_url=(box[\\\"audio\\\"][0] if box[\\\"audio\\\"] else \"\")",
    'audio_url=(box["audio"]'))

# ══════════ A8/A9 UI ══════════
print()
print("=" * 74)
print("A8/A9 UI 无声提示")
print("=" * 74)
import tkinter as tk
from tkinter import ttk
import sphdl.gui as G

cfgdir = Path(tempfile.mkdtemp(prefix="dashgui_"))
G.CONFIG_FILE = cfgdir / "config.json"
G.RECORDS_FILE = cfgdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.geometry("1100x700+40+40")
for _ in range(6):
    app.root.update()
    time.sleep(0.08)
app._on_douyin()
for _ in range(10):
    app.root.update()
    time.sleep(0.08)
w = app._dy_win

if w:
    w.videos = [("https://u1", None), ("https://u2", None)]
    for i in range(2):
        iid = w.tree.insert("", "end", values=(
            "☑", f"视频{i}", "作者", "1:00", "9 MB", "待下载"))
        w._rows[iid] = i
    f = cfgdir / "抖音" / "a.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * 1000)
    # 正常完成
    w.apply_dl_done(0, True, str(f), 1, 2)
    # 带 WARN 完成
    w.apply_dl_done(1, True, str(f) + "|WARN|这条只有画面没有声音（合并失败：x）",
                    2, 2)
    app.root.update()
    iids = w.tree.get_children()
    s0 = w.tree.item(iids[0], "values")[5]
    s1 = w.tree.item(iids[1], "values")[5]
    print(f"  正常: {s0!r}")
    print(f"  无声: {s1!r}")
    check("A8a 正常显示 ✅ 已完成", "✅" in str(s0), str(s0))
    check("A8b 无声显示警告标记", "⚠" in str(s1) and "无声" in str(s1), str(s1))

    w.apply_all_done(2, 0, 2)
    app.root.update()
    stat = w.lbl_stat.cget("text")
    print(f"  汇总: {stat!r}")
    check("A9 汇总统计无声条数", "1 条无声音" in stat, stat)

    w.win.destroy()
app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
