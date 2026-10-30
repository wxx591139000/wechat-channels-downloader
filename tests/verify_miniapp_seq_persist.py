"""小程序短剧：集号跨会话持久化 + 自动跳集检测（v2.8.6）。

═══ 客户反馈（2026-10-30，v2.8.2）═══
① 「视频是从第 27 集开始播放的，但看视频清单里显示的是 1、2、3 集」
② 「并没有自动翻下一集」

═══ 铁证（客户真实文件时间）═══
```
第01集_8f4700bb.mp4  14:39:24
第02集_0c7d73f1.mp4  14:39:27  （距上一集 2 秒）
第03集_747532af.mp4  14:40:28  （距上一集 60 秒）  ← 一集约 45 秒，这是自动跳
第04集_00ea4f61.mp4  14:43:23  （距上一集 175 秒） ← 超过一集时长，是手动点的
第04 之后: 无
```
→ **自动跳集其实生效了（02→03）**，只是没标记出来，且第 04 之后停了。

═══ 两个 bug 的根因 ═══
① `seq = _miniapp_saved + 1`，每次启动归零 → 从第 27 集播也写「第01集」，
   而且与旧文件重名 → 旧文件被跳过 → 看起来"没抓到"。
② 没有任何「这是自动跳还是手动点」的反馈，客户无从判断。

═══ 本版改动 ═══
① 集号跨会话持久化：`.seq.json` + 扫描已有「第NN集_」文件名取最大值兜底；
   同一集哈希复用原序号（幂等，不重复不倒退）
② 保存后按时间间隔判断并提示「自动连播中」/「可能是手动选集」
③ 明确说明：小程序 URL 只有哈希、不含真实剧集号，
   **拿不到「第27集」这个数字**，只能"接着上次继续编号"

覆盖：
S1  ★首次保存 → 第01集
S2  ★第二次启动（新实例）→ 接着上次编号（不回到 01）
S3  ★已有文件 第05..第07 → 新实例从 第08 开始
S4  ★同一集哈希复用 → 序号不变、不新建文件
S5  .seq.json 被删 → 仍能从文件名扫出最大序号兜底
S6  ★自动跳集（间隔≤180s）提示"自动连播中"
S7  ★手动换集（间隔>180s）提示"可能是手动选集"
S8  日志明确说明拿不到真实集号
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl import config
from sphdl.addon import SphAddon


class FakeResp:
    def __init__(self, status, body=b""):
        self.status_code = status
        self.content = body
        self.headers = {}


class FakeFlow:
    def __init__(self, ep):
        self.response = FakeResp(200, b"X" * 2000)
        self.request = type("Q", (), {
            "path": f"/vod/{ep}/f0.mp4?t=1", "host": "x.wxamedia.com"})()


def new_host(td):
    """模拟"新启动一个程序实例"（所有内存状态都是初始值）。"""
    h = type("H", (), {})()
    h._miniapp_saved = 0
    h.miniapp_saved_total = 0
    h.out_dir = str(td)
    h.on_miniapp = None
    h._mp_names = None
    h._mp_seq = 0
    h._mp_names_loaded = False
    h._mp_scanned = False
    h._mp_prev_ts = None
    return h


print("=" * 74)
print("S1 首次保存")
print("=" * 74)
td = Path(tempfile.mkdtemp(prefix="seq_"))
config.DOWNLOAD_DIR = td
h1 = new_host(td)
SphAddon._save_miniapp_media(h1, FakeFlow("aaaa111122223333444455"))
files = sorted(td.rglob("*.mp4"))
print(f"  产出: {[f.name for f in files]}")
check("S1 ★首次保存 = 第01集",
      files and files[0].name.startswith("第01集_"),
      files[0].name if files else "-")

print()
print("=" * 74)
print("S2 ★第二次启动（模拟重启）→ 接着编号")
print("=" * 74)
h2 = new_host(td)      # 全新实例，内存状态归零
SphAddon._save_miniapp_media(h2, FakeFlow("bbbb111122223333444455"))
files = sorted(td.rglob("*.mp4"))
names = [f.name for f in files]
print(f"  产出: {names}")
check("S2a ★重启后是第02集（不是又回到01）",
      any(n.startswith("第02集_") for n in names), str(names))
check("S2b 没有覆盖第01集", any(n.startswith("第01集_") for n in names))

print()
print("=" * 74)
print("S3 ★已有 第05..第07 文件 → 新实例从 08 开始")
print("=" * 74)
td3 = Path(tempfile.mkdtemp(prefix="seq3_"))
config.DOWNLOAD_DIR = td3
sub = td3 / "小程序短剧"
sub.mkdir(parents=True, exist_ok=True)
for n in (5, 6, 7):
    (sub / f"第{n:02d}集_oldhash{n}.mp4").write_bytes(b"O" * 1500)
h3 = new_host(td3)
SphAddon._save_miniapp_media(h3, FakeFlow("cccc111122223333444455"))
names3 = sorted(f.name for f in sub.glob("*.mp4"))
print(f"  目录内容: {names3}")
check("S3 ★新集是第08集（接着已有的最大序号）",
      any(n.startswith("第08集_") for n in names3), str(names3))

print()
print("=" * 74)
print("S4 ★同一集哈希复用 → 序号不变、不新建")
print("=" * 74)
before = len(list(sub.glob("*.mp4")))
SphAddon._save_miniapp_media(h3, FakeFlow("cccc111122223333444455"))
after = len(list(sub.glob("*.mp4")))
print(f"  文件数: {before} → {after}")
check("S4 同集不新建文件", before == after, f"{before}→{after}")
check("S4b 序号未倒退（仍是第08集）",
      any(f.name.startswith("第08集_cccc1111") for f in sub.glob("*.mp4")))

print()
print("=" * 74)
print("S5 ★.seq.json 被删 → 从文件名扫出兜底")
print("=" * 74)
seqf = sub / ".seq.json"
existed = seqf.exists()
if existed:
    seqf.unlink()
print(f"  .seq.json 存在过: {existed}  删除后: {seqf.exists()}")
h5 = new_host(td3)
SphAddon._save_miniapp_media(h5, FakeFlow("dddd111122223333444455"))
names5 = sorted(f.name for f in sub.glob("*.mp4"))
print(f"  目录: {names5}")
check("S5 ★.seq.json 丢了也能接着编号（第09集）",
      any(n.startswith("第09集_") for n in names5), str(names5))

print()
print("=" * 74)
print("S6-S8 自动跳集检测（源码检查）")
print("=" * 74)
src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
i = src.find("def _save_miniapp_media")
j = src.find("def response", i)
seg = src[i:j]

check("S6a 有「自动连播中」提示", "自动连播中" in seg)
check("S6b 间隔判据 ≤180 秒", "<= 180" in seg or "<=180" in seg)
check("S7a 有「可能是手动选集」提示", "可能是手动选集" in seg)
check("S7b 提示引导别关播放页", "别关播放页" in seg)
check("S8 ★明确说明拿不到真实集号",
      "拿不到" in seg or "只有哈希" in seg or "不含集号" in seg)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
