"""v3.4.5 回归：nonce 不是对象身份 + 同名不同条靠「发布」列自证。

背景（2026-11-13 客户实拍，v3.4.4 跑完仍是 3 行同名）：
  实测那 3 行**确实是 3 条不同视频**（同一个博主「屁皮题PPT」用同一套文案
  连发），证据：条目层 id / md5sum / file_size / createtime 全不同。
  但 v3.4.4 有个隐患：`_OBJECT_ID_KEYS` 里放了 `object_nonce_id`，
  详情页那条被抓成了 `17525191845727011266_0_140_0_0`（带场景后缀的 nonce），
  而同一视频在推荐流里的 `id` 是 15052933753685936530 —— 换个入口就会
  被认成两条（md5 一样时靠 md5 兜住了，换清晰度就兜不住）。

本测试锁四件事：
  A 结构 —— parser 有 _extract_publish_ts；nonce 不再进对象 ID 候选
  B 单元 —— id 优先于 object_nonce_id；只有 nonce 时 object_id 为空
  C 报文 —— 双层编码的真实形状 bridge 报文能同时拿到 id / md5 / 发布时间
  D 界面 —— Record.publish_ts 持久化 + 「发布」列与 values 对齐 + 格式化
"""
import json
import sys
import tempfile
import time
from pathlib import Path

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
    p = src("sphdl/parser.py")
    check("A1 parser 有 _extract_publish_ts", "def _extract_publish_ts" in p)
    check("A2 VideoData 构造传了 publish_ts", "publish_ts=_extract_publish_ts(entry" in p)
    check("A3 nonce 不在对象 ID 候选键里",
          "object_nonce_id" not in p.split("_OBJECT_ID_KEYS")[1].split(")")[0],
          p.split("_OBJECT_ID_KEYS")[1].split(")")[0][:70])
    check("A4 exportId 也不当对象 ID（它是场景签名口径）",
          "exportId" not in p.split("_OBJECT_ID_KEYS")[1].split(")")[0])
    e = src("sphdl/entities.py")
    check("A5 VideoData 有 publish_ts 字段", "publish_ts: int = 0" in e)
    g = src("sphdl/gui.py")
    check("A6 Record 有 publish_ts 字段", "publish_ts: int = 0" in g)
    check("A7 _load 白名单读回 publish_ts",
          'kw["publish_ts"] = int(d.get("publish_ts")' in g)
    check("A8 列表新增「发布」列", '"pub": ("发布", 86)' in g)
    check("A9 _sort_key 支持 pub 排序", 'if col == "pub":' in g)


# ===================== B 单元 =====================
def test_unit():
    print("\n--- B 单元 ---")
    from sphdl.parser import _extract_object_id, _extract_publish_ts

    entry = {
        "id": "15052933753685936530",
        "object_nonce_id": "17525191845727011266_0_140_0_0",
        "createtime": 1794449538,
    }
    oid = _extract_object_id(entry)
    check("B1 id 优先于 object_nonce_id", oid == "15052933753685936530", oid)

    only_nonce = {"object_nonce_id": "17525191845727011266_0_140_0_0"}
    oid2 = _extract_object_id(only_nonce)
    check("B2 只有 nonce 时宁可为空也不拿它当身份", oid2 == "", repr(oid2))

    check("B3 短 id（非 feed 形态）不认", _extract_object_id({"id": "123"}) == "",
          repr(_extract_object_id({"id": "123"})))
    check("B4 objectId 仍可作为兜底",
          _extract_object_id({"objectId": "15053775234619869614"}) == "15053775234619869614")

    check("B5 createtime 正常解析", _extract_publish_ts(entry) == 1794449538)
    check("B6 毫秒时间戳降级成秒",
          _extract_publish_ts({"createtime": 1794449538000}) == 1794449538)
    check("B7 拿不到时返回 0", _extract_publish_ts({}) == 0)
    check("B8 非法值不炸", _extract_publish_ts({"createtime": "abc"}) == 0)


