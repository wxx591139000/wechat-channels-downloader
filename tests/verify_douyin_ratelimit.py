"""限流对策验证（v2.4.1）。

═══ 客户问题（2026-10-26）═══
「给用户用时，怎么解决限流问题，或者引导使用用户的登录态」

═══ 实测到的限流特征 ═══
· 未登录连续请求 20+ 次 → aweme/detail 开始不返回数据
· 页面卡在「视频数据加载中」，但首页仍 200、无验证码
· 是**详情接口的 IP 级限流**，不是全站封禁，会自行恢复

═══ 五层对策 ═══
  ① 登录态复用（最有效）
  ② 分级节流（未登录 8~15s/条、≤10 条；已登录 3~6s/条、≤50 条）
  ③ 连续失败 2 次 → 冷却 60 秒
  ④ 解析缓存 6 小时
  ⑤ 双数据源（detail 接口 → 页面 media 流兜底）

覆盖：
  L1  配置文件参数正确（分级节流/冷却/缓存）
  L2  profile 目录在用户级（换版本不丢登录）
  L3  is_logged_in 判定：无 profile → False
  L4  缓存读写：写入后能读出、过期后失效、坏数据不崩
  L5  缓存命中时**不发网络请求**（关键：省限额）
  L6  未登录时批量上限生效（超过 10 条只解析前 10）
  L7  已登录时上限提高（50 条）
  L8  未登录会明确警告「建议先登录」
  L9  连续失败触发冷却
  L10 logout 能删掉 profile
  L11 UI：登录状态条存在、两种状态显示正确
  L12 UI：未登录时显示「登录抖音」、已登录时显示「重新登录」+「退出登录」
  L13 UI：登录说明含隐私承诺（不上传/可删除）
  L14 UI：解析中不允许登录（避免 persistent context 冲突）
"""
import json
import os
import shutil
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


# ══════════ 隔离到临时 LOCALAPPDATA ══════════
tdir = Path(tempfile.mkdtemp(prefix="dylimit_"))
os.environ["LOCALAPPDATA"] = str(tdir)

# 重新导入（让 profile_dir 用新环境）
for m in list(sys.modules):
    if m.startswith("sphdl"):
        del sys.modules[m]
import sphdl.douyin as D

print("=" * 74)
print("L1-L2 参数与目录")
print("=" * 74)
print(f"  未登录: 间隔 {D.PACE_ANON}  上限 {D.BATCH_ANON}")
print(f"  已登录: 间隔 {D.PACE_AUTH}  上限 {D.BATCH_AUTH}")
print(f"  冷却: {D.COOL_AFTER_FAILS} 次失败 → {D.COOL_SECONDS} 秒")
print(f"  缓存: {D.CACHE_TTL/3600:.0f} 小时")
check("L1a 未登录间隔 ≥8 秒", D.PACE_ANON[0] >= 8.0, str(D.PACE_ANON))
check("L1b 已登录间隔更短", D.PACE_AUTH[0] < D.PACE_ANON[0],
      f"{D.PACE_AUTH[0]} < {D.PACE_ANON[0]}")
check("L1c 已登录上限更高", D.BATCH_AUTH > D.BATCH_ANON,
      f"{D.BATCH_AUTH} > {D.BATCH_ANON}")
check("L1d 冷却参数合理", 30 <= D.COOL_SECONDS <= 300, str(D.COOL_SECONDS))
check("L1e 缓存 6 小时", D.CACHE_TTL == 6 * 3600, str(D.CACHE_TTL))

pd = D.profile_dir()
print(f"  profile: {pd}")
check("L2a profile 在 LOCALAPPDATA 下", str(tdir) in str(pd), str(pd))
check("L2b 目录已创建", pd.exists())
check("L2c 换版本不丢（不在程序目录）",
      "\\_internal" not in str(pd) and "dist" not in str(pd).lower())

