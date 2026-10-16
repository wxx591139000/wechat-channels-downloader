#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.0.17 档位/授权端到端验收（D/E/P 段）—— 与 verify_max_records.py 互补。

覆盖用户 2026-10-16 追加的三条要求：
  D 段：满档 → 客户删记录 → 列表低于上限 → 能继续加载新视频
        （小套餐客户勤删记录即可长期使用）
  E 段：换授权码立即生效、无需重启（升级/降级/清除/无效码不变量）
  P 段：满档时已有记录绝不消失（捕获拒收/重启/换码/删记录四路径）

数据隔离：patch gui 模块级 RECORDS_FILE / CONFIG_FILE 到临时目录，
不碰真实 records.json（verify_max_records.py 用的是同一套手法）。

跑法（在 sphdl/ 下）：
    ..\\.venv\\Scripts\\python.exe tests\\verify_tier_reload.py
退出码 0 = 全通过。
"""
import sys
import json
import time
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # sphdl/
sys.path.insert(0, str(ROOT))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


class VD:
    """模拟 addon 投递的 VideoData。"""

    def __init__(self, n):
        self.url = f"http://x/v{n}"
        self.video_id = f"vid{n}"
        self.description = f"视频{n}"
        self.decode_key = "k"
        self.size = 100
        self.is_image = False
        self.renditions = []
        self.quality_note = ""


def make_app(tmpdir):
    """建一个数据隔离的 App 实例 + pump/capture 工具函数。"""
    import tkinter as tk
    r = tk.Tk()
    r.withdraw()
    r.destroy()

    import sphdl.gui as G
    import sphdl.config as C
    G.RECORDS_FILE = tmpdir / "records.json"
    G.CONFIG_FILE = tmpdir / "config.json"
    C.PROXY_BACKUP_FILE = tmpdir / "proxy_backup.json"
    (tmpdir / "config.json").write_text(json.dumps({
        "port": 8899, "download_dir": str(tmpdir / "downloads"),
        "auto_proxy": False, "auto_download": False, "download_images": True,
        "natural_capture": False, "guide_auto_shown": True}), encoding="utf-8")
    return G.App()


def pump(app, ms=140):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.004)


def capture(app, n, wait=True):
    """投一条捕获事件。

    wait=True 时 pump 少量时间让事件被消费。注意：逐条 pump 存在
    _pending_slots 占位与队列消费的时序竞态，批量投递场景请用
    capture_many() 统一 pump，断言更稳。
    """
    ok = app._on_capture(VD(n), "test")
    if wait:
        pump(app)
    return ok


def capture_many(app, ns, pump_ms=900):
    """批量投递后再统一 pump（避免逐条 pump 的时序竞态）。"""
    for n in ns:
        app._on_capture(VD(n), "test")
    pump(app, pump_ms)


def delete_first(app, k):
    rids = list(app.store.order[:k])
    recs = [app.store.items[x] for x in rids]
    for x in rids:
        app.store.remove(x)
    app._drop_records_ui(recs)
    pump(app)


def snapshot(app):
    return {r: (app.store.items[r].title, app.store.items[r].url,
                app.store.items[r].status, app.store.items[r].file)
            for r in app.store.order}


def main():
    from sphdl import config, license as lic

    tmpdir = Path(tempfile.mkdtemp(prefix="sphdl_tier_"))
    app = make_app(tmpdir)
    try:
        # ---------- 前置 ----------
        print("=" * 74)
        print("前置：档位 / 命名 / 数据隔离")
        print("=" * 74)
        check("数据隔离：起始列表为空", len(app.store.order) == 0,
              f"n={len(app.store.order)}")
        check("档位为轻量版 500", app.store.max_records == 500,
              f"max={app.store.max_records}")
        check("4 档为确定数值 (500/1000/2000/20000)",
              tuple(config.MAX_RECORDS_TIERS) == (500, 1000, 2000, 20000),
              f"{config.MAX_RECORDS_TIERS}")
        check("无「不限量」档（label_of(0) 不再是旗舰版）",
              config.label_of(0) != "旗舰版（不限量）" and 0 not in config.MAX_RECORDS_TIERS)
        check("旗舰版标签 = 旗舰版（2 万条）",
              config.label_of(20000) == "旗舰版（2 万条）", config.label_of(20000))

        # ---------- D 段 ----------
        print("\n" + "=" * 74)
        print("D 段：满档 → 删记录 → 列表低于上限 → 继续加载（核心诉求）")
        print("=" * 74)
        for i in range(500):
            app._on_capture(VD(i), "test")
        pump(app, 700)
        check("D1 塞满 500 条", len(app.store.order) == 500,
              f"n={len(app.store.order)}")
        check("D1b is_full = True", app.store.is_full())
        oldest = app.store.order[-1]
        capture_many(app, range(9000, 9003))
        check("D2 满档后 3 条被拒收（列表仍 500）", len(app.store.order) == 500,
              f"n={len(app.store.order)}")
        check("D2b 最旧记录未被顶掉", app.store.order[-1] == oldest)
        check("D2c 拒收计数 = 3", app._rejected_count == 3,
              f"rejected={app._rejected_count}")
        check("D2d 满档已提示", app._limit_warned is True)

        delete_first(app, 100)
        check("D3 删 100 条后列表 = 400", len(app.store.order) == 400,
              f"n={len(app.store.order)}")
        check("D3b 删后 is_full = False", not app.store.is_full())
        check("D3c 删后满档门闩复位（否则第二轮满档静默）",
              app._limit_warned is False and app._full_event_pending is False,
              f"warned={app._limit_warned} pending={app._full_event_pending}")
        check("D3d 删后拒收计数清零", app._rejected_count == 0,
              f"rejected={app._rejected_count}")
        check("D3e 界面行数与 store 一致",
              len(app.tree.get_children()) == 400,
              f"tree={len(app.tree.get_children())}")
        check("D3f 去重索引与列表一致", len(app._vid_index) == 400,
              f"idx={len(app._vid_index)}")

        capture_many(app, range(5000, 5100))
        check("D4 删后 100 条新视频全部入库", len(app.store.order) == 500,
              f"n={len(app.store.order)}")
        check("D4b 列表回到 500", len(app.store.order) == 500,
              f"n={len(app.store.order)}")

        # 循环可用性
        capture_many(app, [6000], pump_ms=400)
        check("D5 第二轮满档同样拒收",
              len(app.store.order) == 500 and app._rejected_count == 1,
              f"n={len(app.store.order)} rejected={app._rejected_count}")
        delete_first(app, 200)
        check("D5b 第二轮删 200 → 列表 300", len(app.store.order) == 300,
              f"n={len(app.store.order)}")
        capture_many(app, range(7000, 7200))
        check("D5c 第二轮列表回到 500（200 条全部入库）",
              len(app.store.order) == 500, f"n={len(app.store.order)}")
        delete_first(app, max(1, len(app.store.order)))
        pump(app, 200)
        check("D6 删除全部记录 → 清空", len(app.store.order) == 0,
              f"n={len(app.store.order)}")
        capture_many(app, range(8000, 8500))
        check("D6b 清空后可再满档 500", len(app.store.order) == 500,
              f"n={len(app.store.order)}")

        # ---------- E 段 ----------
        print("\n" + "=" * 74)
        print("E 段：换授权码立即生效（无需重启）")
        print("=" * 74)
        before = app.store.max_records
        t0 = time.time()
        ok1 = app._activate_license(lic.make_code(1000))
        dt = time.time() - t0
        check("E1 换标准版码：激活成功", ok1)
        check("E1b 档位立即变 1000（未重启）", app.store.max_records == 1000,
              f"{before} → {app.store.max_records}，耗时 {dt * 1000:.0f}ms")
        check("E1c 界面授权状态同步", "已授权" in app._tier_source_text(),
              app._tier_source_text())

        app._activate_license(lic.make_code(20000))
        check("E2 换旗舰版码：档位立即 20000", app.store.max_records == 20000,
              f"max={app.store.max_records}")
        capture_many(app, range(9000, 9100))
        check("E2b 原满档的 100 条新视频全部可加载",
              len(app.store.order) == 600, f"n={len(app.store.order)}")

        # 降档裁剪
        app._activate_license(lic.make_code(500))
        check("E3 降档到轻量版：立即裁到 500（未重启）",
              app.store.max_records == 500 and len(app.store.order) == 500,
              f"max={app.store.max_records} n={len(app.store.order)}")

        # 清除授权
        app._activate_license(lic.make_code(1000))
        cleared = app._clear_license()
        check("E4 清除授权：返回 True", cleared)
        check("E4b 清除后回落默认档 500", app.store.max_records == 500,
              f"max={app.store.max_records}")
        check("E4c 授权码已清空", not app.cfg.license_code,
              f"code={app.cfg.license_code!r}")

        # 无效码不变量
        b = app.store.max_records
        ok_bad = app._activate_license("SPHDL-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        check("E5 无效码：激活失败", ok_bad is False)
        check("E5b 无效码：档位不变", app.store.max_records == b,
              f"max={app.store.max_records}")

        # 一码一档
        c1000 = lic.make_code(1000)
        c2000 = lic.make_code(2000)
        check("E6 一码一档：1000 的码解不出 2000",
              lic.verify_code(c1000)["tier"] == 1000
              and lic.verify_code(c2000)["tier"] == 2000)
        tampered = c1000[:-1] + ("A" if c1000[-1] != "A" else "B")
        check("E6b 篡改码被拒", lic.verify_code(tampered) is None)

        # ---------- P 段 ----------
        print("\n" + "=" * 74)
        print("P 段：满档时已有记录绝不消失（用户红线）")
        print("=" * 74)
        app._activate_license(lic.make_code(500))
        delete_first(app, max(1, len(app.store.order)))
        pump(app, 200)
        capture_many(app, range(1000, 1500))
        BEFORE = snapshot(app)
        check("P0 重新塞满 500 条", len(BEFORE) == 500, f"n={len(BEFORE)}")

        capture_many(app, range(9100, 9120), pump_ms=400)
        A1 = snapshot(app)
        check("P1 满档拒收后仍 500 条", len(A1) == 500, f"n={len(A1)}")
        check("P1b 已有记录一条都没消失",
              not [k for k in BEFORE if k not in A1],
              f"消失={[k for k in BEFORE if k not in A1][:5]}")
        check("P1c 已有记录内容未被改动",
              not [k for k in BEFORE if k in A1 and BEFORE[k] != A1[k]])

        # P2 重启
        app._force_save()
        app.root.destroy()
        app = make_app(tmpdir)
        pump(app, 200)
        A2 = snapshot(app)
        check("P2 重启后仍 500 条", len(A2) == 500, f"n={len(A2)}")
        check("P2b 重启后已有记录一条不缺",
              not [k for k in BEFORE if k not in A2],
              f"缺失={[k for k in BEFORE if k not in A2][:5]}")
        check("P2c 重启后仍为满档", app.store.is_full())

        # P3 换码
        app._activate_license(lic.make_code(2000))
        pump(app, 200)
        A3 = {r: app.store.items[r].title for r in app.store.order}
        check("P3 换码后已有 500 条全在",
              all(k in A3 for k in BEFORE),
              f"缺失={[k for k in BEFORE if k not in A3][:5]}")
        check("P3b 换码只增不减（500 条）", len(A3) == 500, f"n={len(A3)}")

        # P4 删记录不删磁盘文件
        victims = list(app.store.order[:10])
        for r in victims:
            f = tmpdir / "downloads" / f"{r}.mp4"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"FAKEVIDEO" * 100)
            app.store.items[r].file = str(f)
            app.store.items[r].status = "done"
        kept = [r for r in app.store.order if r not in victims]
        recs = [app.store.items[r] for r in victims]
        for r in victims:
            app.store.remove(r)
        app._drop_records_ui(recs)
        pump(app, 200)
        A4 = {r: app.store.items[r].title for r in app.store.order}
        check("P4 删 10 条后列表 = 490", len(A4) == 490, f"n={len(A4)}")
        check("P4b 其余 490 条一条不少",
              all(r in A4 for r in kept),
              f"缺失={[r for r in kept if r not in A4][:5]}")
        disk_gone = [r for r in victims
                     if not (tmpdir / "downloads" / f"{r}.mp4").exists()]
        check("P4c ★磁盘文件未删（只删记录）", not disk_gone,
              f"被误删={disk_gone[:5]}")

        # P5 文案
        src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
        check("P5 满档提示含「不会顶掉已有记录」", "不会顶掉已有记录" in src)
        check("P5b 满档提示含「或删除部分记录后继续拉取」",
              "或删除部分记录后继续拉取" in src)
        check("P5c 界面提示含「无需重启」", "无需重启" in src)
    finally:
        try:
            app.root.destroy()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    ok = sum(1 for _, p, _ in RESULTS if p)
    print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} "
          f"({ok}/{len(RESULTS)})")
    fails = [n for n, p, _ in RESULTS if not p]
    for f in fails:
        print("  未通过: " + f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
