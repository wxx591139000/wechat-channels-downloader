"""清理「串视频」的评论（72 条 cid 出现在多个视频上）。

═══ 背景（2026-10-25）═══
v2.2.16 之前，评论挂在「最后捕获的视频」，客户在多个视频间切换时
会把评论串到别的视频上。实测数据：
    403 条评论 / 283 唯一 cid / 72 条 cid 出现在 2~3 个视频里

v2.2.16 只修今后，不碰历史。本脚本清理历史脏数据。

═══ 判定规则（无法 100% 知道真相，用可解释的启发式）═══
1. 只处理「同一 cid 出现在多条记录」的情况
2. 对每个重复 cid，选一条最可能的宿主保留，其余删掉：
   · 优先看「该视频抓到的根评论数 ≤ comment_count」的（不溢出）
   · 多个候选取"缺口最大"的（最需要这些评论来补齐总数）
   · 再平手取 seq 更小的（先抓到的更可能是原主）
3. 只删 `comments`（含 replies）里的条目，**不动任何其它字段**
4. 幂等：重复跑不会继续删（第二次跑应显示 0 条可清理）

═══ 安全 ═══
· 备份两份（用户目录 backups/ + 临时目录）
· 先干跑打印计划，再执行
"""
import collections
import json
import os
import pathlib
import shutil
import time


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cid_of(c):
    return c.get("cid") or (c.get("nick", "") + "\x00" + c.get("content", ""))


def count_tree(nodes):
    n = 0
    for c in nodes or []:
        n += 1
        n += count_tree(c.get("replies"))
    return n


def main(apply_changes: bool):
    home = pathlib.Path(os.environ["LOCALAPPDATA"]) / "sphdl"
    rec_path = home / "records.json"
    if not rec_path.exists():
        print("❌ 找不到 records.json:", rec_path)
        return

    data = load(rec_path)
    print(f"records.json: {rec_path}")
    print(f"记录总数: {len(data)}")

    # ---------- 备份 ----------
    ts = time.strftime("%Y%m%d_%H%M%S")
    bk_dir = home / "backups"
    bk_dir.mkdir(parents=True, exist_ok=True)
    bk1 = bk_dir / f"records.json.bak-{ts}"
    bk2 = pathlib.Path(os.environ.get("TEMP", ".")) / f"records.json.bak-{ts}"
    shutil.copy2(rec_path, bk1)
    shutil.copy2(rec_path, bk2)
    print(f"✅ 备份1: {bk1}")
    print(f"✅ 备份2: {bk2}")
    print()

    # ---------- 收集 cid → 出现过的记录 ----------
    owner = collections.defaultdict(list)      # cid -> [(record_index, rec)]
    for i, r in enumerate(data):
        for c in r.get("comments") or []:
            owner[cid_of(c)].append((i, r))

    dups = {k: v for k, v in owner.items() if len({i for i, _ in v}) > 1}
    print(f"重复 cid（出现在多条记录）: {len(dups)}")
    if not dups:
        print("✅ 没有需要清理的，退出")
        return

    # ---------- 每个视频的"缺口" ----------
    def gap(rec):
        """抓到数比 comment_count 多 → 负缺口(溢出)；少 → 正缺口。"""
        got = count_tree(rec.get("comments"))
        total = int(rec.get("comment_count") or 0)
        if not total:
            return 0
        return total - got          # >0 还缺；<0 溢出了

    # ---------- 决定保留给谁 ----------
    keep_for = {}                 # cid -> record_index（保留）
    for cid, occ in dups.items():
        idxs = sorted({i for i, _ in occ})
        cands = []
        for i in idxs:
            g = gap(data[i])
            cands.append((0 if g >= 0 else 1,   # 先要"不溢出"的
                          -g,                   # 缺口大的优先
                          int(data[i].get("seq") or 0),   # 先抓到的优先
                          i))
        cands.sort()
        keep_for[cid] = cands[0][3]

    # ---------- 执行清理 ----------
    removed_total = 0
    per_rec = []
    for i, r in enumerate(data):
        before = count_tree(r.get("comments"))
        if before == 0:
            continue
        kept = []
        removed_here = 0
        for c in r.get("comments") or []:
            cid = cid_of(c)
            if cid in keep_for and keep_for[cid] != i:
                removed_here += 1
                continue
            kept.append(c)
        if removed_here:
            r["comments"] = kept
            removed_total += removed_here
            after = count_tree(kept)
            per_rec.append((r.get("title", "")[:24], before, after,
                            removed_here, r.get("comment_count")))

    print()
    print(f"{'视频':26s} {'清理前':>6s} {'清理后':>6s} {'删除':>5s} {'总数':>6s}")
    print("-" * 62)
    for t, b, a, d, cnt in per_rec:
        print(f"{t:26s} {b:6d} {a:6d} {d:5d} {str(cnt):>6s}")
    print("-" * 62)
    print(f"共删除 {removed_total} 条重复评论")

    # ---------- 校验 ----------
    check = collections.defaultdict(set)
    for i, r in enumerate(data):
        for c in r.get("comments") or []:
            check[cid_of(c)].add(i)
    still = {k: v for k, v in check.items() if len(v) > 1}
    print()
    print(f"校验：仍有重复的 cid = {len(still)}（应为 0）")
    if still:
        print("  ⚠️ 仍有重复:", list(still.items())[:3])

    if not apply_changes:
        print()
        print("（干跑模式，未写回文件。加 --apply 才真正保存）")
        return

    # ---------- 写回（原子写） ----------
    tmp = rec_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    # 保留原格式风格：先看原文件是不是紧凑格式
    raw_old = bk1.read_text(encoding="utf-8")
    compact = "\n" not in raw_old.strip()
    if compact:
        tmp.write_text(json.dumps(data, ensure_ascii=False),
                       encoding="utf-8")
    os.replace(tmp, rec_path)
    print(f"✅ 已写回: {rec_path}")
    print(f"   文件大小: {rec_path.stat().st_size} 字节")


if __name__ == "__main__":
    import sys
    main("--apply" in sys.argv)
