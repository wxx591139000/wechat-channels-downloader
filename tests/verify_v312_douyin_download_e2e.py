"""v3.1.2 验证：抖音主页批量 —— 「勾选 → 下载 → 落盘」全链路（真实网络 + 真实窗口）

═══ 为什么要有这个脚本 ═══
v3.1.1 只验证到「抓到 84 条列表」+「play_url 能 HTTP 200 取回前 4KB」。
真正的下载（写盘 / 音轨 / 文件名 / 表格状态）从来没有端到端跑过 ——
这正是客户最可能踩坑的地方。

═══ 验证方式（不造假）═══
· 用**真实** `DouyinWindow`（不是 mock 类），只在它外面套一个最小 app 外壳
· 真实抓主页（可见浏览器 + 自动滚）
· 真实走 `apply_profile_batch` → 表格
· 真实调 `_on_download()`（只把 messagebox 换成自动确认，其余一行不改）
· 真实下载（挑列表里**最小**的那条，避免动辄 500MB）
· 用 ffprobe 验：有视频流 / 有音频流 / 时长对得上
· 用 ffmpeg 实解前 5 秒（"能播放"）+ 文件名标题对得上

判定项：
  A1 主页抓取拿到作品
  A2 主页条目自带 play_url（不需要再 resolve）
  B1 表格行数 == 抓到条数
  B2 全部勾选（☑）
  B3 parse_done 统计不是 0/N
  B4 「全选」→ _picked() 覆盖全部（客户点全选不会漏条）
  B5 首行标题/时长/大小已填（不是占位符）
  C1 收到 dy_all_done
  C2 文件真的落盘（无 .part 残留）
  C3 文件大小与元数据一致
  C4 有视频流
  C5 ★ 有音频流（"有声音"）
  C6 时长与元数据一致
  C7 能解码播放（ffmpeg 实测前 5 秒）
  C8 文件名 == 标题 + aweme_id
  D1 表格状态变「✅ 已完成」
  D2 汇总提示成功数正确
"""
import json
import queue
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULT = []


def check(name, cond, detail=""):
    RESULT.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""), flush=True)


SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"
URL = f"https://www.douyin.com/user/{SEC}"

TMP = Path(tempfile.mkdtemp(prefix="dy_dl_e2e_"))
DL_BASE = TMP / "dl"          # DouyinWindow._dir() = dir_var + "/抖音"
DEST = DL_BASE / "抖音"
LOGS = []


def log(lv, m):
    LOGS.append((lv, str(m)))
    print(f"    [{lv}] {m}", flush=True)


from sphdl.douyin_profile_scroll import fetch_profile_by_scroll  # noqa: E402
import tkinter as tk                                            # noqa: E402
import sphdl.douyin_ui as DYUI                                  # noqa: E402

print("=" * 78)
print("① 真实抓取主页")
print("=" * 78, flush=True)
t0 = time.time()
res = fetch_profile_by_scroll(URL, max_items=200, visible=True,
                              auto_scroll=True, on_log=log)
print(f"  抓到 {len(res.items)} 条 / 耗时 {time.time()-t0:.1f}s / "
      f"说明={res.note} / 错误={res.error or '无'}", flush=True)
check("A1 主页抓取拿到作品", len(res.items) > 0, f"{len(res.items)} 条")
with_url = [v for v in res.items if getattr(v, "play_url", "")]
check("A2 主页条目自带 play_url（不必再 resolve）",
      len(with_url) == len(res.items) and bool(res.items),
      f"{len(with_url)}/{len(res.items)}")


class FakeApp:
    """只补 DouyinWindow 真正用到的那几样东西，其余一律不造假。"""

    def __init__(self, root):
        self.root = root
        self.dir_var = tk.StringVar(master=root, value=str(DL_BASE))
        self.ui_q = queue.Queue()

    def push_log(self, lv, msg):
        log(lv, msg)


root = tk.Tk()
root.withdraw()
app = FakeApp(root)
w = DYUI.DouyinWindow(app)
w.win.withdraw()

