"""验证 v3.1.6：点赞/推荐字段口径修正。

背景（客户 2026-11-03 用截图指出）：
    同一条视频，视频号页面上「大拇指 1284 / 心形 512」，
    下载器「👍 点赞」列却显示 512 —— 说明把心形当成了点赞。

真机报文实证（reference/channels-decrypt/wx_response.json）：
    entry.fav_count = 4703  ==  monotonic_data.count_info.thumb_up_count = 4703
    entry.like_count = 2570
    → 微信的 fav_count 才是大拇指点赞，like_count 是心形推荐。名字是反的。

覆盖：
    A. 真实报文解析：点赞=4703、推荐=2570、转发=2602、评论=268
    B. 统计数被包一层对象时也能取到（{"thumb_up_count": {"thumb_up_count": N}}）
    C. 旧记录迁移：like=512/fav=1284 → like=1284/推荐=512
    D. 迁移幂等：存盘再读一遍，数字不动
    E. 界面「👍 点赞」列显示的是大拇指数
    F. 老记录没有 fav_count → 点赞显示「—」，不拿心形冒充

用法：../.venv/Scripts/python.exe tests/verify_v316_like_semantics.py
    退出码 0 = 全绿；1 = 有失败项。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="sphdl_v316_"))
os.environ["SPHDL_DATA_DIR"] = str(_TMP)

# 注入授权密钥 + 造一个「视频号 + 点赞增强包」的码：
#   要验的是"授权已买时，历史记录的点赞列该显示数字"，
#   没授权时列一律「—」是设计如此，验不出问题。
_LIC_CODE = None
try:
    _sec = ROOT / ".license_secret"
    if _sec.exists():
        from sphdl import license as _lic
        _raw = bytes.fromhex(_sec.read_text().strip())
        (ROOT / "sphdl" / "_lic_key.py").write_text(
            "K = %r\n" % _lic.obfuscate(_raw), encoding="utf-8")
        _LIC_CODE = _lic.make_code(tier=2000, days=0,
                                   product=_lic.PRODUCT_VIDEO,
                                   caps=_lic.CAP_LIKE_STATS, max_devices=1)
except Exception as _e:                        # noqa: BLE001
    print(f"⚠ 生成测试授权码失败（点赞列会显示「—」，相关断言跳过）：{_e}")

_cfg = {"auto_download": False}
if _LIC_CODE:
    _cfg["license_codes"] = {"1": _LIC_CODE}
    _cfg["license_code"] = _LIC_CODE
(_TMP / "config.json").write_text(json.dumps(_cfg), encoding="utf-8")

FAILS = []


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def test_real_payload():
    """A/B：拿项目里那份真实报文，跑一遍解析。"""
    print("\n[A/B] 真实报文解析（reference/channels-decrypt/wx_response.json）")
    from sphdl.parser import (extract_stats, iter_object_desc_entries,
                              last_stat_keys)

    p = ROOT.parent / "reference" / "channels-decrypt" / "wx_response.json"
    if not p.exists():
        print("  ⚠ 报文样本不存在，跳过（这条不是产品缺陷）")
        return
    data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    ents = list(iter_object_desc_entries(data))
    check("报文里找到 1 个作品条目", len(ents) == 1, f"{len(ents)} 个")
    if not ents:
        return
    od, entry = ents[0]
    st = extract_stats(od, entry)
    keys = last_stat_keys()

    check("点赞 = 大拇指（fav_count=4703）", st["like_count"] == 4703,
          f"like_count={st['like_count']} 命中={keys.get('like_count')}")
    check("推荐 = 心形（like_count=2570）", st["recommend_count"] == 2570,
          f"recommend_count={st['recommend_count']} 命中={keys.get('recommend_count')}")
    check("转发 = 2602", st["forward_count"] == 2602, str(st["forward_count"]))
    check("评论 = 268", st["comment_count"] == 268, str(st["comment_count"]))
    check("不再把心形塞进收藏", st["fav_count"] == 0, str(st["fav_count"]))
    check("两个口径数值不相等（不是同一个数重复填）",
          st["like_count"] != st["recommend_count"],
          f"{st['like_count']} vs {st['recommend_count']}")

    # B. 嵌套对象：报文里 thumb_up_count 是 {"thumb_up_count": 4703}
    nested = {"object_desc": {}, "id": "x",
              "monotonic_data": {"count_info": {"thumb_up_count":
                                                {"thumb_up_count": 8888}}}}
    st2 = extract_stats({}, nested)
    check("统计值包了一层对象也能取到", st2["like_count"] == 8888,
          f"like_count={st2['like_count']}")

    # 反证：只有心形字段时，点赞必须是 0，不能拿心形顶上
    only_like = {"object_desc": {}, "id": "y", "like_count": 512}
    st3 = extract_stats({}, only_like)
    check("只有心形数据 → 点赞为 0（不冒充）", st3["like_count"] == 0,
          f"like_count={st3['like_count']}")
    check("只有心形数据 → 归到推荐", st3["recommend_count"] == 512,
          str(st3["recommend_count"]))


def test_legacy_migration():
    """C/D/E/F：老记录搬迁 + 界面显示 + 幂等。"""
    print("\n[C/D/E/F] 旧记录口径搬迁（模拟客户升级）")
    import sphdl.gui as G

    store_path = _TMP / "records_mig.json"
    legacy = [
        {  # 客户截图里那种：旧 like 存的是心形 512，旧 fav 存的是大拇指 1284
            "id": "r1", "title": "明朝最诡异的大爆炸", "url": "u1",
            "decode_key": "k1", "size": 1000, "source": "bridge钩子",
            "created": "2026-11-03 10:00:34", "status": "waiting",
            "like_count": 512, "fav_count": 1284, "recommend_count": 0,
        },
        {  # 极老记录：没有 fav_count，只有被误当点赞的心形
            "id": "r2", "title": "很老的记录", "url": "u2",
            "decode_key": "k2", "size": 900, "source": "bridge钩子",
            "created": "2026-11-02 08:00:00", "status": "waiting",
            "like_count": 77, "fav_count": 0,
        },
        {  # 全新口径记录：不该被再搬一次
            "id": "r3", "title": "已是新口径", "url": "u3",
            "decode_key": "k3", "size": 800, "source": "bridge钩子",
            "created": "2026-11-03 11:00:00", "status": "waiting",
            "like_count": 999, "recommend_count": 111, "stat_sem": 1,
        },
    ]
    store_path.write_text(json.dumps(legacy, ensure_ascii=False),
                          encoding="utf-8")
    store = G.RecordStore(store_path, max_records=0)

    r1 = store.items["r1"]
    check("旧记录点赞搬正：512 → 1284", r1.like_count == 1284, str(r1.like_count))
    check("旧记录推荐补上：→ 512", r1.recommend_count == 512, str(r1.recommend_count))
    check("旧记录 fav_count 清空", r1.fav_count == 0, str(r1.fav_count))
    check("搬迁计数 = 2（r1 + r2）", store.migrated_stats == 2,
          str(store.migrated_stats))

    r2 = store.items["r2"]
    check("无大拇指数的老记录 → 点赞 0（不当假数据）", r2.like_count == 0,
          str(r2.like_count))
    check("它的心形数归到推荐", r2.recommend_count == 77, str(r2.recommend_count))

    r3 = store.items["r3"]
    check("新口径记录原样不动", r3.like_count == 999 and r3.recommend_count == 111,
          f"{r3.like_count}/{r3.recommend_count}")

    # D. 幂等：存盘再读，不能又交换回去
    check("保存成功", store.save() is True)
    store2 = G.RecordStore(store_path, max_records=0)
    r1b = store2.items["r1"]
    check("重读后点赞仍是 1284（幂等）", r1b.like_count == 1284, str(r1b.like_count))
    check("重读后推荐仍是 512", r1b.recommend_count == 512, str(r1b.recommend_count))
    check("重读时不再触发搬迁", store2.migrated_stats == 0,
          str(store2.migrated_stats))

    # E. 界面显示：授权开启时，点赞列应是大拇指数
    print("\n[E] 界面「👍 点赞」列显示值")
    G.RECORDS_FILE = store_path
    try:
        app = G.App()
    except Exception as e:                       # noqa: BLE001
        check("启动真实 App", False, f"{type(e).__name__}: {e}")
        return
    try:
        # ★ P56：建列表时曾把 _like_stats_ok 覆盖成 False，
        #   导致"重启后历史行点赞列全「—」"，新捕获行却正常。
        if _LIC_CODE:
            check("启动即识别「点赞增强包」授权", app._like_stats_ok is True,
                  f"_like_stats_ok={app._like_stats_ok}")
            check("与 _has_like_stats() 一致（不再被建列表代码覆盖）",
                  bool(app._like_stats_ok) == bool(app._has_like_stats()),
                  f"{app._like_stats_ok} vs {app._has_like_stats()}")
        iid = app._row_of.get("r1")          # rid -> iid
        check("列表里能找到这条记录", iid is not None, str(list(app._row_of)[:3]))
        if iid:
            vals = app.tree.item(iid, "values")
            like_cell = str(vals[6]) if len(vals) > 6 else ""
            check("点赞列显示 1284（不是 512）", like_cell == "1284", like_cell)
            check("列表里不出现 512（心形数不再冒充点赞）",
                  "512" not in like_cell, like_cell)
    finally:
        try:
            app.store.save()
        except Exception:
            pass
        try:
            app.root.destroy()
        except Exception:
            pass


def main():
    print("=" * 66)
    print("v3.1.6 点赞/推荐口径验证")
    print("=" * 66)
    try:
        test_real_payload()
        test_legacy_migration()
    except Exception as e:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("测试自身无异常", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print("   -", f)
        sys.exit(1)
    print("✅ 全部通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
