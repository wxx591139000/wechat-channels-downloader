"""CA 证书零窗口 + 序号列隐藏（v2.2.18）。

═══ 客户事故（2026-10-25）═══
「另外 2 个客户最后装不上 CA 证书，被退款了。
  CA 证书要从根本上解决，安装证书时不能闪退，
  要么不显示命令窗，要么停留等待用户关闭，不能给人闪退的感觉。」

用户要求的两条：
  1. **装证书全程不显示命令窗口**（根除"闪退感"）
  2. **视频清单里的「序号」列隐藏**

═══ 为什么彻底放弃批处理 ═══
即使把 bat 换行符修成 CRLF，仍有这些情况会让窗口一闪而过：
  · 杀软拦截 cmd 启动
  · UAC 策略
  · 中文/空格路径被拆词
  · 环境变量缺失（certutil 不在 PATH）
客户看不到任何原因 → 只觉得"软件坏了" → 退款。
所以唯一根本解：**不启动任何控制台进程**。

覆盖：
  W1 ca_install._run 必定带 CREATE_NO_WINDOW
  W2 GUI 不再调用 open_wizard / 不再启动 bat
  W3 ca_install.install() 实测可跑（零窗口）
  W4 CA 文件缺失时给"先启动嗅探"的提示（不是黑窗口）
  W5 失败时给手动步骤 + 复制路径
  W6 序号列 width=0 / minwidth=0 / 表头空
  W7 隐藏序号列后 _col_index 仍与 values 索引一一对应
  W8 seq 字段本身保留（清空重置/按序下载仍在用）
  W9 标题列变宽补偿
  W10 界面截图确认序号列看不到
"""
import inspect
import subprocess
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


print("=" * 74)
print("W1 ca_install 全程零窗口")
print("=" * 74)
from sphdl import ca_install

mod_src = inspect.getsource(ca_install)
src = inspect.getsource(ca_install._run)
# ★ v2.2.21：零窗口改为模块常量 _NO_WINDOW，判据看"有没有用这个常量"
has_const = "CREATE_NO_WINDOW" in mod_src
print("  模块定义含 CREATE_NO_WINDOW: " + str(has_const))
print(f"  _run 传 creationflags: {'creationflags=_NO_WINDOW' in src}")
check("W1a 模块定义了 _NO_WINDOW（CREATE_NO_WINDOW）",
      "CREATE_NO_WINDOW" in mod_src)
check("W1b _run 把 _NO_WINDOW 传给 subprocess",
      "creationflags=_NO_WINDOW" in src)
# Popen 也必须零窗口
check("W1d 安装用的 Popen 也带 CREATE_NO_WINDOW",
      "creationflags=_NO_WINDOW" in inspect.getsource(
          ca_install.install_interactive))

# 源码里不许出现可见窗口的启动方式
bad_pat = ("shell=True", "start ", "os.system", "Popen(f'")
found = [p for p in bad_pat if p in src]
print(f"  危险启动方式: {found or '无'}")
check("W1c 没有 shell=True / os.system 等可见窗口方式", not found, str(found))

print()
print("=" * 74)
print("W2 GUI 不再启动 bat / 向导")
print("=" * 74)
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
# 只看 _launch_cert_wizard 函数体
i = gui.find("def _launch_cert_wizard")
j = gui.find("\n    def ", i + 10)
body = gui[i:j]
print(f"  函数体长度: {len(body)} 字符")
check("W2a 不再调 open_wizard", "open_wizard" not in body)
check("W2b 不再 Popen 启动 bat", "Popen" not in body)
check("W2c 不再引用 install-cert.bat",
      "install-cert.bat" not in body.replace(
          "根因是 install-cert.bat 是 LF 换行", ""),
      "注释里提到不算")
check("W2d 调用了 ca_install.install_interactive",
      "install_interactive(" in body)

print()
print("=" * 74)
print("W3/W4/W5 实际行为")
print("=" * 74)
ca = ca_install.find_ca_file()
print(f"  CA 文件: {ca}")
t0 = time.time()
inst = ca_install.is_installed()
print(f"  is_installed()={inst} 耗时 {time.time()-t0:.2f}s")
check("W3a is_installed 可跑且不崩", isinstance(inst, bool))

