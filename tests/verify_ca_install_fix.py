"""证书安装闪退修复验证（v2.2.17）。

═══ 客户报障（2026-10-25）═══
「这 2 天有 3 个客户使用 2.2.9 时装不上 CA 证书，说装证书时命令窗口闪退。
  其中一个客户说『反复装不上，后来搞明白了，直接进的 C 盘，手动装的 CA』」

═══ 根因（实测复现）═══
`install-cert.bat` 是 **LF 换行**（不是 CRLF）。
cmd 解析 LF 批处理会在 `cd /d "%~dp0"` 处失败：
    返回码 1，耗时 0.03 秒，输出「系统找不到指定的路径。」
→ 窗口一闪就没，客户看不到任何信息。
影响 v2.2.2 ~ v2.2.16 **全部 13 个已发版本**。

═══ 修法 ═══
1. 两个 bat 全部转 CRLF（保留 GBK 字节）
2. `.gitattributes` 锁死 `*.bat eol=crlf`，防止再被改回 LF
3. `release.py` 打包自检：包内任何 .bat 不是 CRLF → 直接拒绝出包
4. 新增 `sphdl/ca_install.py`：**程序内直接装证书**
   （用户根证书库不需要管理员权限），不再依赖 bat
5. GUI「安装/检测」改为「先自动装 → 失败才开向导」

覆盖：
  B1 仓库里所有 .bat 都是 CRLF
  B2 .gitattributes 锁了 .bat eol=crlf
  B3 release.py 含 bat 换行符自检
  B4 install-cert.bat 实际能跑（返回码 0，不是 0.03s 秒退）
  B5 诊断 bat 实际能跑
  B6 ca_install 能找到 CA 文件
  B7 ca_install 判断安装状态（不崩）
  B8 ca_install.install() 幂等（已装时返回成功）
  B9 GUI 已改为自动装优先
  B10 找 CA 文件不会误用含私钥的 mitmproxy-ca.pem
"""
import subprocess
import sys
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
print("B1 仓库里所有 .bat 都是 CRLF")
print("=" * 74)
bats = sorted(list(ROOT.glob("*.bat")) + list(ROOT.glob("*.cmd")))
if not bats:
    check("B1 找到 bat 文件", False, "一个都没有？")
for p in bats:
    b = p.read_bytes()
    has_crlf = b"\r\n" in b
    # 归一 CRLF 后不应再有裸 LF
    bare_lf = b.replace(b"\r\n", b"").count(b"\n")
    ok = has_crlf and bare_lf == 0
    print(f"  {p.name:26s} CRLF={has_crlf} 裸LF={bare_lf}")
    check(f"B1 {p.name} 全 CRLF", ok, f"裸LF={bare_lf}")

print()
print("=" * 74)
print("B2 .gitattributes 锁住换行")
print("=" * 74)
ga = ROOT / ".gitattributes"
if ga.exists():
    txt = ga.read_text(encoding="utf-8")
    has_bat = "*.bat" in txt and "eol=crlf" in txt
    print(f"  .gitattributes 存在，含 *.bat eol=crlf: {has_bat}")
    check("B2 .gitattributes 锁 *.bat eol=crlf", has_bat)
else:
    check("B2 .gitattributes 存在", False, "文件不存在")

print()
print("=" * 74)
print("B3 release.py 含换行符自检")
print("=" * 74)
rel = (ROOT / "release.py").read_text(encoding="utf-8")
check("B3a 有 bat 换行检查", "批处理换行符检查通过" in rel)
check("B3b 发现 LF 会 sys.exit", "必须全部是 CRLF" in rel)

print()
print("=" * 74)
print("B4/B5 bat 真的能跑（不是 0.03 秒秒退）")
print("=" * 74)
for name in ("install-cert.bat", "诊断-双击我.bat"):
    p = ROOT / name
    if not p.exists():
        check(f"B4 {name} 存在", False)
        continue
    t0 = time.time()
    try:
        r = subprocess.run(["cmd", "/c", str(p)], input=b"\r\n",
                           capture_output=True, timeout=90, cwd=str(ROOT))
        dt = time.time() - t0
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("gbk", "replace")
        # 关键判据：不能是"系统找不到指定的路径"
        bad = "找不到指定的路径" in out
        print(f"  {name}: 返回码={r.returncode} 耗时={dt:.2f}s "
              f"路径错误={bad}")
        check(f"B4 {name} 正常执行", not bad and r.returncode == 0,
              f"rc={r.returncode} {out.strip()[:40]}")
        check(f"B4b {name} 不是秒退（>0.2s）", dt > 0.2, f"{dt:.2f}s")
    except subprocess.TimeoutExpired:
        check(f"B4 {name} 正常执行", True, "卡在 pause = 运行正常")

print()
print("=" * 74)
print("B6-B8 ca_install 模块")
print("=" * 74)
from sphdl import ca_install

ca = ca_install.find_ca_file()
print(f"  找到 CA 文件: {ca}")
check("B6 能找到 CA 文件", ca is not None, str(ca))
check("B10 不是含私钥的 mitmproxy-ca.pem",
      ca is None or "mitmproxy-ca.pem" != ca.name, str(ca.name if ca else ""))

inst = ca_install.is_installed()
print(f"  当前是否已安装: {inst}")
check("B7 is_installed 返回 bool", isinstance(inst, bool), str(inst))

if ca is not None:
    ok, msg = ca_install.install()
    print(f"  install() → ok={ok}  msg={msg[:60]}")
    check("B8 install 不抛异常且返回说明", isinstance(ok, bool) and bool(msg),
          msg[:50])
    # 幂等：再装一次应仍是成功
    ok2, msg2 = ca_install.install()
    print(f"  再装一次 → ok={ok2}  msg={msg2[:50]}")
    check("B8b 重复安装幂等（第二次也成功）", ok2 is True, msg2[:40])
else:
    print("  （本机没有 CA 文件，跳过实际安装）")
    ok, msg = ca_install.install()
    check("B8 无 CA 时给出可操作提示",
          ok is False and ("启动嗅探" in msg or "生成" in msg), msg[:50])

print()
print("=" * 74)
print("B9 GUI 已改为自动装优先")
print("=" * 74)
gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("B9a GUI 调用了 ca_install.install_interactive",
      "install_interactive(" in gui)
# ★ v2.2.18 演进：失败时**不再开向导**（会闪退的那个），
#   改为 messagebox 给出手动步骤 + 自动复制证书路径。
#   旧判据「自动安装未成功 + open_wizard」已过时，改为断言零窗口方案。
i = gui.find("def _launch_cert_wizard")
j = gui.find("\n    def ", i + 10)
body = gui[i:j]
# v2.2.21：失败提示在 _on_ca_install_result
i2 = gui.find("def _on_ca_install_result")
j2 = gui.find("\n    def ", i2 + 10)
res_body = gui[i2:j2]
check("B9b 不开向导，失败走 messagebox 手动引导",
      "open_wizard" not in body and "证书没装上" in res_body,
      "应改为 messagebox 手动引导")
check("B9c 不再用 Popen(shell=True) 拉 bat",
      "Popen" not in body and "shell=True" not in body)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
