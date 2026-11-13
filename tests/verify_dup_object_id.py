"""v3.4.4 回归：同一个视频不再抓成 3 行、点赞数按最新快照刷新。

背景（2026-11-13 客户实拍）：
  同一个标题 13:32:32~33 被抓成 3 行，点赞 4 / 20 / 7；
  三行的 video_id(md5sum) / encfilekey / decode_key / URL 全不同
  → 旧去重键（md5sum 或 (url, decode_key)）全部落空。
  md5sum 是「一路转码文件」的哈希，不是「这一条 feed 对象」的身份。

分三组：
  A 结构性 —— 唯一实现、字段持久化、两处（addon/GUI）都接了别名索引
  B 单元   —— 对象 ID 提取、不误并不同视频、统计取 max
  C 功能   —— 真实 SphAddon._handle + GUI 列表刷新 + 删除释放 + 跨会话持久化
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"   [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"   [{detail}]" if detail else ""))


def src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ===================== A 结构性 =====================
def test_source():
    print("\n--- A 结构性 ---")
    d = src("sphdl/dedup.py")
    for token in ("PREFIX_OID", "PREFIX_MD5", "PREFIX_NONCE", "PREFIX_URL",
                  "def identity_aliases", "def aliases_of",
                  "def legacy_key", "def max_stats"):
        check(f"A1 dedup.py 有 {token}", token in d)
    p = src("sphdl/parser.py")
    check("A2 parser 从 entry 提取 object_id",
          "def _extract_object_id" in p and
          "object_id=_extract_object_id(entry" in p)
    check("A3 泛化 id 只认 ≥15 位长数字（防把不同视频并成一条）",
          "len(v) >= 15" in p)
    check("A4 object_desc 为列表时剔除容器 ID",
          "safe_entry = {k: vv for k, vv in obj.items()" in p)
    e = src("sphdl/entities.py")
    check("A5 VideoData 有 object_id 字段", "object_id: str = \"\"" in e)
    g = src("sphdl/gui.py")
    check("A6 Record 有 object_id 字段", "object_id: str = \"\"" in g)
    check("A7 _load 白名单读回 object_id",
          '"object_id"' in g.split("def _load", 1)[1].split("def _migrate_stat_sem", 1)[0])
    check("A8 捕获时把 object_id 写进 Record",
          "object_id=str(getattr(vd, \"object_id\", \"\") or \"\")" in g)
    check("A9 启动时重建别名索引", "self._register_aliases(rid, r)" in g)
    check("A10 _add_row 登记别名", "self._register_aliases(r.id, r)" in g)
    check("A11 删记录释放整组别名",
          "self._release_aliases(rid)" in g and "all_ids.extend" in g)
    check("A12 _poll 有 duplicate 事件分支",
          'elif kind == "duplicate":' in g)
    check("A13 捕获分支按别名判重（而非只看 md5/URL）",
          "if self._alias_owner(aliases_of(vd)) is not None:" in g)
    check("A14 同名不同条只提示、绝不按标题合并",
          "def _note_same_title" in g and "同名不同条" in g and
          "_note_same_title(rec)" in g)
    a = src("sphdl/addon.py")
    check("A15 addon 共用 dedup.py（没有第二套判断）",
          "from .dedup import" in a and "aliases_of(vd)" in a)
    check("A16 addon 判重后仍通知 GUI 刷新统计",
          "self.on_duplicate(vd, owner)" in a)
    check("A17 addon 释放/清空别名索引",
          "self._alias_owner.clear()" in a and "_release_owner_locked" in a)
    check("A18 候选缓存/锚点也按别名判重",
          a.count("is_known_in(self, vd)") >= 3 and
          "def is_known_in" in d,
          f"is_known_in 调用 {a.count('is_known_in(self, vd)')} 处")
    check("A19 日志带对象 ID（下一次实拍可直接判读）",
          "oid={short_id(getattr(vd, 'object_id', ''))}" in a)


# ===================== B 单元 =====================
def test_unit():
    print("\n--- B 单元 ---")
    from sphdl.parser import extract_from_object_desc, iter_object_desc_entries
    from sphdl.entities import VideoData
    from sphdl.dedup import aliases_of, max_stats, short_id

    def mkdesc(md5, size=100):
        return {"media": [{"url": "https://x/stodownload?encfilekey=Z" + md5,
                           "md5sum": md5, "file_size": size}]}

    d1 = mkdesc("md5aaaa")
    entry = {"id": "15042040784711457411", "object_desc": d1,
             "fav_count": 20, "like_count": 8, "forward_count": 81}
    vd = extract_from_object_desc(d1, entry=entry)
    check("B1 长数字 feed id 被识别为 object_id",
          vd.object_id == "15042040784711457411", vd.object_id)
    check("B2 统计仍按大拇指/心形口径", vd.like_count == 20
          and vd.recommend_count == 8)

    short = extract_from_object_desc(mkdesc("m2"),
                                    entry={"id": "1", "object_desc": mkdesc("m2")})
    check("B3 短数字 id 不被当成对象 ID（防误合并）", short.object_id == "")

    explicit = extract_from_object_desc(
        mkdesc("m3"), entry={"objectId": "OBJ-XYZ-9", "object_desc": mkdesc("m3")})
    check("B4 显式 objectId 可用", explicit.object_id == "OBJ-XYZ-9")

    # 容器带多个 objectDesc：容器 id 不能变成每条视频的 object_id
    payload = {"id": "15042040784711457411",
               "object_desc": [mkdesc("m4"), mkdesc("m5")]}
    pairs = list(iter_object_desc_entries(payload))
    check("B5 一容器多 objectDesc 时 yielded 2 条", len(pairs) == 2)
    ids = [extract_from_object_desc(d, entry=e).object_id for d, e in pairs]
    check("B6 容器 ID 没被误套到每条视频上", ids == ["", ""], str(ids))

    def mkvd(oid, md5, like=0, url=None):
        return VideoData(url=url or f"https://x/stodownload?encfilekey=K{md5}",
                         decode_key="k", description="6步年终晋升汇报",
                         video_id=md5, object_id=oid, like_count=like)

    v1 = mkvd("OID-SAME", "md5-A", 4)
    v2 = mkvd("OID-SAME", "md5-B", 20)
    v3 = mkvd("OID-OTHER", "md5-C", 7)
    check("B7 同一对象不同 md5 → oid 别名相同（应合并）",
          bool(set(aliases_of(v1)) & set(aliases_of(v2))))
    check("B8 不同对象同名 → 无共同别名（不误合并）",
          not (set(aliases_of(v1)) & set(aliases_of(v3))))
    check("B9 统计取 max", max_stats(v1, v2)["like_count"] == 20
          and max_stats(v2, v1)["like_count"] == 20)
    check("B10 日志短标识不泄露完整 ID", len(short_id("15042040784711457411")) < 15)


# ===================== C 功能 =====================
class FakeGUI:
    """duck-typed App，用来跑真实的 _refresh_duplicate / _note_same_title。"""

    def __init__(self, records):
        self.store = SimpleNamespace(items={r.id: r for r in records},
                                     order=[r.id for r in records])
        self._alias_to_rid = {}
        for r in records:
            self._register_aliases(r.id, r)
        self._known_logged = set()
        self._same_title_logged = set()
        self._dirty = False
        self.updated = []
        self.logs = []

    def _register_aliases(self, rid, obj):
        from sphdl.dedup import aliases_of as _al
        for a in _al(obj):
            self._alias_to_rid[a] = rid

    def _alias_owner(self, aliases):
        for a in aliases:
            rid = self._alias_to_rid.get(a)
            if rid and rid in self.store.items:
                return rid
        return None

    def _update_row(self, rid):
        self.updated.append(rid)

    def push_log(self, level, msg):
        self.logs.append(msg)


def test_functional():
    print("\n--- C 功能 ---")
    from sphdl.entities import VideoData
    from sphdl.pipeline import Pipeline
    from sphdl.addon import SphAddon
    from sphdl.gui import App, Record, RecordStore
    from sphdl.dedup import aliases_of

    def mkvd(oid, md5, like, title="6步年终晋升汇报"):
        return VideoData(url=f"https://x/stodownload?encfilekey=K{md5}",
                         decode_key="k", description=title, video_id=md5,
                         object_id=oid, like_count=like, forward_count=like * 2)

    with tempfile.TemporaryDirectory() as td:
        pipe = Pipeline(str(Path(td) / "dl"), on_event=lambda *a: None)
        caps, dups = [], []
        ad = SphAddon(pipe, on_capture=lambda v, s: caps.append(v) or True,
                      auto_download=False)
        ad.natural_capture = False
        ad.on_duplicate = lambda v, k: dups.append(v)
        # 客户实拍的三次：同一对象、三路转码、三个不同点赞快照
        for v in (mkvd("OID-1", "5d704157", 4),
                  mkvd("OID-1", "355465de", 20),
                  mkvd("OID-1", "c400a1fa", 7)):
            ad._handle(v, source="bridge钩子")
        check("C1 同一视频只调用 1 次 on_capture（不插 3 行）",
              len(caps) == 1, f"caps={len(caps)}")
        check("C2 重复命中通知 GUI 2 次（刷新统计）", len(dups) == 2)
        check("C3 seen 只留 1 个 canonical key", len(ad.seen) == 1,
              str(sorted(map(str, ad.seen))))
        check("C4 三路 md5 别名都登记到同一归属组",
              len(ad._owner_aliases) == 1 and
              len(next(iter(ad._owner_aliases.values()))) >= 7,
              str(next(iter(ad._owner_aliases.values())))[:120])

        # 同名但不同对象 = 3 条独立帖子，不能被合并
        caps2 = []
        ad2 = SphAddon(pipe, on_capture=lambda v, s: caps2.append(v) or True,
                       auto_download=False)
        ad2.natural_capture = False
        for v in (mkvd("OID-A", "md5-a", 4), mkvd("OID-B", "md5-b", 20),
                  mkvd("OID-C", "md5-c", 7)):
            ad2._handle(v, source="bridge钩子")
        check("C5 同名不同对象仍保留 3 条（不误合并）", len(caps2) == 3,
              f"caps={len(caps2)}")

        # 没有 object_id 时保持旧行为
        caps3 = []
        ad3 = SphAddon(pipe, on_capture=lambda v, s: caps3.append(v) or True,
                       auto_download=False)
        ad3.natural_capture = False
        ad3._handle(mkvd("", "md5-x", 1), source="test")
        ad3._handle(mkvd("", "md5-x", 9), source="test")
        ad3._handle(mkvd("", "md5-y", 3), source="test")
        check("C6 无对象 ID 时退回 md5/URL 旧口径", len(caps3) == 2,
              f"caps={len(caps3)}")

        # 删记录 → 整组别名释放 → 可重新捕获
        n = ad.release_seen(["5d704157"])
        caps_before = len(caps)
        ad._handle(mkvd("OID-1", "md5-new", 30), source="bridge钩子")
        check("C7 release_seen 释放整组别名后可再捕获",
              n == 1 and len(caps) == caps_before + 1 and
              len(ad.seen) == 1 and
              ad._alias_owner.get("oid:OID-1") == "md5-new",
              f"n={n} seen={ad.seen}")
        ad.clear_seen()
        check("C8 clear_seen 同时清空别名索引",
              not ad.seen and not ad._alias_owner and not ad._owner_aliases)

        # GUI：重复命中把统计刷新到 max，不新增行
        rec = Record(id="r1", title="6步年终晋升汇报",
                     url="https://x/stodownload?encfilekey=K5d704157",
                     decode_key="k", size=0, source="bridge钩子",
                     created="11-13 13:32:32", video_id="5d704157",
                     object_id="OID-1", like_count=4, forward_count=8)
        fake = FakeGUI([rec])
        App._refresh_duplicate(fake, mkvd("OID-1", "355465de", 20))
        App._refresh_duplicate(fake, mkvd("OID-1", "c400a1fa", 7))
        check("C9 GUI 列表点赞刷新到 20（4/20/7 取最大）",
              rec.like_count == 20 and rec.forward_count == 40,
              f"like={rec.like_count} fwd={rec.forward_count}")
        check("C10 没有新增记录、只刷新了原有行",
              len(fake.store.items) == 1 and fake._dirty and fake.updated == ["r1"])
        check("C11 新出现的 md5 别名补进索引（换清晰度仍认得出）",
              fake._alias_to_rid.get("md5:355465de") == "r1" and
              fake._alias_to_rid.get("md5:c400a1fa") == "r1",
              str({k: v for k, v in fake._alias_to_rid.items() if k.startswith("md5:")}))
        check("C12 日志明确说明「不新增行」",
              any("不新增行" in m for m in fake.logs))

        # 同名不同对象：只提示，不合并
        rec2 = Record(id="r2", title="6步年终晋升汇报",
                      url="https://x/stodownload?encfilekey=Kmd5-b",
                      decode_key="k", size=0, source="bridge钩子",
                      created="11-13 13:32:33", video_id="md5-b",
                      object_id="OID-B", like_count=20)
        fake2 = FakeGUI([rec])
        fake2.store.items[rec2.id] = rec2
        fake2.store.order.insert(0, rec2.id)
        App._note_same_title(fake2, rec2)
        check("C13 同名不同对象会提示「是两条独立视频」",
              any("两条独立视频" in m for m in fake2.logs),
              str(fake2.logs)[:160])
        check("C14 提示后不删任何记录", len(fake2.store.items) == 2)

        # 跨会话：object_id 必须写盘并读回
        p = Path(td) / "records.json"
        st = RecordStore(p, max_records=100)
        st.add(rec)
        check("C15 原子落盘成功", st.save())
        raw = json.loads(p.read_text(encoding="utf-8"))
        check("C16 records.json 里有 object_id",
              raw[0].get("object_id") == "OID-1", str(raw[0].get("object_id")))
        st2 = RecordStore(p, max_records=100)
        check("C17 重启后 object_id 仍在（白名单没漏读）",
              st2.items["r1"].object_id == "OID-1")


def main():
    test_source()
    test_unit()
    test_functional()
    print(f"\n===== v3.4.4 去重回归：PASS={PASS} FAIL={FAIL} =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
