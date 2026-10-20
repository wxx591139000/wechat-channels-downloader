"""停止嗅探 vs 正在下载：根因验证与修复验收（v2.2.0）。

用户反馈：
  「客户抓好了想要的视频清单后，不想继续抓，于是他关了嗅探，
   但发现原来在下载的视频也停掉了」

═══ 根因链条（实测确认）═══

  ① 开嗅探 → ProxyManager 把系统代理设为 127.0.0.1:8899
  ② 下载器用 requests（trust_env=True）→ **自动走系统代理** → 流量经 mitmproxy
  ③ 「停止嗅探」→ SniffEngine.stop() → pm.cleanup() 恢复系统代理
     + mitmproxy 进程退出
  ④ ★ already-established 的 TCP 连接由 mitmproxy 承载，进程退出即被 RST
  ⑤ requests 抛 ConnectionError → downloader 进续传分支
  ⑥ 续传时系统代理已恢复（通常变成"无代理"），但**URL 仍可直连**
     → 理论上能续上；但如果客户网络需要代理才能出网，就续不上
  ⑦ ★★ 8 次续传用尽 → finally 块 **删除 .tmp 临时文件** → 已下载部分全丢

  所以客户的体验是："下载停了，而且进度没了，得从头再下"。

═══ 修复 ═══

  D1 停止嗅探时**不再**立刻恢复系统代理，而是等下载队列清空再恢复
     （或提示客户"还有 N 个任务在下载，是否一起停？"）
  D2 失败时**保留 .tmp**（不删），下次可续传
  D3 停止嗅探时若仍有下载任务，界面明确提示

本测试验证 D1/D2 的代码行为。
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl.downloader as DL
import sphdl.gui as G

print("=" * 74)
print("D2 失败时是否保留 .tmp（决定能否续传）")
print("=" * 74)
src = inspect.getsource(DL.MpdDownloader._download_streaming)
has_keep = hasattr(DL.MpdDownloader, "_keep_tmp")
print(f"  有 _keep_tmp() 判定方法: {has_keep}")
check("D2a ★失败时保留 .tmp（有 _keep_tmp 分支）", has_keep,
      "保留断点文件 → 下次可 Range 续传")

# 行为验证：_keep_tmp 的两种语义
if has_keep:
    class _Fake(DL.MpdDownloader):
        def __init__(self, cancelled):
            self._c = cancelled
        def _cancelled(self):
            return self._c
    # 用户主动停 → 不保留
    f1 = _Fake(cancelled=True)
    r1 = f1._keep_tmp()
    # 网络故障（未主动停）→ 保留
    f2 = _Fake(cancelled=False)
    r2 = f2._keep_tmp()
    print(f"  主动停止 → 保留 tmp? {r1}（应 False，用户不要了别占磁盘）")
    print(f"  意外中断 → 保留 tmp? {r2}（应 True，可续传）")
    check("D2b 用户主动停止时不保留 .tmp", r1 is False)
    check("D2c 意外中断时保留 .tmp（可续传）", r2 is True)

print()
print("=" * 74)
print("D1 停止嗅探时是否检查下载队列")
print("=" * 74)
src_toggle = inspect.getsource(G.App._toggle_engine)
has_busy_check = ("downloading" in src_toggle and
                  ("busy" in src_toggle or "排队" in src_toggle))
print(f"  _toggle_engine 检查下载任务: {has_busy_check}")
check("D1a ★停止嗅探前检查下载任务", has_busy_check,
      "有任务时先问用户，不直接断代理")

has_pending = hasattr(G.App, "_check_pending_stop")
print(f"  有「等下载完再停」机制: {has_pending}")
check("D1b 支持「等下载完成后自动停止」", has_pending)

print()
print("=" * 74)
print("D3 界面是否有提示")
print("=" * 74)
# 找是否有"还有任务在下载"的提示文案
gui_src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
has_hint = ("还有" in gui_src and "下载" in gui_src and
            ("停止嗅探" in gui_src))
print(f"  gui.py 含相关提示文案: {has_hint}")
check("D3a 停止嗅探前应有明确提示", has_hint)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
print()
print("注：本测试用于「先红后绿」——修复前应有 FAIL，修复后应全 PASS。")
sys.exit(0 if ok_n == len(R) else 1)
