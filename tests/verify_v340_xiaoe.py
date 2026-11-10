"""验证 v3.4.0：小鹅通课程下载 + 独立授权（客户端 + 发码平台 + 解析链路）。

为什么跨模块验：
    `server/sphdl_admin.py`（发码，单文件标准库部署）和 `sphdl/license.py`
    （客户端校验）各自独立实现一份码算法。只要有一处不一致，后台发出来的码
    客户就激活不了，而这种错**单侧测试永远发现不了** —— 所以一律用
    「后台生成 → 客户端校验」的真实方向验。

覆盖：
  A. 授权核心：小鹅通码往返、五条产品线互斥、台数/绑定、老码不受影响
  B. 发码平台：生成小鹅通码能被客户端激活、复制文案、不分档、下拉选项
  C. 小鹅通模块：链接解析、去混淆、m3u8 解析、文件名稳定性、格式化

用法：python tests/verify_v340_xiaoe.py
     退出码 0 = 全绿；1 = 有失败项。
"""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="sphdl_v340_"))
os.environ["SPHDL_DATA_DIR"] = str(_TMP)
# ★ 必须先落 config.json 再 import gui（CONFIG_FILE 是模块级常量，
#   import 那一刻就定死 —— v3.3.0 踩过，P60）
(_TMP / "config.json").write_text(json.dumps({"auto_download": False}),
                                  encoding="utf-8")

FAILS = []


def check(name, ok, detail=""):
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


# ── 注入密钥（客户端侧）──
_SEC_PATH = ROOT / ".license_secret"
_SEC_HEX = _SEC_PATH.read_text().strip() if _SEC_PATH.exists() else ""
if not _SEC_HEX:
    print("[FAIL] 缺 .license_secret，无法生成授权码")
    raise SystemExit(1)

from sphdl import license as lic          # noqa: E402

_RAW = bytes.fromhex(_SEC_HEX)
(ROOT / "sphdl" / "_lic_key.py").write_text(
    "K = %r\n" % lic.obfuscate(_RAW), encoding="utf-8")
lic._secret_cache = None                  # 清缓存：打包会删 _lic_key.py
os.environ["SPHDL_LIC_SECRET"] = _SEC_HEX


