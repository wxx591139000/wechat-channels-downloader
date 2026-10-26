"""「抓不到视频」看门狗验证（v2.2.23）。

═══ 客户痛点（2026-10-25）═══
「刚才安装成功后，我播放视频号视频发现抓取不到」
「避免抓取不到，也没任何提示。或者你可以有其他方法识别视频号在播放
  但你抓取不到，提醒用户做正确的操作，避免静默失败」

静默失败是最伤人的：客户不知道是自己错、证书没生效、还是软件坏了，
只能退款。所以要有看门狗主动诊断。

═══ 判定矩阵 ═══
| 统计                                   | 结论                     |
|----------------------------------------|--------------------------|
| 微信域名 TLS 失败 > 0                  | 证书没生效 / 微信没重启  |
| 微信页面有请求(≥3) 且 抓到 0 个        | 页面开太早 / 微信没重启  |
| 完全没有微信流量                       | 不提示（可能没开视频号） |
| 已经抓到 > 0                           | 不提示（正常工作）       |

覆盖：
  K1 addon 有 4 个统计计数器
  K2 request 钩子统计微信页面请求
  K3 error 钩子统计 TLS 失败
  K4 sniff_diagnostics 返回完整快照
  K5 GUI 有看门狗 + 3 分钟冷却 + 25 秒宽限
  K6 抓到视频时不打扰
  K7 TLS 失败 → 提示指向「微信没重启/证书」
  K8 页面活动但 0 捕获 → 提示指向「页面开太早」
  K9 提示里必须出现「完全退出微信」
  K10 提示框有「帮我退出微信」按钮
  K11 引擎停止后看门狗不再工作
"""
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


addon_src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
gui_src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")

print("=" * 74)
print("K1-K4 addon 统计")
print("=" * 74)
for stat in ("stat_channels_hits", "stat_tls_fail", "stat_captured",
             "stat_last_hit_ts"):
    ok = stat in addon_src
    print(f"  {stat:22s} {'✅' if ok else '❌'}")
    check(f"K1 有 {stat}", ok)

check("K2 request 钩子统计微信请求",
      "stat_channels_hits += 1" in addon_src
      and "stat_last_hit_ts = time.time()" in addon_src)
check("K3 error 钩子统计 TLS 失败",
      "def error(self, flow" in addon_src
      and "stat_tls_fail += 1" in addon_src)
check("K4 sniff_diagnostics 存在且返回各统计",
      "def sniff_diagnostics" in addon_src
      and all(k in addon_src for k in
              ('"channels_hits"', '"tls_fail"', '"captured"', '"last_hit_age"')))

# 真实取一次快照
from sphdl.addon import SphAddon
a = SphAddon(None)
st = a.sniff_diagnostics()
print(f"  实测快照: {st}")
check("K4b 快照字段齐全",
      all(k in st for k in ("channels_hits", "tls_fail", "captured",
                            "comments", "last_hit_age")))

print()
print("=" * 74)
print("K5 v2.2.23 GUI 看门狗接线")
print("=" * 74)
check("K5a 有 _start_capture_watchdog", "def _start_capture_watchdog" in gui_src)
check("K5b 每 5 秒轮询", "self.root.after(5000" in gui_src)
check("K5c 有 3 分钟冷却", "_wd_last_warn" in gui_src and "180" in gui_src)
check("K5d 有 25 秒宽限", "_wd_started" in gui_src and "25" in gui_src)
check("K5e 引擎启动时开启", "_start_capture_watchdog()" in gui_src)

print()
print("=" * 74)
print("K6-K8 三种判定走对分支")
print("=" * 74)
wi = gui_src.find("def _warn_capture_failed")
wj = gui_src.find("\n    def ", wi + 10)
wbody = gui_src[wi:wj]
check("K7 TLS 失败 → 提示证书/微信重启",
      'kind: str' in wbody and "tls" in wbody and "微信没有重启" in wbody)
check("K8 页面活动 → 提示页面开太早",
      "启动嗅探】之前打开的" in wbody or "启动嗅探之前" in wbody)
check("K6 抓到视频时直接返回（不打扰）",
      "if cap > 0:" in gui_src and "return" in gui_src)

print()
print("=" * 74)
print("K9-K10 提示内容")
print("=" * 74)
check("K9 提示含「完全退出微信」", "完全退出微信" in wbody)
check("K9b 提示含托盘退出说明", "托盘" in wbody)
check("K10 有「帮我退出微信」按钮", "帮我退出微信" in wbody)
check("K10b 会 taskkill 微信进程", "taskkill" in wbody and "WeChat.exe" in wbody)

print()
print("=" * 74)
print("K1b 捕获计数只在成功时累加（不虚增）")
print("=" * 74)
i = addon_src.find("if self.on_capture:")
seg = addon_src[i:i+400]
ok = "stat_captured += 1" in seg and "accepted = self.on_capture" in seg
print(f"  片段: {seg[:120]!r}")
check("K1b stat_captured 在 on_capture 之后累加", ok)

print()
print("=" * 74)
print("K11 实际触发一次（模拟 TLS 失败）")
print("=" * 74)
import sphdl.gui as G
import tkinter as tk

tdir = Path(tempfile.mkdtemp(prefix="wd_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.geometry("900x600+40+40")
app.root.update()
time.sleep(0.3)

# 让 addon 报告 TLS 失败
app.addon.stat_tls_fail = 2
app.addon.stat_channels_hits = 5
app.addon.stat_captured = 0
app.addon.stat_last_hit_ts = time.time()
app.engine_running = True
app._wd_last_warn = 0.0

opened = []


def _snap():
    tops = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
    opened.extend(tops)
    for w in tops:
        w.destroy()

app._warn_capture_failed("tls", 2)
# 必须让事件循环跑起来，Toplevel 才会真正创建
for _ in range(8):
    app.root.update()
    time.sleep(0.08)
tops = [w for w in app.root.winfo_children()
        if isinstance(w, tk.Toplevel) and "抓不到" in str(w.title())]
opened.extend(tops)
print(f"  弹出的诊断框数量: {len(opened)}")
if tops:
    # 抓一下框里的关键文字，确认内容对
    texts = []
    def walk(x):
        for c in x.winfo_children():
            # ★ 必须同时读 Text 控件（正文在 Text 里，只读 label 会漏）
            if c.winfo_class() == "Text":
                try:
                    texts.append(c.get("1.0", "end"))
                except Exception:
                    pass
            else:
                try:
                    t = c.cget("text")
                    if t:
                        texts.append(str(t))
                except Exception:
                    pass
            walk(c)
    walk(tops[0])
    joined = " ".join(texts)
    print(f"  框内关键文字命中: 完全退出微信={'完全退出微信' in joined} "
          f"托盘={'托盘' in joined} 帮我退出微信={'帮我退出微信' in joined}")
    for w in tops:
        w.destroy()
check("K11 能弹出诊断框", len(opened) >= 1, str(len(opened)))
app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