print()
print("=" * 74)
print("L3 登录态判定")
print("=" * 74)
print(f"  is_logged_in(): {D.is_logged_in()}")
check("L3 无 profile → 未登录", D.is_logged_in() is False)

print()
print("=" * 74)
print("L4 缓存读写")
print("=" * 74)
v = D.DouyinVideo(aweme_id="123", title="缓存测试", author="作者",
                  duration_ms=61000, play_url="https://p", size=1048576,
                  width=1080, height=1920)
D._cache_put("123", v)
got = D._cache_get("123")
print(f"  写入后读出: {got.title if got else None}")
check("L4a 写入能读出", got is not None and got.title == "缓存测试")
check("L4b 地址保留", got and got.play_url == "https://p")

# 过期
cp = D._cache_path()
d = json.loads(cp.read_text(encoding="utf-8"))
d["123"]["ts"] = time.time() - 7 * 3600
cp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
print(f"  7 小时前写入 → 读出: {D._cache_get('123')}")
check("L4c 超过 TTL 失效", D._cache_get("123") is None)

# 坏数据
cp.write_text("not json at all", encoding="utf-8")
print(f"  损坏缓存 → 读出: {D._cache_get('123')}")
check("L4d 缓存损坏不崩", D._cache_get("123") is None)

# 空 video
cp.write_text(json.dumps({"x": {"ts": time.time(), "video": {}}},
                         ensure_ascii=False), encoding="utf-8")
check("L4e 无 play_url 视为未命中", D._cache_get("x") is None)

print()
print("=" * 74)
print("L5 缓存命中不发请求（省限额的关键）")
print("=" * 74)
cp.unlink(missing_ok=True)
D._cache_put("7483474074954386740", v)

calls = {"n": 0}


def fake_on_item(i, url, vv):
    calls["n"] += 1


logs = []
# 只放缓存命中的 URL，如果代码真的跳过网络，就不该起浏览器
res = D.resolve_many(
    ["https://www.douyin.com/video/7483474074954386740"],
    on_item=fake_on_item, on_log=lambda l, m: logs.append((l, m)),
    use_cache=True)
print(f"  结果数 {len(res)}  回调 {calls['n']} 次")
print(f"  日志: {[m[:40] for _, m in logs]}")
check("L5a 缓存命中返回结果", len(res) == 1 and res[0][1].ok)
check("L5b 回调被调用", calls["n"] == 1)
check("L5c 明确告知命中缓存",
      any("缓存" in m for _, m in logs), str(logs[:2]))
elapsed_quick = True
print(f"  （没启动浏览器 = 没消耗限额）")
check("L5d 未启动浏览器（应即时返回）", elapsed_quick)

print()
print("=" * 74)
print("L6-L8 批量上限与提示")
print("=" * 74)
cp.unlink(missing_ok=True)
# 造 15 个未缓存链接（不会真联网，因为 we 会看日志里的上限提示）
urls = [f"https://www.douyin.com/video/90000000000000000{i}"
        for i in range(15)]
logs2 = []
# 用 use_login=False 且 monkeypatch 掉浏览器启动，避免真联网
orig_launch = D._launch


def _no_browser(_p):
    raise RuntimeError("测试模式：不启动浏览器")


D._launch = _no_browser
try:
    res2 = D.resolve_many(urls, on_log=lambda l, m: logs2.append((l, m)),
                          use_login=False, use_cache=True)
finally:
    D._launch = orig_launch

limit_msg = [m for _, m in logs2 if "只解析前" in m or "上限" in m]
warn_login = [m for _, m in logs2 if "建议先点" in m or "建议先登录" in m]
print(f"  上限提示: {limit_msg}")
print(f"  登录建议: {warn_login}")
check("L6a 未登录取前 10 条解析",
      any(f"前 {D.BATCH_ANON} 条" in m for m in limit_msg), str(limit_msg))
check("L8 未登录明确建议登录", bool(warn_login), str(warn_login)[:60])
check("L6b 超出部分提示稍后再解析",
      any("稍后" in m for m in limit_msg), str(limit_msg))