# ══════════════════ A. 授权核心 ══════════════════
def test_license_core():
    print("\n[A] 授权核心（sphdl/license.py）")
    check("常量 PRODUCT_XIAOE = 5", lic.PRODUCT_XIAOE == 5)
    check("产品表含小鹅通",
          lic.PRODUCT_LABELS.get(lic.PRODUCT_XIAOE) == "小鹅通课程下载",
          lic.PRODUCT_LABELS.get(lic.PRODUCT_XIAOE, ""))
    check("小鹅通归入不分档", lic.PRODUCT_XIAOE in lic.PRODUCTS_NO_TIER)

    code = lic.make_code(0, product=lic.PRODUCT_XIAOE)
    info = lic.verify_code(code)
    check("小鹅通码往返", bool(info), code)
    check("…product=5", bool(info) and info["product"] == 5,
          str(info and info.get("product")))
    check("…tier=0（不分档）", bool(info) and info["tier"] == 0,
          str(info and info.get("tier")))
    check("…is_xiaoe_product 为真", lic.is_xiaoe_product(info))
    check("…显示名正确",
          lic.label_of_product(info) == "小鹅通课程下载版（不分档）",
          lic.label_of_product(info))

    # 五条产品线互斥
    others = {
        "视频号": lic.PRODUCT_VIDEO,
        "公众号": lic.PRODUCT_MP_ARTICLE,
        "抖音": lic.PRODUCT_DOUYIN,
        "B 站": lic.PRODUCT_BILIBILI,
    }
    for name, p in others.items():
        check(f"小鹅通码不被当成{name}",
              not lic.has_feature(info, p))

    vinfo = lic.verify_code(lic.make_code(1000))
    check("视频号码不含小鹅通", not lic.is_xiaoe_product(vinfo))
    binfo = lic.verify_code(lic.make_code(0, product=lic.PRODUCT_BILIBILI))
    check("B 站码不含小鹅通", not lic.is_xiaoe_product(binfo))

    # 带台数 / 绑定
    md = lic.make_code(0, product=lic.PRODUCT_XIAOE, max_devices=3)
    md_info = lic.verify_code(md)
    check("小鹅通限 3 台码可验",
          bool(md_info) and md_info.get("max_devices") == 3,
          str(md_info and md_info.get("max_devices")))
    dev = lic.make_code(0, product=lic.PRODUCT_XIAOE, device="163145d5")
    dev_info = lic.verify_code(dev)
    check("小鹅通绑定码可验",
          bool(dev_info) and dev_info.get("device_bound"), dev)

    rows = lic.selftest()
    bad = [r for r in rows if not r[1]]
    check("license.selftest 全绿", not bad,
          f"{len(rows)} 项，失败 {[b[0] for b in bad]}")

    # ── P59 ⑤ 归来：产品线枚举必须"一处改全处生效" ──
    # v3.4.0 加小鹅通时，gui 里有两处硬编码产品元组又漏了它（与 v3.3.0 加
    # B 站时同一个坑）。现在改成遍历 lic.PRODUCT_LABELS，这里把它钉死：
    #   ① 兜底取值必须能取到"只买了小鹅通"的码
    #   ② 兜底/其它产品判定不得再出现手写元组（否则下次还会漏）
    import re as _re

    from sphdl import gui as G

    class _CfgStub:
        def __init__(self, d):
            self.d = d

        def code_of(self, p):
            return self.d.get(int(p), "")

    class _AppStub:
        def __init__(self, d):
            self.cfg = _CfgStub(d)

        def _check_bound(self, info):
            return info

        _license_info = G.App._license_info
        _has_other_product = G.App._has_other_product

    only_x = _AppStub({lic.PRODUCT_XIAOE: lic.make_code(
        0, product=lic.PRODUCT_XIAOE)})
    got = _AppStub._license_info(only_x, None)
    check("只买小鹅通码 → 通用展示能取到授权",
          bool(got) and int(got.get("product") or 0) == lic.PRODUCT_XIAOE,
          str(got and got.get("product")))
    check("只买小鹅通码 → 被判为「有其它产品」",
          _AppStub._has_other_product(only_x) is True)
    only_v = _AppStub({lic.PRODUCT_VIDEO: lic.make_code(1000)})
    check("只买视频号码 → 不算「有其它产品」",
          _AppStub._has_other_product(only_v) is False)

    gsrc = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
    hard = _re.findall(r"for p in \(lic\.PRODUCT_", gsrc)
    check("gui 里不再有手写产品元组（单点事实源）", not hard,
          f"命中 {len(hard)} 处")
    check("兜底遍历 lic.PRODUCT_LABELS",
          "for p in sorted(lic.PRODUCT_LABELS)" in gsrc)
    check("FLAG_ALL 由产品表推导（不是写死 15）",
          lic.FLAG_ALL == sum(1 << (int(p) - 1) for p in lic.PRODUCT_LABELS),
          str(lic.FLAG_ALL))


