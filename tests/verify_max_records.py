"""列表上限档位 + 授权码 —— 根因/修复 的完整程序化验证（v2.0.17）。

策略（用户 2026-10-16 确认）：
  · 档位由发布方掌控，**客户端不可自行配置**（界面无档位下拉）
  · 通道①：授权码解锁（sphdl/license.py，客户在界面输入）
  · 通道②：构建预置档位写死（release.py --max-records）
  · **满档 = 停止加载后续视频**（不顶掉已有记录）；不限量档持续加载

段 A（无 GUI，RecordStore 真实代码路径）
  A1 三档各自：加到上限 → 满；第 N+1 条被拒收（返回 False），最旧记录仍在
  A2 不限量档（0）：3000 条全部入库、永不拒收（持续加载）
  A3 下调档位：裁掉最旧、数量正确、剩余为最新
  A4 原子落盘：写失败不留半截文件、旧文件完好、不留 .tmp
  A5 2 万条规模：插入 + 落盘耗时
  A6 启动裁剪计数（600 条 + 500 档 → 保留 500、裁掉 100）
  A7 档位文案

段 B（授权码机制）
  B1 4 档往返生成/校验一致
  B2 篡改/乱码/空码一律拒绝
  B3 大小写 + 去连字符容错
  B4 有效期解析（永久 / N 天自首次激活 / 过期判定）
  B5 未注入密钥时一律无效（源码泄漏也造不出码）

段 C（真 Tk 界面，App 端到端）
  C1 满档停止加载：行数 == store 条数 == 上限；最旧记录仍在；状态栏「已满」
  C2 被拒视频不进 _url_to_rid / _vid_index
  C3 授权码解锁更高档 → 恢复加载（满档期间被拒的视频可重新入库）
  C4 满档提示只出现一次；满档自动停止批量拉取
  C5 不限量档持续加载；下调档位裁掉最旧且不动磁盘文件
  C6 授权码持久化到 config.json，重启后档位仍生效
  C7 无效授权码不改动档位
  C8 预置锁档版本：档位写死、界面隐藏授权入口、标题带档位
  C9 addon 层：GUI 拒收 → 撤回「已见」标记

运行: ..\.venv\Scripts\python.exe tests\verify_max_records.py
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sphdl.gui as g                      # noqa: E402
from sphdl import config, license as lic    # noqa: E402
from sphdl.entities import VideoData        # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'[PASS]' if cond else '[FAIL]'} {name} {detail}")
    if not cond:
        FAIL.append(name)


def mkrec(i, status="waiting"):
    return g.Record(id=f"id{i:05d}", title=f"t{i}",
                    url=f"https://finder.video.qq.com/stodownload?encfilekey=E{i:05d}",
                    decode_key="k", size=1, source="test",
                    created="10-16 00:00:00", status=status,
                    video_id=f"vid{i:05d}")


# ================================ 段 A ================================
def part_a():
    print("\n=========== 段 A：RecordStore（真实代码路径） ===========")
    tmpdir = Path(tempfile.mkdtemp())

    for cap in (500, 1000, 2000):
        st = g.RecordStore(tmpdir / f"records_{cap}.json", cap)
        for i in range(cap):
            st.add(mkrec(i))
        full = st.is_full()
        extra = st.add(mkrec(cap))
        check(f"A1 档位 {cap}: 满档后拒收第 {cap + 1} 条、旧记录保留",
              full and extra is False and len(st.items) == cap
              and len(st.order) == cap and "id00000" in st.items,
              f"full={full} add_ret={extra} items={len(st.items)}")

    st = g.RecordStore(tmpdir / "records_flagship.json", 20000)
    ok_all = all(st.add(mkrec(i)) for i in range(3000))
    check("A2 旗舰版（2 万条）: 3000 条全部入库、未满（远低于上限）",
          ok_all and len(st.items) == 3000 and not st.is_full(),
          f"items={len(st.items)}")

    removed = st.set_max_records(1000)
    check("A3 下调 3000→1000: 裁掉最旧 2000 条、保留最新 1000 条",
          len(removed) == 2000 and len(st.items) == 1000
          and st.order[0] == "id02999" and st.order[-1] == "id02000",
          f"removed={len(removed)} remain={len(st.items)} tail={st.order[-1]}")
    check("A3b 恰满档位视为满：拒收新记录",
          st.is_full() and st.add(mkrec(9999)) is False)
    st.set_max_records(2000)
    check("A3c 调大档位后恢复加载", not st.is_full() and st.add(mkrec(9999)) is True)

    p2 = tmpdir / "atomic.json"
    st2 = g.RecordStore(p2, 500)
    for i in range(10):
        st2.add(mkrec(i))
    check("A4a 首次落盘成功", st2.save() and p2.exists())
    before = p2.read_text(encoding="utf-8")
    st2b = g.RecordStore(p2, 500)           # 旧文件可正常读取（向后兼容）
    real_dumps = g.json.dumps
    g.json.dumps = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("注入写入失败"))
    ok_fail = (st2.save() is False)
    g.json.dumps = real_dumps
    check("A4b 写失败返回 False、旧文件完好（无半截 JSON）、旧格式可读",
          ok_fail and p2.read_text(encoding="utf-8") == before
          and len(st2b.items) == 10 and not (tmpdir / "atomic.json.tmp").exists())

    big = g.RecordStore(tmpdir / "big.json", 0)
    t0 = time.perf_counter()
    for i in range(20000):
        big.add(mkrec(i))
    t_add = time.perf_counter() - t0
    t0 = time.perf_counter()
    big.save()
    t_save = time.perf_counter() - t0
    size_mb = (tmpdir / "big.json").stat().st_size / 1048576
    print(f"      实测：插入 20000 条 {t_add:.2f}s，落盘 {t_save:.2f}s，"
          f"records.json {size_mb:.1f} MB")
    check("A5 不限档 2 万条：插入 <3s 且落盘 <3s", t_add < 3.0 and t_save < 3.0,
          f"add={t_add:.2f}s save={t_save:.2f}s")

    p3 = tmpdir / "trim.json"
    st3 = g.RecordStore(p3, 1000)
    for i in range(600):
        st3.add(mkrec(i))
    st3.save()
    st4 = g.RecordStore(p3, 500)
    check("A6 启动裁剪：600 条 + 500 档 → 保留 500、裁掉 100",
          len(st4.order) == 500 and st4.trimmed_on_load == 100,
          f"trimmed_on_load={st4.trimmed_on_load}")
    check("A7 档位文案（轻量/标准/旗舰/至尊）",
          config.label_of(20000) == "至尊（2 万条）"
          and config.label_of(1000) == "标准（1000 条）"
          and lic.label_of_tier(2000) == "旗舰（2000 条）"
          and config.name_of(500) == "轻量",
          f"labels={[config.label_of(t) for t in (500, 1000, 2000, 20000)]}")


# ================================ 段 B ================================
def part_b():
    print("\n=========== 段 B：授权码机制 ===========")
    check("B1a 授权密钥已注入（作者侧/构建产物）", lic.license_available())
    if not lic.license_available():
        check("B1 4 档往返", False, "缺 .license_secret / _lic_key.py")
        return
    ok = True
    for tier in (500, 1000, 2000, 20000):
        code = lic.make_code(tier)
        info = lic.verify_code(code)
        ok = ok and bool(info) and info["tier"] == tier
        print(f"      档位 {lic.label_of_tier(tier):<5} → {code} (len={len(code)})")
    check("B1b 4 档往返生成/校验一致", ok)
    check("B1c 码长度 ≤ 40 字符（便于微信/短信传抄）",
          len(lic.make_code(1000)) <= 40, f"len={len(lic.make_code(1000))}")

    code = lic.make_code(1000, days=365)
    info = lic.verify_code(code)
    check("B2a 有效期解析（365 天 → 360，粒度 8 天）",
          bool(info) and info["days"] == 360, f"{info}")
    check("B2b 永久码 days=0", lic.verify_code(lic.make_code(500))["days"] == 0)
    now = time.time()
    check("B2c 未过期 → False", lic.license_expired(360, now - 10 * 86400) is False)
    check("B2d 已过期 → True", lic.license_expired(360, now - 400 * 86400) is True)
    check("B2e 永久码永不判过期", lic.license_expired(0, now - 9999 * 86400) is False)

    bad_last = code[:-1] + ("A" if code[-1] != "A" else "B")
    check("B3a 篡改末位被拒", lic.verify_code(bad_last) is None, bad_last)
    body = code.replace("-", "")
    check("B3b 篡改载荷（改档位字符）被拒",
          lic.verify_code("SPHDL-" + "1" + body[len(lic.CODE_PREFIX) + 1:]) is None)
    check("B3c 乱码/空码被拒",
          lic.verify_code("SPHDL-！！！") is None and lic.verify_code("") is None)
    check("B3d 小写 + 去连字符 + 空格容错",
          lic.verify_code(code.lower().replace("-", " ")) is not None)
    check("B3e 混淆自反（密钥注入格式）",
          lic.obfuscate(lic.obfuscate(b"\x01\x02\x03")) == b"\x01\x02\x03")

    # B5 无密钥时一律无效（模拟源码泄漏场景：无 _lic_key.py）
    snippet = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import sphdl.license as lic\n"
        "lic._secret_cache = None\n"
        "import types, sys as s\n"
        "s.modules['sphdl._lic_key'] = None      # 阻断密钥加载\n"
        "lic.get_secret.__globals__['_secret_cache'] = b''\n"
        "print('NO_KEY_RESULT', lic.license_available(), lic.verify_code(\n"
        "      'SPHDL-0410-1Y81-TNVY-9YZB-RCVA-2GTR'))\n" % ROOT
    )
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    line = next((l for l in (r.stdout or "").splitlines()
                 if l.startswith("NO_KEY_RESULT")), "")
    check("B5 未注入密钥时任何码都无效（源码泄漏也造不出码）",
          "NO_KEY_RESULT False None" in line, line or (r.stderr or "")[-150:])


# ================================ 段 C ================================
def part_c():
    print("\n=========== 段 C：App 端到端（真 Tk） ===========")
    tmpdir = Path(tempfile.mkdtemp())
    g.RECORDS_FILE = tmpdir / "records.json"
    g.CONFIG_FILE = tmpdir / "config.json"
    g.CONFIG_FILE.write_text(json.dumps({"auto_download": False},
                                        ensure_ascii=False), encoding="utf-8")
    # 测试用小档（与 500/1000/2000 完全同一代码路径）：覆盖「未授权默认档」
    g.config.build_default_max_records = lambda: 5
    g.config.locked_max_records = lambda: None

    app = g.App()
    app.root.withdraw()

    def pump():
        import queue as _q
        try:
            while True:
                app._append_log(*app.log_q.get_nowait())
        except _q.Empty:
            pass
        try:
            while True:
                app._handle_event(app.ui_q.get_nowait())
        except _q.Empty:
            pass
        app._update_status_bar()
        app.root.update()

    def rows():
        return len(app.tree.get_children())

    def vd(i):
        return VideoData(
            url=f"https://finder.video.qq.com/stodownload?encfilekey=K{i:05d}",
            decode_key="k", size=100, description=f"视频{i}", video_id=f"BVID{i:05d}")

    def capture(i):
        accepted = app._on_capture(vd(i), "test")
        pump()
        return accepted

    check("C0 未授权：档位 = 默认档 5、界面显示「未授权 · 默认档」",
          app.store.max_records == 5 and "未授权" in app.limit_hint.cget("text"),
          f"tier={app.store.max_records} hint={app.limit_hint.cget('text')[:40]}")

    rets = [capture(i) for i in range(8)]
    check("C1 满档后不再加载：行数 == store 条数 == 上限",
          rows() == 5 == len(app.store.order),
          f"tree={rows()} store={len(app.store.order)}")
    check("C1b 前 5 条入库、后 3 条被拒收", rets == [True] * 5 + [False] * 3,
          f"rets={rets}")
    oldest = app.store.items[app.store.order[-1]]
    check("C1c 最旧记录仍在（满档不顶掉旧记录）", oldest.title == "视频0",
          f"最旧={oldest.title}")
    check("C1d 状态栏显示「已满·未加载」",
          "已满·未加载 3 条" in app.status_bar.cget("text")
          and f"共 5 条 / 上限 {config.label_of(5)}" in app.status_bar.cget("text"),
          repr(app.status_bar.cget("text")[-70:]))
    stale = [u for u, r in app._url_to_rid.items() if r not in app.store.items]
    check("C2 被拒收的视频没有进入 _url_to_rid / _vid_index",
          not stale and app._vid_index == {f"BVID{i:05d}" for i in range(5)},
          f"stale={len(stale)} vid_index={len(app._vid_index)}")

    # C3 授权码解锁 2000 档 → 恢复加载
    ok_act = app._activate_license(lic.make_code(2000))
    check("C3 授权码解锁更高档：档位立即提升 + 恢复加载",
          ok_act and app.store.max_records == 2000
          and "已授权" in app.limit_hint.cget("text"), 
          f"tier={app.store.max_records} hint={app.limit_hint.cget('text')[:40]}")
    resumed = capture(5)
    check("C3b 满档期间被拒的视频可重新入库",
          resumed is True and len(app.store.order) == 6
          and app.store.items[app.store.order[0]].title == "视频5",
          f"accepted={resumed} count={len(app.store.order)}")

    warns = [l for l in app.log_text.get("1.0", "end").splitlines() if "列表已满" in l]
    check("C4 满档提示只出现一次", len(warns) == 1, f"warns={len(warns)}")

    # C4b 满档自动停批量（用内部小档压到快满）
    n0 = len(app.store.order)
    app.store.set_max_records(n0 + 3)
    app._limit_warned = False
    app._full_event_pending = False
    app.addon.batch_mode = True
    app.addon._batch_active = True
    for i in range(10):
        capture(300 + i)
    check("C4b 满档时批量拉取被自动停止",
          len(app.store.order) == n0 + 3 and app.addon.batch_mode is False
          and app.addon._batch_active is False,
          f"count={len(app.store.order)} batch={app.addon.batch_mode}")

    # C5 旗舰版（2 万条）容量充足：可继续加载
    ok_unl = app._activate_license(lic.make_code(20000))
    before_n = len(app.store.order)
    for i in range(30):
        capture(200 + i)
    check("C5a 授权「旗舰版（2 万条）」：档位 20000、可继续加载（30 条全入库）",
          ok_unl and app.store.max_records == 20000
          and "满 20000 条" in app.limit_hint.cget("text")[:40]
          and len(app.store.order) == before_n + 30,
          f"{before_n} → {len(app.store.order)} tier={app.store.max_records}")

    # C5b 降档裁剪（500 档）：先到 505 条
    need = 505 - len(app.store.order)
    for i in range(need):
        capture(1000 + i)
    probe = tmpdir / "downloaded.mp4"
    probe.write_bytes(b"x" * 1024)
    app.store.items[app.store.order[0]].file = str(probe)
    n505 = len(app.store.order)
    app._activate_license(lic.make_code(500))
    pump()
    trim_log = [l for l in app.log_text.get("1.0", "end").splitlines()
                if "裁掉最旧" in l]
    check("C5b 授权 500 档：裁掉最旧 + 磁盘文件不受影响",
          n505 == 505 and len(app.store.order) == 500 and rows() == 500
          and probe.exists() and probe.stat().st_size == 1024 and trim_log,
          f"{n505} → {len(app.store.order)} tree={rows()} log={len(trim_log)}")

    # C6 授权持久化 + 重启生效
    cfg = json.loads(g.CONFIG_FILE.read_text(encoding="utf-8"))
    # ★ v2.0.19 起授权按产品线分槽存 license_codes={产品:码}，
    #   license_activated_at 也随之变成 dict → 不能用 >0 比较
    _codes = cfg.get("license_codes") or {}
    _ats = cfg.get("license_activated_at") or {}
    check("C6a 授权码写入 config.json（分槽存储）",
          bool(_codes.get("1") or cfg.get("license_code"))
          and (isinstance(_ats, dict) and bool(_ats.get("1"))
               or isinstance(_ats, (int, float)) and _ats > 0),
          f"codes={list(_codes)[:3]} ats={type(_ats).__name__}")
    app2 = g.App()
    app2.root.withdraw()
    check("C6b 重启后档位仍为授权档（500）", app2.store.max_records == 500,
          f"tier={app2.store.max_records}")
    app2.root.destroy()

    # C7 无效授权码不改动档位
    before_tier = app.store.max_records
    ok_bad = app._activate_license("SPHDL-0000-0000-0000-0000-0000-0000")
    check("C7 无效授权码被拒且档位不变",
          ok_bad is False and app.store.max_records == before_tier,
          f"tier={app.store.max_records}")

    app.root.destroy()

    # C8 预置锁档版本（子进程真跑）：档位写死 + 隐藏授权入口 + 标题带档位
    snippet = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import sphdl.gui as g\n"
        "g.config.locked_max_records = lambda: 1000\n"
        "import json, tempfile\n"
        "from pathlib import Path\n"
        "tmp = Path(tempfile.mkdtemp())\n"
        "g.RECORDS_FILE = tmp/'records.json'; g.CONFIG_FILE = tmp/'config.json'\n"
        "app = g.App(); app.root.withdraw()\n"
        "has_btn = bool(app.btn_license.winfo_manager())\n"
        "print('LOCKED', app.store.max_records, has_btn, app.root.title())\n"
        "print('TIER_TEXT', app.limit_value_lbl.cget('text'), '|',\n"
        "      app._tier_source_text())\n"
        "app.root.destroy()\n" % ROOT
    )
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=90)
    out = r.stdout or ""
    line = next((l for l in out.splitlines() if l.startswith("LOCKED")), "")
    check("C8 预置锁档版本：档位写死 1000、隐藏授权按钮、标题含「·标准（1000 条）」",
          "LOCKED 1000 False" in line and "·标准（1000 条）" in line
          and "版本预置" in out,
          line or (r.stderr or "")[-200:])

    # C9 addon 层：GUI 拒收 → 撤回「已见」标记
    from sphdl.addon import SphAddon
    from sphdl.pipeline import Pipeline
    pipe = Pipeline(str(tmpdir / "dl"), on_event=lambda *a: None)

    class FakeGUI:
        def __init__(self, accept_n):
            self.remaining = accept_n
            self.calls = []

        def __call__(self, v, source):
            self.calls.append(v.video_id)
            if self.remaining > 0:
                self.remaining -= 1
                return True
            return False

    def mkvid(n):
        return VideoData(url=f"https://x/stodownload?encfilekey=Z{n}", decode_key="k",
                         description=f"v{n}", video_id=f"VID{n}")

    g1 = FakeGUI(1)
    ad = SphAddon(pipe, on_capture=g1, auto_download=False)
    ad.natural_capture = False
    ad._handle(mkvid(1), source="test")
    ad._handle(mkvid(1), source="test")      # 去重命中，不再回调
    g2 = FakeGUI(2)
    ad2 = SphAddon(pipe, on_capture=g2, auto_download=False)
    ad2.natural_capture = False
    ad2._handle(mkvid(1), source="test")
    ad2._handle(mkvid(2), source="test")
    ad2._handle(mkvid(3), source="test")     # 拒收 → seen 撤回
    check("C9 GUI 拒收时 addon 撤回「已见」标记（升级档位后可再捕获）",
          "VID1" in ad.seen and len(g1.calls) == 1
          and ad2.seen == {"VID1", "VID2"} and ad2.rejected_count == 1,
          f"seen={sorted(ad2.seen)} rejected={ad2.rejected_count}")


def main():
    print(f"档位可选值={config.MAX_RECORDS_TIERS} 默认档={config.DEFAULT_MAX_RECORDS} "
          f"授权功能={'可用' if lic.license_available() else '不可用'}")
    part_a()
    try:
        part_b()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("B 段执行", False, f"异常: {e}")
    try:
        part_c()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("C 段执行", False, f"异常: {e}")
    print("\nRESULT:", "FAIL " + ",".join(FAIL) if FAIL else "ALL PASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