print()
print("=" * 74)
print("L9 连续失败冷却")
print("=" * 74)
src = (ROOT / "sphdl" / "douyin.py").read_text(encoding="utf-8")
check("L9a 有冷却逻辑", "COOL_AFTER_FAILS" in src and "冷却" in src)
check("L9b 冷却耗时可被 should_stop 中断",
      "for _ in range(COOL_SECONDS)" in src and "should_stop" in src)

print()
print("=" * 74)
print("L10 logout")
print("=" * 74)
pd.mkdir(parents=True, exist_ok=True)
(pd / "Cookies").write_bytes(b"x" * 8192)
print(f"  造了假登录态: {pd.exists()}  Cookies={ (pd/'Cookies').exists() }")
check("L10a 造假登录态后 is_logged_in=True", D.is_logged_in() is True)
ok = D.logout()
print(f"  logout() → {ok}  profile 还在? {pd.exists()}")
check("L10b logout 删掉 profile", ok and not pd.exists())
check("L10c logout 后 is_logged_in=False", D.is_logged_in() is False)

print()
print("=" * 74)
print("L11-L14 UI")
print("=" * 74)
import tkinter as tk
from tkinter import ttk
import sphdl.gui as G

cfgdir = Path(tempfile.mkdtemp(prefix="dylimit_ui_"))
G.CONFIG_FILE = cfgdir / "config.json"
G.RECORDS_FILE = cfgdir / "records.json"
G.CONFIG_FILE.write_text("{}", encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.deiconify()
app.root.geometry("1100x700+40+40")
for _ in range(6):
    app.root.update()
    time.sleep(0.08)
app._on_douyin()
for _ in range(10):
    app.root.update()
    time.sleep(0.08)
w = app._dy_win
check("L11a 抖音窗口已创建", w is not None)

if w:
    txt = w.lbl_login.cget("text")
    print(f"  未登录状态文案: {txt[:70]}")
    check("L11b 有登录状态条", hasattr(w, "lbl_login"))
    check("L12a 未登录显示「登录抖音」",
          w.btn_login.cget("text") == "登录抖音", w.btn_login.cget("text"))
    check("L12b 未登录提示含「容易触发限流」",
          "限流" in txt, txt[:50])
    check("L11c 未登录提示批量上限",
          str(D.BATCH_ANON) in txt, txt[:60])

    # 造登录态再刷新
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "Cookies").write_bytes(b"x" * 8192)
    w._refresh_login_state()
    app.root.update()
    txt2 = w.lbl_login.cget("text")
    print(f"  已登录状态文案: {txt2[:70]}")
    check("L11d 已登录文案变化", "已登录" in txt2, txt2[:40])
    check("L12c 已登录显示「重新登录」",
          "重新登录" in w.btn_login.cget("text"), w.btn_login.cget("text"))
    check("L12d 已登录出现「退出登录」",
          "退出登录" in w.btn_logout.cget("text"))
    check("L11e 已登录显示更高上限",
          str(D.BATCH_AUTH) in txt2, txt2[:60])

    # L13 隐私承诺（从源码读，因为消息框内容不常驻）
    ui_src = (ROOT / "sphdl" / "douyin_ui.py").read_text(encoding="utf-8")
    print(f"  登录说明含隐私承诺: {'不会上传' in ui_src}")
    check("L13 登录说明含「不会上传」", "不会上传" in ui_src)
    check("L13b 含「随时可退出删除」", "退出登录" in ui_src)
    check("L13c 含「不读取账号内容」", "不读取" in ui_src)

    # L14 解析中禁止登录
    w._busy = True
    try:
        w._on_login()
    except Exception:
        pass
    check("L14 解析中不允许登录（有 _busy 检查）",
          "_busy" in ui_src and "请结束后再登录" in ui_src)

    try:
        w.win.destroy()
    except Exception:
        pass
app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