# ══════════════════ B. 发码平台 ══════════════════
def test_admin_server():
    print("\n[B] 发码平台（server/sphdl_admin.py）")
    sys.path.insert(0, str(ROOT / "server"))
    try:
        import sphdl_admin as adm
    except Exception as e:                    # noqa: BLE001
        check("导入发码平台模块", False, f"{type(e).__name__}: {e}")
        return
    check("导入发码平台模块", True)
    check("后台常量 PRODUCT_XIAOE = 5",
          getattr(adm, "PRODUCT_XIAOE", None) == 5,
          str(getattr(adm, "PRODUCT_XIAOE", None)))
    check("后台把小鹅通归入不分档", 5 in adm.PRODUCTS_NO_TIER)
    check("后台产品表含小鹅通", 5 in adm.PRODUCT_LABEL)

    a_code = adm.make_code(0, product=adm.PRODUCT_XIAOE)
    a_info = lic.verify_code(a_code)
    check("后台发的小鹅通码 → 客户端校验通过", bool(a_info), a_code)
    check("…客户端识别为小鹅通",
          bool(a_info) and a_info["product"] == lic.PRODUCT_XIAOE,
          str(a_info and a_info["product"]))
    check("…客户端识别为不分档（tier=0）",
          bool(a_info) and a_info["tier"] == 0,
          str(a_info and a_info.get("tier")))

    a_md = adm.make_code(0, product=adm.PRODUCT_XIAOE, max_devices=2)
    a_md_info = lic.verify_code(a_md)
    check("后台发的小鹅通限台数码可验",
          bool(a_md_info) and a_md_info.get("max_devices") == 2,
          str(a_md_info and a_md_info.get("max_devices")))

    check("复制文案 = 「小鹅通课程下载授权码：」",
          adm.code_label(0, adm.PRODUCT_XIAOE) == "小鹅通课程下载授权码：",
          adm.code_label(0, adm.PRODUCT_XIAOE))
    check("台账展示名含「小鹅通」",
          "小鹅通" in adm.NO_TIER_LABEL.get(adm.PRODUCT_XIAOE, ""),
          adm.NO_TIER_LABEL.get(adm.PRODUCT_XIAOE, ""))

    # 发码页下拉里必须有 5
    try:
        html = adm.PAGE_NEW if hasattr(adm, "PAGE_NEW") else ""
    except Exception:                         # noqa: BLE001
        html = ""
    if not html:
        src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
        check("发码页下拉含 value=\"5\"", '<option value="5">' in src,
              "源码里搜索")
        check("下拉文案含「小鹅通课程下载（不分档）」",
              "小鹅通课程下载（不分档）" in src)

    # 反向：老产品不受影响
    v_code = adm.make_code(2000, product=adm.PRODUCT_VIDEO, max_devices=1)
    v_info = lic.verify_code(v_code)
    check("后台发的视频号码 → 客户端仍是视频号 2000",
          bool(v_info) and v_info["product"] == 1 and v_info["tier"] == 2000,
          f"{v_info and v_info['product']}/{v_info and v_info['tier']}")
    check("…且不含小鹅通", not lic.is_xiaoe_product(v_info))

    # ── 双实现交叉：后台与客户端的产品表必须逐条对齐 ──
    # 两份代码各自独立（后台是单文件标准库部署，不能 import 客户端包），
    # 所以只能在这里做"集合相等"的硬校验。漏一条 = 后台能发、客户端不认。
    check("后台产品表 == 客户端产品表",
          set(adm.PRODUCT_LABEL) == set(lic.PRODUCT_LABELS),
          f"后台 {sorted(adm.PRODUCT_LABEL)} vs 客户端 {sorted(lic.PRODUCT_LABELS)}")
    check("后台不分档清单 == 客户端不分档清单",
          set(adm.PRODUCTS_NO_TIER) == set(lic.PRODUCTS_NO_TIER),
          f"{sorted(adm.PRODUCTS_NO_TIER)} vs {sorted(lic.PRODUCTS_NO_TIER)}")

    # ── 全产品线闭环：每一条都要「后台发 → 客户端认」 ──
    # 以后加 ClassIn 之类的产品线，只要漏改一处，这里立刻红。
    for p, name in sorted(lic.PRODUCT_LABELS.items()):
        tier = 0 if p in lic.PRODUCTS_NO_TIER else 1000
        try:
            c = adm.make_code(tier, product=p)
        except Exception as e:                # noqa: BLE001
            check(f"后台能发「{name}」码", False, f"{type(e).__name__}: {e}")
            continue
        i = lic.verify_code(c)
        check(f"后台发「{name}」→ 客户端认产品号 {p}",
              bool(i) and int(i.get("product") or 0) == int(p),
              str(i and i.get("product")))


