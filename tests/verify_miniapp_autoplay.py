"""小程序短剧「连播抓取」验证（v2.8.2）。

═══ 客户澄清（2026-10-30）═══
「没有这个开关，但一集播完会自动跳下一集」

═══ 决定性证据（客户真实文件）═══
```
第01集_f0841ec0.mp4  10:04:32  4.4MB  73秒  音轨 aac
第02集_03c02455.mp4  10:12:35  2.5MB  45秒  音轨 aac
第03集_8f4700bb.mp4  10:12:45  3.6MB  46秒  音轨 aac   ← 距上一集仅 10 秒
```
第02→第03 只隔 10 秒，客户**没有手动操作**（手动换集要点选菜单），
说明是小程序**自动跳集**，而我们的边播边存完整抓到了。

→ **C 方案成立且已自然跑通**：挂着嗅探 + 开连播 = 连续抓完整季。

本版只做「让它更明确、可观测」：
  · 累计保存集数（miniapp_saved_total）
  · 每 5 集打一次连播进度
  · 每集保存后提示"挂着即可继续"
  · 使用说明补小程序短剧章节

覆盖：
K1  累计集数递增
K2  ★连续 3 集保存（模拟自动跳集）→ total=3
K3  每 5 集打一次进度提示（不是每集）
K4  每集都有"继续挂着"引导
K5  使用说明含小程序短剧章节
K6  使用说明写明"自动跳下一集，挂着不用管"
K7  不同集不会因为同一 .part 冲突（各自的 part 文件）
"""
import re
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
    def __init__(self, status, body=b"", cr=None):
        self.status_code = status
        self.content = body
        self.headers = {"content-range": cr} if cr else {}


class FakeFlow:
    def __init__(self, status, body=b"", cr=None, ep="abcdef0123456789abcdef"):
        self.response = FakeResp(status, body, cr)
        self.request = type("Q", (), {
            "path": f"/vod/{ep}/f0.mp4?t=1", "host": "x.wxamedia.com"})()


class Host:
    def __init__(self, out_dir):
        self._miniapp_saved = 0
        self.miniapp_saved_total = 0
        self.out_dir = str(out_dir)
        self.on_miniapp = None


print("=" * 74)
print("K1-K3 连播累计")
print("=" * 74)
td = Path(tempfile.mkdtemp(prefix="lianbo_"))
config.DOWNLOAD_DIR = td
h = Host(td)

EPS = ["aaaa111122223333444455", "bbbb111122223333444455",
       "cccc111122223333444455", "dddd111122223333444455",
       "eeee111122223333444455", "ffff111122223333444455"]
for ep in EPS[:3]:
    SphAddon._save_miniapp_media(h, FakeFlow(200, b"X" * 3000, ep=ep))
files = sorted(td.rglob("*.mp4"))
print(f"  保存 3 集 → total={h.miniapp_saved_total}, 文件 {len(files)} 个")
check("K1 累计集数递增", h.miniapp_saved_total == 3, str(h.miniapp_saved_total))
check("K2 ★模拟自动跳集连存 3 集", len(files) == 3, str(len(files)))
names = [f.name for f in files]
print(f"  文件名: {names}")
check("K2b 集序 01/02/03 正确",
      any(n.startswith("第01集_") for n in names)
      and any(n.startswith("第02集_") for n in names)
      and any(n.startswith("第03集_") for n in names), str(names))
check("K7 三集是三个独立文件（无 part 冲突）",
      len({f.stem for f in files}) == 3)

# 再存 2 集到 5（触发 5 集进度提示）
for ep in EPS[3:5]:
    SphAddon._save_miniapp_media(h, FakeFlow(200, b"Y" * 3000, ep=ep))
print(f"  存到 5 集 → total={h.miniapp_saved_total}")
check("K3 累计到 5 集", h.miniapp_saved_total == 5, str(h.miniapp_saved_total))

print()
print("=" * 74)
print("K3b-K4 日志提示")
print("=" * 74)
src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
i = src.find("def _save_miniapp_media")
j = src.find("def response", i)
seg = src[i:j]

check("K3b ★每 5 集打一次进度（% 5）", "% 5 == 0" in seg)
check("K3c 进度文案含集数", "连续保存" in seg and "miniapp_saved_total" in seg)
check("K4a 每集提示「继续挂着」", "保持嗅探运行即可连续抓取" in seg)
check("K4b 提示说明「自动跳下一集」", "自动跳下一集" in seg)
check("K4c 提示说明「换剧才点选集」", "选集" in seg)

print()
print("=" * 74)
print("K5-K6 使用说明")
print("=" * 74)
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
gi = gui.find('GUIDE_TEXT = """')
gj = gui.find('"""', gi + 20)
guide = gui[gi:gj]
print(f"  GUIDE_TEXT 长度 {len(guide)}")
check("K5 使用说明含小程序短剧章节",
      "小程序短剧" in guide and "边播边存" in guide)
check("K6 ★写明「自动跳下一集，挂着不用管」",
      "自动跳下一集" in guide and "挂着" in guide)
check("K5b 写明保存目录", "小程序短剧" in guide and "第01集" in guide)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
