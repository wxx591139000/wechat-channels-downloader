#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：删记录后必须能重新捕获（v2.0.18 修复）。

背景（2026-10-16 用户报告的真实故障）：
  朋友机 —— 旗舰版下到 3000 条 → 删掉所有记录 → 重新打开视频号 → 清单不动。
  根因：addon.seen（捕获去重集合）是纯内存、只增不减，
        而 GUI 删记录只清了自己那侧的索引，**没通知 addon 清 seen**。
  → 删记录后重新捕获同一批视频，全部命中 seen → 被"⏭ 重复视频"跳过。

覆盖：
  R1 删部分记录 → 这批视频必须能重新捕获
  R2 删除全部记录 → 所有视频必须能重新捕获
  R3 删记录不能误伤「仍在列表里」的视频的去重（它们仍应被判重复）
  R4 图片开关关闭时不占 seen 名额（既有行为，防回归）
  R5 边界：空删除不报错
  R6 clear_seen 必须清掉「列表里已无」的残留 key

本测试只依赖真实对象（从 store 取 video_id），不假设内部编号顺序。
跑法（在 sphdl/ 下）：
    ..\\.venv\\Scripts\\python.exe tests\\verify_seen_release.py
退出码 0 = 全通过。
"""
import sys
import time
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


class VD:
    """模拟 addon 解析出的 VideoData。"""

    def __init__(self, n, is_image=False):
        self.url = f"http://x/v{n}"
        self.video_id = f"vid{n}"
        self.description = f"视频{n}"
        self.decode_key = "k"
        self.size = 100
        self.is_image = is_image
        self.renditions = []
        self.quality_note = ""


def make_app(tmpdir):
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
        "port": 8899, "download_dir": str(tmpdir / "dl"),
        "auto_proxy": False, "auto_download": False,
        "download_images": True, "natural_capture": False,
        "guide_auto_shown": True}), encoding="utf-8")
    return G.App()


def pump(app, ms=200):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.004)


def capture(app, n):
    """走 addon 真实捕获路径（含 seen 去重 + _handle 全逻辑）。"""
    app.addon._handle(VD(n), source="test")
    pump(app)


def capture_many(app, nums):
    """逐条捕获并给足 GUI 队列消化时间（避免异步未入列）。"""
    for n in nums:
        capture(app, n)
    pump(app, 400)


def delete_records(app, rids):
    """走真实 GUI 删除链路（_remove_row = 所有删除路径的统一出口）。"""
    for rid in list(rids):
        app._remove_row(rid)
    pump(app, 300)
    app._dirty = True


def vid_of(app, rid):
    """取记录对应的 video_id（用于按真实 ID 重捕，不猜编号）。"""
    rec = app.store.items.get(rid)
    return rec.video_id if rec else None


def main():
    tmpdir = Path(tempfile.mkdtemp(prefix="sphdl_seen_"))
    app = make_app(tmpdir)
    N = 30
    try:
        # ---------- 前置 ----------
        print("=" * 74)
        print(f"前置：捕获 {N} 条，确认 seen / 列表同步")
        print("=" * 74)
        capture_many(app, range(N))
        check(f"{N} 条全部捕获", len(app.store.order) == N,
              f"list={len(app.store.order)}")
        check(f"addon.seen = {N}", len(app.addon.seen) == N,
              f"seen={len(app.addon.seen)}")

        # ---------- R1：删部分记录 → 这批能重新捕获 ----------
        print("\n" + "=" * 74)
        print("R1 删掉 10 条 → 这 10 条必须能重新捕获（按真实 video_id）")
        print("=" * 74)
        victims = list(app.store.order[:10])
        victim_vids = {vid_of(app, r) for r in victims}
        victim_nums = [int(v.replace("vid", "")) for v in victim_vids if v]
        delete_records(app, victims)
        check("R1a 列表剩 20 条", len(app.store.order) == 20,
              f"list={len(app.store.order)}")
        before = len(app.store.order)
        capture_many(app, victim_nums)              # 重捕被删的那批
        gained = len(app.store.order) - before
        check("R1b ★ 被删的 10 条重新捕获成功", gained == 10,
              f"新增 {gained} 条（期望 10）")
        check("R1c 列表回到 30 条", len(app.store.order) == 30,
              f"list={len(app.store.order)}")

        # ---------- R3：仍在列表里的视频，重捕仍应判重复 ----------
        print("\n" + "=" * 74)
        print("R3 未被删的视频重捕 → 仍应判「重复」（去重不能被破坏）")
        print("=" * 74)
        alive_rids = list(app.store.order)
        alive_nums = [int(vid_of(app, r).replace("vid", ""))
                      for r in alive_rids if vid_of(app, r)]
        before = len(app.store.order)
        capture_many(app, alive_nums)
        check("R3 仍在列表的视频重捕不增加条目", len(app.store.order) == before,
              f"{before} → {len(app.store.order)}")
        check("R3b addon.seen 未膨胀", len(app.addon.seen) == N,
              f"seen={len(app.addon.seen)}（期望 {N}）")

        # ---------- R2：删除全部记录 → 全部能重新捕获 ----------
        print("\n" + "=" * 74)
        print("R2 删除全部记录 → 所有视频必须能重新捕获")
        print("=" * 74)
        delete_records(app, list(app.store.order))
        app.addon.clear_seen()      # 模拟「删除全部记录」按钮的 clear_seen
        check("R2a 列表清空", len(app.store.order) == 0,
              f"list={len(app.store.order)}")
        check("R2b ★ 删全部后 addon.seen 已清空", len(app.addon.seen) == 0,
              f"seen={len(app.addon.seen)}（期望 0）")
        capture_many(app, range(N))
        check("R2c ★ 30 条全部重新捕获成功", len(app.store.order) == N,
              f"list={len(app.store.order)}（期望 {N}）")

        # ---------- R6：clear_seen 必须清掉「列表里已无」的残留 key ----------
        print("\n" + "=" * 74)
        print("R6 clear_seen 清残留（列表已无的 key 也能清掉）")
        print("=" * 74)
        capture(app, 7777)
        resid = app.store.order[0]
        app.store.remove(resid)     # 故意绕过 _remove_row，制造残留
        check("R6a 已制造残留（seen 有、列表无）",
              len(app.addon.seen) == N + 1,
              f"seen={len(app.addon.seen)}（期望 {N + 1}）")
        app.addon.clear_seen()
        check("R6b ★ clear_seen 把残留也清掉了", len(app.addon.seen) == 0,
              f"seen={len(app.addon.seen)}（期望 0）")
        capture_many(app, range(N))
        check("R6c ★ 残留清理后可全量重捕", len(app.store.order) == N,
              f"list={len(app.store.order)}")

        # ---------- R4：图片开关关闭时，不占 seen 名额 ----------
        print("\n" + "=" * 74)
        print("R4 图片关闭时不占 seen（既有行为，防回归）")
        print("=" * 74)
        app.cfg.download_images = False
        app.addon.allow_images = False
        seen_before = len(app.addon.seen)
        app.addon._handle(VD(900, is_image=True), source="test")
        pump(app)
        check("R4a 图片被跳过，不占 seen 名额",
              len(app.addon.seen) == seen_before,
              f"seen {seen_before} → {len(app.addon.seen)}")
        check("R4b 图片未进列表", len(app.store.order) == N,
              f"list={len(app.store.order)}")

        # ---------- R7：release_seen 幂等 + 返回值正确 ----------
        print("\n" + "=" * 74)
        print("R7 release_seen 接口契约（幂等 / 返回值 / 空输入）")
        print("=" * 74)
        r1 = app.addon.release_seen(["vid0"])
        r2 = app.addon.release_seen(["vid0"])       # 再来一次
        check("R7a 首次释放返回 1", r1 == 1, f"return={r1}")
        check("R7b 重复释放返回 0（幂等）", r2 == 0, f"return={r2}")
        check("R7c 空输入返回 0", app.addon.release_seen([]) == 0)
        check("R7d None 输入不报错", app.addon.release_seen(None) == 0)
        check("R7e 释放后 seen 减 1", len(app.addon.seen) == N - 1,
              f"seen={len(app.addon.seen)}（期望 {N - 1}）")

        # ---------- R5：边界 ----------
        print("\n" + "=" * 74)
        print("R5 边界：空删除不报错")
        print("=" * 74)
        delete_records(app, [])
        check("R5 空删除不出异常", True)
    finally:
        try:
            app.root.destroy()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    ok = sum(1 for _, p, _ in RESULTS if p)
    print(f"RESULT: {'ALL PASS' if ok == len(RESULTS) else 'FAIL'} ({ok}/{len(RESULTS)})")
    for n, p, _ in RESULTS:
        if not p:
            print("  未通过: " + n)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