# ══════════════════ C. 小鹅通模块 ══════════════════
def test_xiaoe_module():
    print("\n[C] 小鹅通模块（sphdl/xiaoe.py）")
    from sphdl import xiaoe

    # ① 链接解析
    cases = [
        ("https://appabc12345.h5.xiaoeknow.com/p/course/video/"
         "v_64b4b3cce4b007b201a6d98f?product_id=p_62d7b05de4b00a4f372892372",
         "appabc12345", "v_64b4b3cce4b007b201a6d98f",
         "p_62d7b05de4b00a4f372892372"),
        ("https://appkxt2fvhl1688.h5.xiaoeknow.com/v1/course/video/"
         "v_6063d18ae4b04c019596ee24?type=2",
         "appkxt2fvhl1688", "v_6063d18ae4b04c019596ee24", ""),
    ]
    for url, appid, rid, pid in cases:
        t = xiaoe.parse_target(url)
        check(f"解析 {url[-28:]}",
              t["kind"] == "video" and t["appid"] == appid
              and t["resource_id"] == rid and t["product_id"] == pid,
              f"{t['kind']}/{t['appid']}/{t['resource_id']}/{t['product_id']}")

    share = ("城市治理公开课 陆军：中国城市精细化管理系统与智慧升级(上)\n"
             "https://appkxt2fvhl1688.h5.xiaoeknow.com/p/course/video/"
             "v_64b4b3cce4b007b201a6d98f?product_id=p_62d7b05de4b00a4f372892372")
    links = xiaoe.extract_links(share)
    check("从分享文案里抠出链接", len(links) == 1, str(links)[:80])
    check("同一链接粘两次只留一条",
          len(xiaoe.extract_links(share + "\n" + share)) == 1)
    check("课程目录页识别为 unknown",
          xiaoe.parse_target(
              "https://appx.h5.xiaoeknow.com/p/course/column/p_123").get("kind")
          == "unknown")
    check("非小鹅通链接不返回",
          xiaoe.extract_links("https://www.bilibili.com/video/BV1xx") == [])

    # ② 去混淆（用编码方向构造成小鹅通的样子）
    payload = json.dumps([{"definition_name": "高清", "definition_p": "720P",
                           "url": "https://cdn/x.m3u8?sign=1&t=2&us=3"}],
                         ensure_ascii=False)
    raw_b64 = base64.b64encode(payload.encode()).decode()
    enc = (raw_b64.replace("1", "@").replace("2", "#")
           .replace("3", "$").replace("4", "%")) + "__ba"
    try:
        got = json.loads(xiaoe.deobf(enc))
        ok = got[0]["definition_p"] == "720P"
    except Exception as e:                    # noqa: BLE001
        ok = False
        got = f"{type(e).__name__}: {e}"
    check("去混淆还原（含 __ba 噪声尾巴）", ok, str(got)[:80])

    # ③ m3u8 解析
    enc_pl = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:3