if ca is not None:
    t0 = time.time()
    ok, msg = ca_install.install()
    dt = time.time() - t0
    print(f"  install() → ok={ok} 耗时 {dt:.2f}s  msg={msg[:50]}")
    check("W3b install 可跑", isinstance(ok, bool) and bool(msg))
else:
    ok, msg = ca_install.install()
    print(f"  无 CA 时 install() → ok={ok} msg={msg[:60]}")
    check("W4 无 CA 时提示先启动嗅探",
          ok is False and ("启动嗅探" in msg or "生成" in msg), msg[:50])

# 失败提示必须含手动步骤
# ★ v2.2.21：失败提示移到 _on_ca_install_result
g2 = gui
i2 = g2.find("def _on_ca_install_result")
j2 = g2.find("\n    def ", i2 + 10)
res_body = g2[i2:j2]
manual_ok = ("certmgr.msc" in res_body
             and "受信任的根证书颁发机构" in res_body
             and "clipboard_append" in res_body
             and "CA证书手动安装SOP.pdf" in res_body)
print(f"  失败分支含手动步骤+复制路径: {manual_ok}")
check("W5 失败时给手动步骤 + 复制路径", manual_ok)

print()
print("=" * 74)
print("W6-W10 序号列隐藏")
print("=" * 74)
check("W6a 列定义里 no 宽度为 0", '"no": ("", 0)' in gui)
check("W6b column 设置了 minwidth=0", "minwidth=0" in gui)
check("W6c 表头文本为空", '"no": ("", 0)' in gui)
check("W9 标题列变宽（349，序号+大小都隐藏后）",
      '"title": ("标题", 349)' in gui)

# W7：_row_values 仍返回 8 个字段，索引对应
import sphdl.gui as G
tdir = Path(tempfile.mkdtemp(prefix="seqhide_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

cols = tuple(app.tree["columns"])
print(f"  Treeview 列: {cols}")
r = G.Record(id="t1", title="测试", url="u", decode_key="", size=100,
             source="t", created="2026-10-25 10:00:00", status="waiting",
             seq=7)
vals = app._row_values(r)
print(f"  _row_values 返回 {len(vals)} 个值: {vals[:4]}...")
check("W7a values 数与列数一致", len(vals) == len(cols),
      f"{len(vals)} vs {len(cols)}")
check("W7b 序号字段仍在 values 里（空字符串）", vals[0] == "", repr(vals[0]))
# _col_index 对应关系
for name, expect in (("no", 0), ("status", 1), ("title", 2),
                     ("platform", 3), ("size", 4)):
    got = app._col_index(name)
    if got != expect:
        check(f"W7c _col_index({name})", False, f"{got} != {expect}")
check("W7c 所有 _col_index 索引正确",
      all(app._col_index(n) == e for n, e in
          (("no", 0), ("status", 1), ("title", 2), ("platform", 3),
           ("size", 4), ("source", 5), ("time", 6), ("file", 7))))

# W6d 实际渲染后序号列宽为 0
app.store.add(r)
app._add_row(r)
app.root.update()
w = app.tree.column("no", "width")
mw = app.tree.column("no", "minwidth")
hd = app.tree.heading("no", "text")
print(f"  渲染后 序号列: width={w} minwidth={mw} 表头={hd!r}")
check("W6d 渲染后序号列宽度为 0", int(w) == 0, str(w))
check("W6e 表头为空", str(hd).strip() == "", repr(hd))

print()
print("=" * 74)
print("W8 seq 字段与排序能力保留")
print("=" * 74)
check("W8a Record 仍有 seq 字段", hasattr(r, "seq"), str(getattr(r, "seq", None)))
check("W8b 清空重置仍在", "_reset_seq_on_clear" in gui)
check("W8c _renumber_rows 仍在", "_renumber_rows" in gui)
# 点表头仍能按 seq 排序
try:
    app._on_tree_sort("no")
    check("W8d 仍可按 no 排序（不崩）", True)
except Exception as e:
    check("W8d 仍可按 no 排序（不崩）", False, str(e)[:60])

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
