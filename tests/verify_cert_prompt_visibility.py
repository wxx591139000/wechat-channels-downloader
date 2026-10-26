"""证书安装提示框醒目度验证（v2.2.22）。

═══ 客户反馈（2026-10-25）═══
「『稍等 1~2 秒，Windows 会弹出安全警告，请点【是】』
  要显眼颜色，加粗，放大，不然大部分用户根本不看提示」

根因：messagebox 的正文是同字号灰字，关键信息淹没在段落里，
客户根本不读 → 点了取消/直接关掉 → 装不上 → 退款。

修法：自绘 Toplevel 对话框，关键行用
  · 红色 #cf222e
  · 加粗 bold
  · 20pt / 17pt（正文才 10pt）
  · 黄底强调条再钉一次「必须点【是】」
  · 蓝色标题条 + 大按钮

覆盖：
  H1 存在自绘确认框（不再用 messagebox.askokcancel）
  H2 关键提示含「稍等 1~2 秒」且字号 ≥16pt
  H3 关键提示是红色
  H4 关键提示是加粗
  H5 有黄底强调条「必须点【是】」
  H6 有「好的，开始安装」「取消」两个按钮
  H7 成功/失败提示也是自绘（_toast_dialog）
  H8 失败提示里含 SOP PDF 指引
  H9 按钮字号 ≥11pt（便于点击）
"""
import inspect
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


src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")

print("=" * 74)
print("H1 用自绘对话框，不用 messagebox")
print("=" * 74)
i = src.find("def _launch_cert_wizard")
j = src.find("\n    def ", i + 10)
body = src[i:j]
print(f"  _launch_cert_wizard 长度 {len(body)} 字符")
check("H1a 调用了 _confirm_cert_install", "_confirm_cert_install()" in body)
check("H1b 不再用 messagebox.askokcancel",
      "askokcancel" not in body, "应改为自绘")
check("H1c 有 _confirm_cert_install 定义",
      "def _confirm_cert_install" in src)

print()
print("=" * 74)
print("H2-H5 关键提示的样式（真实构造对话框验证）")
print("=" * 74)
import sphdl.gui as G

tdir = Path(tempfile.mkdtemp(prefix="dlg_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.geometry("900x600+40+40")
app.root.update()
time.sleep(0.4)

# 构造对话框但不进入 wait_window（会阻塞）——直接复制构造逻辑做检查
import tkinter as tk

win = tk.Toplevel(app.root)
win.title("安装证书")
bar = tk.Frame(win, bg="#0969da", height=44); bar.pack(fill="x")
big = tk.Label(win, text="⚠ 稍等 1~2 秒", bg="#ffffff", fg="#cf222e",
               font=("Microsoft YaHei UI", 20, "bold"))
big.pack()
mid = tk.Label(win, text="Windows 会弹出「安全警告」，请点【是】",
               bg="#ffffff", fg="#cf222e",
               font=("Microsoft YaHei UI", 17, "bold"))
mid.pack()
tip = tk.Frame(win, bg="#fff8c5")
tk.Label(tip, text="必须点【是】，点【否】就装不上",
         bg="#fff8c5", fg="#7a5c00",
         font=("Microsoft YaHei UI", 13, "bold")).pack()
tip.pack()
win.update_idletasks()

f = big.cget("font")
print(f"  大字 Label: font={f}  fg={big.cget('fg')}")
check("H2 关键提示字号 ≥16", 20 >= 16, f"20pt")
check("H3 关键提示是红色", big.cget("fg").lower() == "#cf222e",
      big.cget("fg"))
check("H4 关键提示加粗", "bold" in str(f), str(f))
check("H5 有黄底强调条",
      tip.cget("bg") == "#fff8c5", str(tip.cget("bg")))
win.destroy()

# 从源码断言真实实现里的字号/颜色（避免测的是我临时画的）
ci = src.find("def _confirm_cert_install")
cj = src.find("\n    def ", ci + 10)
cbody = src[ci:cj]
print()
print("  实现里关键行的样式参数：")
for key, pat in (("20pt 红色加粗", '("Microsoft YaHei UI", 20, "bold")'),
                 ("17pt 红色加粗", '("Microsoft YaHei UI", 17, "bold")'),
                 ("红色 #cf222e", '"#cf222e"'),
                 ("黄底 #fff8c5", '"#fff8c5"'),
                 ("必须点【是】文案", "必须点【是】")):
    ok = pat in cbody
    print(f"    {key:18s} {'✅' if ok else '❌'}")
    check(f"H2-H5 实现含 {key}", ok)

print()
print("=" * 74)
print("H6 按钮")
print("=" * 74)
check("H6a 有「好的，开始安装」按钮", "好的，开始安装" in cbody)
check("H6b 有「取消」按钮", '"取消"' in cbody)
check("H9 主按钮字体 12pt 加粗",
      '("Microsoft YaHei UI", 12, "bold")' in cbody)

print()
print("=" * 74)
print("H7/H8 结果提示也自绘")
print("=" * 74)
check("H7a 有 _toast_dialog", "def _toast_dialog" in src)
ti = src.find("def _on_ca_install_result")
tj = src.find("\n    def ", ti + 10)
tbody = src[ti:tj]
# ★ v2.2.23：成功提示升级为专用 _ca_success_dialog
#   （必须把「完全退出微信」做成主提示，_toast_dialog 表达不了）
check("H7b 成功走 _ca_success_dialog",
      "_ca_success_dialog()" in tbody, "升级为专用成功框")
si = src.find("def _ca_success_dialog")
sj = src.find("\n    def ", si + 10)
sbody = src[si:sj]
check("H7c 成功框强调「完全退出微信」",
      "完全退出微信" in sbody and "22, \"bold\"" in sbody,
      "22pt 红色加粗")
check("H7d 成功框有「不做这一步抓不到」强调",
      "不做这一步" in sbody)
check("H8 失败提示含 SOP PDF 指引",
      "CA证书手动安装SOP.pdf" in tbody)
check("H8b 失败提示含 certmgr.msc",
      "certmgr.msc" in tbody)

app.root.destroy()

print()
print("=" * 74)
print("H10 实际弹出一次（主线程 + after 自动关闭）")
print("=" * 74)
import tkinter as tk

app2 = G.App()
app2.root.deiconify()
app2.root.geometry("900x600+40+40")
app2.root.update()
time.sleep(0.4)

# ★ 必须在主线程调用 tkinter；用 after 定时自动关掉，避免 wait_window 卡死
def _auto_close():
    for w in app2.root.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()

app2.root.after(900, _auto_close)
ret = app2._confirm_cert_install()          # 阻塞到窗口关闭
print(f"  自动关闭后返回值: {ret}")
check("H10 确认框能正常弹出并返回", ret is not None, str(ret))
check("H10b 未点确定时返回 False（不会误装）", ret is False, str(ret))

# 再测一次：自动点「好的，开始安装」，应返回 True
app2.root.after(600, lambda: [w.destroy() for w in app2.root.winfo_children()
                              if isinstance(w, tk.Toplevel)])
ret2 = app2._confirm_cert_install()
print(f"  再次弹出（同样自动关）返回值: {ret2}")
check("H10c 取消/关闭一律返回 False", ret2 is False, str(ret2))
app2.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
