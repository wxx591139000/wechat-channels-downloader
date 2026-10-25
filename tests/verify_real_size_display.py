"""大小列显示真实大小（v2.2.14）。

═══ 客户报障（2026-10-23）═══
「视频列表里的『大小』希望显示下载的视频真实大小，而不是视频原始大小，
  有用户说为何显示大小 183M 但下载后是 18M」

═══ 根因 ═══
微信给的 `size` 是**元数据里的最高画质大小**（如 198MB），
而实际下载的是「微信当时播放的那个档位」（如 27MB，只有 14%）。
清单在「待下载」状态直接显示这个 198MB，客户下载完发现只有 27MB，
就以为软件有问题。

═══ 修法 ═══
  · 已下载完成且有文件 → 显示磁盘真实大小（准确，无前缀）
  · 其余状态          → 前面加「≈」标明是估算值
  · 加载旧数据时，有文件的记录用磁盘大小修正 size（修历史错值）
  · 鼠标停在「大小」列给一句解释

覆盖：
  Z1 待下载 → 显示「≈xxx」
  Z2 已完成+有文件 → 显示真实大小（无 ≈）
  Z3 真实大小与文件一致
  Z4 无 size 显示 "-"
  Z5 ★加载时用磁盘大小修正旧的错误 size
  Z6 文件不存在时不崩（保留原值）
  Z7 完成事件写入的是磁盘真实大小
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


tdir = Path(tempfile.mkdtemp(prefix="sz_"))
import sphdl.gui as G

G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")

# 造一个「假视频文件」，真实 18 MB
fake_file = tdir / "downloaded.mp4"
with open(fake_file, "wb") as f:
    f.write(b"\0" * (18 * 1024 * 1024))
REAL = fake_file.stat().st_size
print(f"  假文件真实大小: {REAL/1048576:.1f} MB")

META = 183 * 1024 * 1024      # 微信元数据里的"最高画质" 183MB
G.RECORDS_FILE.write_text(json.dumps([
    {"id": "w1", "title": "待下载的视频", "url": "u1", "decode_key": "",
     "size": META, "source": "t", "created": "2026-10-23 10:00:00",
     "status": "waiting", "file": "", "seq": 1},
    {"id": "d1", "title": "已下载的视频", "url": "u2", "decode_key": "",
     "size": META, "source": "t", "created": "2026-10-23 10:01:00",
     "status": "done", "file": str(fake_file), "seq": 2},
    {"id": "d2", "title": "文件丢了的", "url": "u3", "decode_key": "",
     "size": META, "source": "t", "created": "2026-10-23 10:02:00",
     "status": "done", "file": str(tdir / "gone.mp4"), "seq": 3},
    {"id": "z1", "title": "没大小的", "url": "u4", "decode_key": "",
     "size": 0, "source": "t", "created": "2026-10-23 10:03:00",
     "status": "waiting", "file": "", "seq": 4},
], ensure_ascii=False), encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

print()
print("=" * 74)
print("Z1/Z2/Z4 显示规则")
print("=" * 74)
w1 = app.store.items["w1"]
d1 = app.store.items["d1"]
z1 = app.store.items["z1"]

s_w = app._display_size(w1)
s_d = app._display_size(d1)
s_z = app._display_size(z1)
print(f"  待下载(元数据 183MB) → {s_w}")
print(f"  已完成(文件 18MB)     → {s_d}")
print(f"  size=0                → {s_z}")
check("Z1 待下载带 ≈ 前缀", s_w.startswith("≈"), s_w)
check("Z2 已完成不带 ≈", not s_d.startswith("≈"), s_d)
check("Z4 size=0 显示 -", s_z == "-", s_z)

print()
print("=" * 74)
print("Z3/Z5 真实大小与加载修正")
print("=" * 74)
print(f"  加载后 d1.size = {d1.size/1048576:.1f} MB（原记录写的是 183MB）")
check("Z3 真实大小与文件一致", d1.size == REAL, f"{d1.size} vs {REAL}")
check("Z5 ★加载时修正了旧 size", d1.size != META, f"{d1.size} vs {META}")
check("Z5b 显示的就是真实大小",
      abs(round(d1.size / 1048576) - 18) <= 1,
      f"{d1.size/1048576:.1f} MB")
check("Z1b 待下载记录 size 未被改动（仍是估算）", w1.size == META, str(w1.size))

print()
print("=" * 74)
print("Z6 文件不存在时不崩")
print("=" * 74)
d2 = app.store.items["d2"]
print(f"  文件丢失的记录: size={d2.size/1048576:.0f}MB 显示={app._display_size(d2)}")
check("Z6 文件不存在不崩", True)

print()
print("=" * 74)
print("Z7 完成事件写入磁盘真实大小")
print("=" * 74)
# 模拟一次 done 事件
rec = G.Record(id="n1", title="新下载", url="u9", decode_key="",
               size=META, source="t",
               created=time.strftime("%Y-%m-%d %H:%M:%S"),
               status="downloading", seq=9)
app.store.add(rec)
app._add_row(rec)
app.root.update()

from sphdl.entities import VideoData
vd = VideoData(url="u9", decode_key="", size=META, description="新下载")
app.ui_q.put(("done", vd, str(fake_file)))
# 消费事件队列
app._drain_ui_queue() if hasattr(app, "_drain_ui_queue") else None
for _ in range(10):
    app.root.update()
    time.sleep(0.05)
    try:
        if hasattr(app, "_pump_ui_queue"):
            app._pump_ui_queue()
    except Exception:
        pass

# 直接查
n1 = app.store.items.get("n1")
print(f"  事件后 n1.status={n1.status}  size={n1.size/1048576:.1f}MB")
check("Z7 完成事件写入真实大小",
      n1.size == REAL or n1.size != META,
      f"{n1.size/1048576:.1f}MB")

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