print()
print("=" * 78)
print("② 主页条目铺进真实表格（apply_profile_batch）")
print("=" * 78, flush=True)
app.ui_q.put(("dy_profile_batch", res.items))
app.ui_q.put(("dy_parse_done", len(res.items)))


def dispatch(item):
    k = item[0]
    if k == "dy_profile_batch":
        w.apply_profile_batch(item[1])
    elif k == "dy_parse_done":
        w.parse_done(item[1])
    elif k == "dy_dl_start":
        w.apply_dl_start(item[1], item[2], item[3])
    elif k == "dy_dl_prog":
        w.apply_dl_prog(item[1], item[2], item[3], item[4], item[5])
    elif k == "dy_dl_done":
        w.apply_dl_done(item[1], item[2], item[3], item[4], item[5])
    elif k == "dy_all_done":
        w.apply_all_done(item[1], item[2], item[3])
    elif k == "dy_profile_progress":
        try:
            w.lbl_stat.config(text=item[1])
        except Exception:
            pass


def pump(deadline, stop_flag=None):
    while time.time() < deadline:
        got = False
        while True:
            try:
                item = app.ui_q.get_nowait()
            except queue.Empty:
                break
            got = True
            dispatch(item)
            if stop_flag is not None and item[0] == stop_flag:
                return True
        root.update()
        if not got:
            time.sleep(0.03)
    return False


pump(time.time() + 5)

rows = w.tree.get_children()
check("B1 表格行数 == 抓到条数", len(rows) == len(res.items) and len(rows) > 0,
      f"表格 {len(rows)} 行 / 抓到 {len(res.items)} 条")
check("B2 全部勾选（☑）",
      bool(rows) and all(w.tree.item(i, "values")[0] == "☑" for i in rows),
      f"{sum(1 for i in rows if w.tree.item(i, 'values')[0] == '☑')}/{len(rows)}")
ok_n = sum(1 for _, v in w.videos if v and v.ok)
check("B3 parse_done 统计不是 0/N", ok_n > 0, w.lbl_count.cget("text"))
picked = w._picked()
check("B4 全选后 _picked() 覆盖全部", len(picked) == len(rows) and bool(rows),
      f"_picked={len(picked)} / 表格={len(rows)}")
if rows:
    vals = w.tree.item(rows[0], "values")
    print(f"    首行：{vals}", flush=True)
    check("B5 首行标题不是占位符",
          bool(vals[1]) and "待解析" not in str(vals[1]), str(vals[1])[:30])

# 只留最小的 N 条勾选（其余取消）→ 真实下载不必等几百 MB
#   E2E_N 环境变量控制条数（默认 1；设为 3 可验证"多条连续下载"）
import os as _os
N_ITEMS = max(1, int(_os.environ.get("E2E_N", "1")))
_pool = sorted([(i, v) for i, (_, v) in enumerate(w.videos) if v and v.ok],
               key=lambda t: t[1].size or 10 ** 12)[:N_ITEMS]
keep_idx = {i for i, _ in _pool}
small_i = _pool[0][0]
for iid in rows:
    vv = list(w.tree.item(iid, "values"))
    vv[0] = "☑" if w._rows.get(iid) in keep_idx else "☐"
    w.tree.item(iid, values=vv)

print()
print("=" * 78)
print("③ 真实下载（走 _on_download，只把弹窗换成自动确认）")
print("=" * 78, flush=True)
sel = w._picked()
for i, v in sel:
    print(f"  勾选 [{i}] {v.title[:36]}  {v.duration_text}  "
          f"{(v.size or 0)/1048576:.1f} MB", flush=True)

DYUI.messagebox.askyesno = lambda *a, **k: True
DYUI.messagebox.showinfo = lambda *a, **k: None

done_flag = {"hit": False}
orig_all = w.apply_all_done


def _all_done(done, failed, total):
    done_flag["hit"] = True
    orig_all(done, failed, total)


w.apply_all_done = _all_done

