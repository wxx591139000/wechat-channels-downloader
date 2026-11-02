"""探针：真实下载 1 条，打印速度/HTTP 状态/卡住的位置。

目的：v3.1.1 的「勾选→下载→落盘」从没端到端验证过。
先拿到真实数据（速度、是否 403、是否有音频），再决定修什么。
"""
import sys
import time
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sphdl.douyin_profile_scroll import fetch_profile_by_scroll  # noqa: E402
from sphdl.douyin import UA, download                          # noqa: E402

SEC = "MS4wLjABAAAAAgTdaUkW9HoblheZJ-W44LQ-5YkSC2pbNbssIgKa8tg"
URL = f"https://www.douyin.com/user/{SEC}"

print("① 抓主页（12 条，取最小的那条来测）", flush=True)
res = fetch_profile_by_scroll(URL, max_items=12, visible=True,
                              auto_scroll=True, on_log=lambda lv, m: None)
print(f"   抓到 {len(res.items)} 条", flush=True)
for v in res.items[:12]:
    print(f"   {(v.size or 0)/1048576:8.1f} MB  {v.duration_text:>8s}  "
          f"{v.title[:28]}", flush=True)

cand = min([v for v in res.items if v.ok], key=lambda v: v.size or 10**12)
print(f"\n② 选中最小：{(cand.size or 0)/1048576:.1f} MB  "
      f"{cand.duration_text}  {cand.title[:30]}", flush=True)
print(f"   aweme_id={cand.aweme_id}  audio_url={'有' if cand.audio_url else '无'}",
      flush=True)

print("\n③ 原始 HTTP 观测（前 2MB）", flush=True)
req = urllib.request.Request(cand.play_url,
                             headers={"User-Agent": UA,
                                      "Referer": "https://www.douyin.com/"})
try:
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"   HTTP {r.status}  content-type={r.headers.get('content-type')}"
              f"  content-length={r.headers.get('content-length')}", flush=True)
        got = 0
        while got < 2 * 1048576:
            c = r.read(262144)
            if not c:
                break
            got += len(c)
        dt = time.time() - t0
        print(f"   读了 {got/1048576:.1f} MB 用 {dt:.1f}s → "
              f"{got/1048576/dt:.2f} MB/s", flush=True)
except Exception as e:                                      # noqa: BLE001
    print(f"   ❌ {type(e).__name__}: {e}", flush=True)

print("\n④ 走生产代码 download()", flush=True)
dest = Path(tempfile.mkdtemp(prefix="dl_probe_"))
last = [0.0]
t0 = time.time()


def prog(got, tot, dt):
    if time.time() - last[0] < 2:
        return
    last[0] = time.time()
    pct = got * 100 / tot if tot else 0
    print(f"   {got/1048576:7.1f} / {tot/1048576:7.1f} MB  {pct:5.1f}%  "
          f"{got/1048576/max(dt,0.01):5.2f} MB/s", flush=True)


ok, info = download(cand, dest, on_progress=prog)
print(f"\n   返回 ok={ok}  info={info}", flush=True)
print(f"   总耗时 {time.time()-t0:.0f}s", flush=True)
for f in sorted(dest.iterdir()):
    print(f"   {f.name}  {f.stat().st_size/1048576:.2f} MB", flush=True)
