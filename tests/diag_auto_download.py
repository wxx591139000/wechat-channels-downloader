"""诊断：为什么「自动下载」开关开着却不自动下载。

═══ 客户报障（2026-10-23）═══
「2.2.8 当前启动了『自动下载』为何没有自动下载」

日志显示：
    [INFO] 已加入待下载列表（GUI 模式不自动下载，请在界面点选后下载）
→ 这句话只在 `addon.auto_download == False` 时打印。

但用户级 config.json 里 `auto_download: True`。
说明 **cfg 与 addon 不同步**。

可能原因：
  A. 顺序问题：addon 创建时 cfg 还没加载完
  B. 开关 UI 改了 cfg 但没同步 addon（或反之）
  C. 启动时某处把 addon.auto_download 重置了
  D. 用户看到的是"自动下载"按钮高亮，但实际值是关
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


print("=" * 74)
print("A. 配置里 auto_download=True 时，addon 是否真的拿到 True")
print("=" * 74)
import sphdl.gui as G

tdir = Path(tempfile.mkdtemp(prefix="autodl_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text(json.dumps({"auto_download": True}), encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

print(f"  cfg.auto_download   = {app.cfg.auto_download}")
print(f"  addon.auto_download = {app.addon.auto_download}")
check("A1 cfg 读到 True", app.cfg.auto_download is True, str(app.cfg.auto_download))
check("A2 addon 也应是 True", app.addon.auto_download is True,
      str(app.addon.auto_download))

print()
print("=" * 74)
print("B. 切换开关：UI → cfg → addon 三者是否一致")
print("=" * 74)
app.auto_dl_var.set(True); app._on_auto_dl_toggle()
print(f"  开 → cfg={app.cfg.auto_download} addon={app.addon.auto_download}")
check("B1 开启后三者一致",
      app.cfg.auto_download is True and app.addon.auto_download is True,
      f"cfg={app.cfg.auto_download} addon={app.addon.auto_download}")

app.auto_dl_var.set(False); app._on_auto_dl_toggle()
print(f"  关 → cfg={app.cfg.auto_download} addon={app.addon.auto_download}")
check("B2 关闭后三者一致",
      app.cfg.auto_download is False and app.addon.auto_download is False,
      f"cfg={app.cfg.auto_download} addon={app.addon.auto_download}")

print()
print("=" * 74)
print("C. 授权门禁是否会拦掉自动下载")
print("=" * 74)
app.auto_dl_var.set(True); app._on_auto_dl_toggle()
lic = app._has_download_license()
print(f"  _has_download_license() = {lic}")
if not lic:
    print("  ⚠️ 未授权 → v2.2.5 的门禁会拒绝自动下载（这是设计）")
check("C1 授权判定不抛异常", isinstance(lic, bool), str(lic))

# 检查 addon.license_check 是否真的挂着
print(f"  addon.license_check 已挂: {app.addon.license_check is not None}")
check("C2 addon.license_check 已挂接",
      app.addon.license_check is not None)

print()
print("=" * 74)
print("D. 有授权时，捕获是否真的提交下载")
print("=" * 74)


class FakePipe:
    def __init__(self):
        self.got = []

    def submit(self, vd):
        self.got.append(vd)


from sphdl.addon import SphAddon
from sphdl.entities import VideoData

fp = FakePipe()
a = SphAddon(fp, auto_download=True)
a.license_check = lambda: True          # 模拟已授权
a.duplicate_check = None
vd = VideoData(url="http://x/1.mp4", decode_key="", size=100,
               description="测试")
a._handle(vd, "test")
print(f"  已授权 + 自动下载开 → 提交 {len(fp.got)} 个")
check("D1 已授权时确实提交", len(fp.got) == 1, str(len(fp.got)))

fp2 = FakePipe()
a2 = SphAddon(fp2, auto_download=True)
a2.license_check = lambda: False        # 未授权
a2._handle(vd, "test")
print(f"  未授权 + 自动下载开 → 提交 {len(fp2.got)} 个（应为 0）")
check("D2 未授权时被门禁拦（设计如此）", len(fp2.got) == 0, str(len(fp2.got)))

print()
print("=" * 74)
print("E. 源码复查：哪里会打印「GUI 模式不自动下载」")
print("=" * 74)
src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
n = src.count("GUI 模式不自动下载")
print(f"  出现次数: {n}")
# 这句只在 auto_download 为 False 的分支
idx = src.find("GUI 模式不自动下载")
before = src[max(0, idx - 300):idx]
print(f"  上文: ...{before[-120:].strip()}")
check("E1 该日志在 auto_download=False 分支",
      "if self.auto_download:" in src[:idx], "")

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