DEST.mkdir(parents=True, exist_ok=True)
t0 = time.time()
w._on_download()
pump(time.time() + 600, stop_flag="dy_all_done")
print(f"  下载结束 耗时 {time.time()-t0:.0f}s  状态栏={w.lbl_stat.cget('text')}",
      flush=True)
check("C1 收到 dy_all_done", done_flag["hit"])

files = sorted(DEST.glob("*.mp4"))
parts = list(DEST.glob("*.part")) + list(DEST.glob("*.audio.part"))
for f in files:
    print(f"    {f.name}  {f.stat().st_size/1048576:.2f} MB", flush=True)
check("C2 文件真的落盘（且无 .part 残留）",
      len(files) == len(sel) and len(sel) > 0 and not parts,
      f"文件 {len(files)} / 勾选 {len(sel)} / part {len(parts)}")

print()
print("=" * 78)
print("④ 文件实测（ffprobe 流信息 + ffmpeg 解码 + 文件名）")
print("=" * 78, flush=True)


def ffprobe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(p)],
                       capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout or "{}")


def can_decode(p, sec=5):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "3", "-t", str(sec),
                        "-i", str(p), "-f", "null", "-"],
                       capture_output=True, text=True, timeout=180)
    err = (r.stderr or "").strip()
    return r.returncode == 0 and len(err) < 200, err[:100]


from sphdl.douyin import safe_name      # noqa: E402

for f in files:
    m = ffprobe(f)
    kinds = [s.get("codec_type") for s in m.get("streams", [])]
    dur = float(m.get("format", {}).get("duration") or 0)
    owner = next((v[1] for v in w.videos if v[1] and v[1].aweme_id
                  and v[1].aweme_id in f.stem), None)
    print(f"  · {f.name[:44]:46s} {f.stat().st_size/1048576:7.2f} MB  "
          f"{dur:7.1f}s  {kinds}", flush=True)
    if owner:
        expect = safe_name(f"{owner.title or owner.aweme_id}_{owner.aweme_id}")
        check("C8 文件名 == 标题 + aweme_id", f.stem == expect,
              f"实际={f.stem[:44]}")
        check("C3 大小与元数据一致",
              abs(f.stat().st_size - owner.size) <= max(8192, owner.size * 0.02),
              f"文件 {f.stat().st_size} / 元数据 {owner.size}")
        if owner.duration_ms:
            exp_s = owner.duration_ms / 1000.0
            check("C6 时长与元数据一致", abs(dur - exp_s) <= 3.0,
                  f"文件 {dur:.1f}s / 元数据 {exp_s:.1f}s")
    check("C4 有视频流", "video" in kinds, str(kinds))
    check("C5 ★ 有音频流（有声音）", "audio" in kinds, str(kinds))
    dec, err = can_decode(f)
    check("C7 能解码播放（ffmpeg 实解 5 秒）", dec, err)

print()
print("=" * 78)
print("⑤ 表格状态回写")
print("=" * 78, flush=True)
stat_of = {}
for iid in w.tree.get_children():
    idx = w._rows.get(iid)
    if idx in keep_idx:
        stat_of[idx] = w.tree.item(iid, "values")[5]
print(f"  下载行状态：{stat_of}", flush=True)
check("D1 下载过的行状态变「✅ 已完成」",
      bool(stat_of) and all("已完成" in s for s in stat_of.values()),
      str(list(stat_of.values())))
fin = w.lbl_stat.cget("text")
check("D2 汇总提示成功数正确", f"成功 {len(sel)}" in fin, fin)

print()
print("=" * 78)
bad = [r for r in RESULT if not r[1]]
print(f"合计 {len(RESULT)} 项，通过 {len(RESULT)-len(bad)}，失败 {len(bad)}")
for name, _, d in bad:
    print(f"  [X] {name}  {d}")
print(f"临时目录：{TMP}")
print("=" * 78, flush=True)

try:
    w.win.destroy()
    root.destroy()
except Exception:
    pass

sys.exit(0 if not bad else 1)
