"""小程序短剧落盘位置 + 入清单验证（v2.7.8）。

═══ 客户实测（2026-10-30）═══
「没有捕获到清单，下载路径也是空的」

═══ 实际日志 ═══
文件**确实保存成功了**（5 个，09:18~09:21），但：
```
🎬 小程序短剧已保存：f0841ec0….mp4（4.4 MB）
   → E:\...\dist\v2.7.7\微信视频号下载器\downloads\小程序短剧
```
客户配置的下载目录是 `E:\视频号下载`，却写到了**程序目录下**。

两个 bug：
① 用了 `config.DOWNLOAD_DIR`（模块默认值 = 程序目录/downloads），
   不是 GUI 里用户配置的 `self.cfg.download_dir`
② 保存后只打日志，**没有加入清单** → 客户在列表里看不到，以为失败

覆盖：
M1  ★落盘用 GUI 注入的 out_dir（用户配置目录）
M2  out_dir 未注入时退回 config.DOWNLOAD_DIR（不崩）
M3  ★保存完成后回调 on_miniapp
M4  回调参数是 (集号, 完整路径, 字节数)
M5  回调抛异常不影响文件保存
M6  ★GUI 收到 miniapp_saved 会加入清单
M7  清单记录字段正确（来源=小程序短剧、状态=done、file 有值）
M8  重复集号不重复入清单
M9  GUI 启动时注入 out_dir 和 on_miniapp
"""
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
    def __init__(self, status, body=b"", cr=None, path="/vod/abcdef0123456789abcdef/f0.mp4?t=1"):
        self.response = FakeResp(status, body, cr)
        self.request = type("Q", (), {"path": path, "host": "x.wxamedia.com"})()


class Host:
    def __init__(self, out_dir=None, cb=None):
        self._miniapp_saved = 0
        self.out_dir = out_dir
        self.on_miniapp = cb


print("=" * 74)
print("M1 落盘到用户配置目录")
print("=" * 74)
user_dir = Path(tempfile.mkdtemp(prefix="userdl_"))
h = Host(out_dir=str(user_dir))
SphAddon._save_miniapp_media(h, FakeFlow(200, b"X" * 5000))
files = list(user_dir.rglob("*.mp4"))
print(f"  用户目录: {user_dir}")
print(f"  产出: {[str(f.relative_to(user_dir)) for f in files]}")
check("M1a ★文件在用户配置目录", len(files) == 1, str(len(files)))
check("M1b 子目录是 小程序短剧", files and "小程序短剧" in str(files[0]))

print()
print("=" * 74)
print("M2 未注入 out_dir 时退回默认（不崩）")
print("=" * 74)
old_default = config.DOWNLOAD_DIR
config.DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="defdl_"))
h2 = Host(out_dir=None)
try:
    SphAddon._save_miniapp_media(h2, FakeFlow(200, b"Y" * 3000,
                                             path="/vod/ffee001122334455667788/f0.mp4?t=1"))
    ok = len(list(config.DOWNLOAD_DIR.rglob("*.mp4"))) == 1
except Exception as e:                                   # noqa: BLE001
    ok = False
    print(f"  异常: {e}")
check("M2 退回 config.DOWNLOAD_DIR 且成功", ok)
config.DOWNLOAD_DIR = old_default

print()
print("=" * 74)
print("M3-M5 回调")
print("=" * 74)
got = []
d3 = Path(tempfile.mkdtemp(prefix="cb_"))
h3 = Host(out_dir=str(d3), cb=lambda ep, path, size: got.append((ep, path, size)))
SphAddon._save_miniapp_media(h3, FakeFlow(
    200, b"Z" * 4096, path="/vod/aabbccdd112233445566/f0.mp4?t=1"))
print(f"  回调收到: {[(g[0][:10], Path(g[1]).name, g[2]) for g in got]}")
check("M3 ★保存后回调 on_miniapp", len(got) == 1, str(len(got)))
check("M4a 回调第1参是集号", got and got[0][0].startswith("aabbccdd"), got[0][0][:12] if got else "-")
check("M4b 第2参是完整路径且存在", got and Path(got[0][1]).exists())
check("M4c 第3参是字节数", got and got[0][2] == 4096, str(got[0][2]) if got else "-")

# 回调抛异常
d5 = Path(tempfile.mkdtemp(prefix="cberr_"))


def bad_cb(ep, path, size):
    raise RuntimeError("boom")


h5 = Host(out_dir=str(d5), cb=bad_cb)
try:
    SphAddon._save_miniapp_media(h5, FakeFlow(
        200, b"W" * 2048, path="/vod/99887766554433221100/f0.mp4?t=1"))
    err = None
except Exception as e:                                   # noqa: BLE001
    err = e
saved5 = list(d5.rglob("*.mp4"))
check("M5 ★回调异常不影响文件保存",
      err is None and len(saved5) == 1, f"err={err} files={len(saved5)}")

print()
print("=" * 74)
print("M6-M9 源码接线")
print("=" * 74)
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
addon = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")

check("M9a GUI 注入 out_dir", "self.addon.out_dir = self.cfg.download_dir" in gui)
check("M9b GUI 注入 on_miniapp", "self.addon.on_miniapp =" in gui)
check("M6 ★GUI 有 miniapp_saved 事件分支", 'kind == "miniapp_saved"' in gui)
check("M6b 调用 _add_miniapp_record", "_add_miniapp_record(" in gui)
i = gui.find("def _add_miniapp_record")
j = gui.find("\n    def ", i + 10)
seg = gui[i:j]
check("M7a 来源标记为 小程序短剧", 'source="小程序短剧"' in seg)
check("M7b 状态 done", 'status="done"' in seg)
check("M7c file 有值", "file=str(path)" in seg)
check("M7d platform 标记", 'platform="小程序短剧"' in seg)
check("M8 ★重复集号不重复入清单", "if rid in self.store.items" in seg)
check("M1c addon 优先用 self.out_dir", 'getattr(self, "out_dir", None)' in addon)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