# ===================== C 真实报文形状 =====================
def _bridge_payload():
    """按真机形状造：result → jsapi_resp.resp_json(字符串) → object 列表。"""
    obj = {
        "object": [
            {"id": "15052933753685936530",
             "object_nonce_id": "17525191845727011266_0_140_0_0",
             "createtime": 1794449538,
             "fav_count": 4, "like_count": 1,
             "object_desc": {
                 "description": "6步年终晋升汇报，领导眼前一亮说值得提拔！",
                 "media": [{"url": "https://x/stodownload?encfilekey=Z1",
                            "md5sum": "5d704157e8a9d8a3447050d2165fcc55",
                            "file_size": 6689211, "width": 1080, "height": 1440}]}},
            {"id": "15053278862614989237",
             "object_nonce_id": "17525191845727011266_0_141_0_0",
             "createtime": 1794490678,
             "fav_count": 26, "like_count": 9,
             "object_desc": {
                 "description": "6步年终晋升汇报，领导眼前一亮说值得提拔！",
                 "media": [{"url": "https://x/stodownload?encfilekey=Z2",
                            "md5sum": "355465de8060d255e3f3aa9e614f3898",
                            "file_size": 6876369, "width": 1080, "height": 1440}]}},
        ]
    }
    resp_json = json.dumps(obj, ensure_ascii=False)
    jsapi = json.dumps({"resp_json": resp_json}, ensure_ascii=False)
    return {"url": "https://channels.weixin.qq.com/web/pages/feed?eid=x",
            "cmdName": "finderH5ExtTransfer",
            "arguments": "{}",
            "result": json.dumps({"jsapi_resp": json.loads(jsapi)},
                                 ensure_ascii=False)}


def test_payload():
    print("\n--- C 真实报文形状 ---")
    from sphdl.parser import (extract_from_object_desc,
                              iter_object_desc_entries)

    vds = []
    for od, entry in iter_object_desc_entries(_bridge_payload()):
        vd = extract_from_object_desc(od, entry=entry)
        if vd:
            vds.append(vd)
    check("C1 双层编码报文里解析出 2 条", len(vds) == 2, str(len(vds)))
    if len(vds) != 2:
        return
    ids = [vd.object_id for vd in vds]
    check("C2 两条拿到各自稳定 id（不是 nonce）",
          ids == ["15052933753685936530", "15053278862614989237"], str(ids))
    check("C3 两条 id 不同（同名不同条不误并）", ids[0] != ids[1])
    ts = [vd.publish_ts for vd in vds]
    check("C4 两条发布时间各自解析", ts == [1794449538, 1794490678], str(ts))
    check("C5 发布时间不同 → 可区分同名行", ts[0] != ts[1])
    check("C6 md5 仍是各自文件的哈希（与 id 正交）",
          [vd.video_id[:8] for vd in vds] == ["5d704157", "355465de"],
          str([vd.video_id[:8] for vd in vds]))


# ===================== D 界面/持久化 =====================
def test_gui():
    print("\n--- D 界面/持久化 ---")
    import sphdl.gui as G

    tdir = Path(tempfile.mkdtemp(prefix="v345pub_"))
    G.CONFIG_FILE = tdir / "config.json"
    G.RECORDS_FILE = tdir / "records.json"
    G.CONFIG_FILE.write_text("{}", encoding="utf-8")
    G.RECORDS_FILE.write_text("[]", encoding="utf-8")

    app = G.App()
    app.root.withdraw()
    try:
        app.root.update()
    except Exception:
        pass

    rec = G.Record(id="p1", title="同名", url="u1", decode_key="", size=100,
                   source="bridge钩子", created="11-13 14:20:24",
                   status="waiting", publish_ts=1794449538)
    rec2 = G.Record(id="p2", title="同名", url="u2", decode_key="", size=100,
                    source="bridge钩子", created="11-13 14:20:26",
                    status="waiting", publish_ts=1794490678)

    cols = tuple(app.tree["columns"])
    vals = app._row_values(rec)
    check("D1 values 与列数一致", len(vals) == len(cols),
          f"{len(vals)} vs {len(cols)}")
    pi = app._col_index("pub")
    check("D2 找到「发布」列", pi > 0, str(pi))
    check("D3 发布列显示 MM-DD HH:MM",
          len(str(vals[pi])) == 11 and str(vals[pi])[2] == "-", repr(vals[pi]))
    check("D4 两条同名但发布时刻不同",
          app._row_values(rec)[pi] != app._row_values(rec2)[pi],
          f"{app._row_values(rec)[pi]} / {app._row_values(rec2)[pi]}")
    check("D5 没有发布时间显示「—」",
          app._pub_cell(0) == "—" and app._pub_cell(None) == "—")

    # 持久化：写盘 → 重新加载 → publish_ts 还在
    app.store.add(rec)
    app.store.add(rec2)
    app.store.save()
    raw = json.loads(G.RECORDS_FILE.read_text(encoding="utf-8"))
    check("D6 records.json 里写了 publish_ts",
          len(raw) == 2 and all("publish_ts" in d for d in raw),
          str([d.get("publish_ts") for d in raw]))

    app2 = G.App()
    app2.root.withdraw()
    got = sorted(int(getattr(r, "publish_ts", 0) or 0)
                 for r in app2.store.items.values())
    check("D7 重启后发布时刻不丢（白名单没漏）",
          got == [1794449538, 1794490678], str(got))
    try:
        app2.root.destroy()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass


def main():
    test_source()
    test_unit()
    test_payload()
    test_gui()
    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