#EXT-X-KEY:METHOD=AES-128,URI="https://app.xiaoe-tech.com/k/keys?ak=1",IV=0x00000000000000000000000000000000
#EXTINF:2.0,
v.f421220_0.ts
#EXTINF:2.0,
v.f421220_1.ts
#EXT-X-ENDLIST
"""
    base = "https://v-tos-k.xiaoeknow.com/a/b/drm/v.f421220.m3u8?sign=x"
    info = xiaoe.parse_m3u8(enc_pl, base)
    check("m3u8 识别为加密", info["encrypted"])
    check("m3u8 分片数 = 2", len(info["segments"]) == 2, str(info["segments"]))
    check("相对分片拼成完整 URL",
          info["segments"][0] ==
          "https://v-tos-k.xiaoeknow.com/a/b/drm/v.f421220_0.ts",
          info["segments"][0] if info["segments"] else "")
    check("key 是完整 URL",
          info["key"].startswith("https://app.xiaoe-tech.com/k/keys"), info["key"])
    check("IV 解析成 16 字节", len(info["iv"]) == 16, str(len(info["iv"])))

    plain = "#EXTM3U\n#EXTINF:2.0,\na_0.ts\n#EXT-X-ENDLIST\n"
    pinfo = xiaoe.parse_m3u8(plain, base)
    check("明文播放列表不判加密", not pinfo["encrypted"])
    check("明文也能取到分片", len(pinfo["segments"]) == 1)

    # ④ 文件名稳定 + 非法字符
    v1 = xiaoe.XiaeoVideo(appid="a", resource_id="v_1",
                          title='第 1 讲：A/B 测试?', quality="720P")
    n1 = xiaoe.target_filename(v1)
    n2 = xiaoe.target_filename(v1)
    check("文件名稳定（两次一致）", n1 == n2, n1)
    check("文件名去掉了 Windows 非法字符",
          not any(c in n1 for c in '\\/:*?"<>|'), n1)
    check("文件名带清晰度", "720P" in n1, n1)

    # ⑤ 格式化
    check("fmt_size 1.5MB", xiaoe.fmt_size(1572864) == "1.5 MB",
          xiaoe.fmt_size(1572864))
    check("fmt_dur 1730s → 28:50", xiaoe.fmt_dur(1730) == "28:50",
          xiaoe.fmt_dur(1730))

    # ⑥ 兼容性护栏：模块可导入、关键函数齐全
    for fn in ("extract_links", "parse_target", "fetch_video", "parse_m3u8",
               "download_video", "find_ffmpeg", "remux_to_mp4"):
        check(f"导出函数 {fn}", callable(getattr(xiaoe, fn, None)))


# ══════════════════ D. 断点续传（本地 HTTP，不联网）══════════════════
def test_resume_local():
    """P50/P51/P52/P28 的回归测：断点续传到底是不是真的能续。

    之前 B 站那次（P50）判据错了 —— 代码里有 Range、有 .part 就以为能续，
    实际上临时文件名带时间戳，两次调用根本不同路径。所以这里不读代码，
    **起一个本地 HTTP 服务器数请求次数**：预置 1 个分片，只应该再要 2 个。
    """
    print("\n[D] 断点续传（本地 HTTP，3 分片）")
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from sphdl.xiaoe import XiaeoVideo, target_filename

    payload = [b"AAA-seg-0" * 40, b"BBB-seg-1" * 50, b"CCC-seg-2" * 60]
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                       # noqa: N802
            hits.append(self.path)
            if self.path.startswith("/v.m3u8"):
                body = ("#EXTM3U\n#EXT-X-VERSION:3\n"
                        "#EXT-X-TARGETDURATION:10\n"
                        + "".join(f"#EXTINF:10.0,\n/{i}.ts\n" for i in range(3))
                        + "#EXT-X-ENDLIST\n").encode()
                ctype = "application/vnd.apple.mpegurl"
            else:
                try:
                    i = int(self.path.strip("/").split(".")[0])
                    body = payload[i]
                except (ValueError, IndexError):
                    self.send_error(404)
                    return
                ctype = "video/mp2t"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):              # 静音
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    out = _TMP / "dl"
    out.mkdir(parents=True, exist_ok=True)
    v = XiaeoVideo(appid="appkxt2fvhl1688", resource_id="v_resume_test",
                   product_id="p_resume_test", title="单测：断点续传",
                   duration=30, quality="720P",
                   m3u8=f"http://127.0.0.1:{port}/v.m3u8")

    # 预置第 0 片（模拟上次下到一半被杀）+ 一个半截 .tmp（模拟写到一半断电）
    dest_name = target_filename(v)
    tmp_dir = out / (Path(dest_name).stem + ".parts")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "00000.ts").write_bytes(payload[0])
    (tmp_dir / "00001.ts.tmp").write_bytes(b"half-written-garbage")

    try:
        import sphdl.xiaoe as X

        # ffmpeg 指一个不存在的路径 → 走「保留 .ts」分支，方便核对拼接结果
        res = X.download_video(v, out, sess=X.make_session(), workers=2,
                               ffmpeg="C:/__no_ffmpeg__.exe")
    finally:
        srv.shutdown()

    seg_hits = [h for h in hits if h.endswith(".ts")]
    check("续传：只补缺的分片（3 片里只要 2 片）", len(seg_hits) == 2,
          f"实际请求 {sorted(seg_hits)}")
    check("续传：不重下已有分片", "/0.ts" not in seg_hits)
    check("下载结果 ok", bool(res and res.get("ok")),
          str(res and res.get("error")))
    check("分片数记为 3", bool(res) and res.get("segments") == 3,
          str(res and res.get("segments")))
    check("半截 .tmp 已被清掉",
          not list(tmp_dir.glob("*.tmp")), str(list(tmp_dir.glob("*.tmp"))))
    check("没有残留 .ts.tmp（原子落盘）",
          not list(tmp_dir.glob("*.ts.tmp")))
    got = Path(res["path"]).read_bytes() if res and res.get("path") else b""
    check("拼接内容 = 3 片原样相加", got == b"".join(payload),
          f"{len(got)} vs {sum(len(p) for p in payload)}")

    # 第二次：成品已在 → 直接跳过，一个分片都不该再请求
    hits.clear()
    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    try:
        again = X.download_video(v, out, sess=X.make_session(), workers=2)
    finally:
        srv2.shutdown()
    check("已下过 → 跳过不重下",
          bool(again and again.get("skipped")) and not hits,
          f"skipped={again and again.get('skipped')}, 请求={hits}")


def main():
    test_license_core()
    test_admin_server()
    test_xiaoe_module()
    test_resume_local()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
