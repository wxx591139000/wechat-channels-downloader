"""验证 v3.3.0：B 站独立授权（客户端判定 + 发码平台发码 + 界面按钮）。

为什么这个测试必须跑**跨模块**：
    发码平台 `server/sphdl_admin.py` 和客户端 `sphdl/license.py` 各自有
    一份**独立实现的码算法**（后台是单文件标准库部署，不能 import 客户端包）。
    两份实现只要有一处不一致，作者在后台发出来的码客户就激活不了 ——
    而这种错**本地单侧测试永远发现不了**。所以这里一律用
    「后台生成 → 客户端校验」的真实方向来验，不各自自证。

覆盖：
  A. 授权核心：B 站码往返、产品互斥、带台数/绑定、老码不受影响
  B. 发码平台：生成 B 站码能被客户端激活、复制文案、不分档
  C. 界面：只有视频号码时 B 站按钮锁定；只有 B 站码时解锁

用法：../.venv/Scripts/python.exe tests/verify_v330_bili_license.py
    退出码 0 = 全绿；1 = 有失败项。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="sphdl_v330_"))
os.environ["SPHDL_DATA_DIR"] = str(_TMP)
# ★ 必须**先**把 config.json 落到这个目录，再 import gui。
#   原因：`gui.CONFIG_FILE = userdata.resolve("config.json")` 是**模块级常量**，
#   在 import 那一刻就定死。如果那时目录里还没有 config.json，
#   resolve() 会走「迁移」分支并返回程序目录下的路径 ——
#   之后无论怎么改写 _TMP/config.json 都不生效，表现为
#   「明明写了授权码，App 里 code_of() 全是空」。
(_TMP / "config.json").write_text(json.dumps({"auto_download": False}),
                                  encoding="utf-8")

FAILS = []


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


# ── 注入密钥（客户端侧）──
_SEC_PATH = ROOT / ".license_secret"
_SEC_HEX = _SEC_PATH.read_text().strip() if _SEC_PATH.exists() else ""
if not _SEC_HEX:
    print("❌ 缺 .license_secret，无法生成授权码，测试无法进行")
    raise SystemExit(1)

from sphdl import license as lic          # noqa: E402

_RAW = bytes.fromhex(_SEC_HEX)
(ROOT / "sphdl" / "_lic_key.py").write_text(
    "K = %r\n" % lic.obfuscate(_RAW), encoding="utf-8")
# ★ 必须清掉密钥缓存：release.py 打完包会删掉 _lic_key.py，如果本进程在
#   写文件**之前**碰过 get_secret()，它会缓存一个空值 `b""` 并永久生效
#   （见 license.get_secret 的 `_secret_cache = b""` 分支），
#   于是后面所有 verify_code 全返回 None —— 表现为「刚刚还全绿的测试突然
#   大面积报未授权」，而这个"失败"跟产品无关，纯属测试自己踩的坑。
lic._secret_cache = None
assert lic.get_secret(), "密钥注入失败，后续断言无意义"

# 发码平台从环境变量读同一个密钥
os.environ["SPHDL_LIC_SECRET"] = _SEC_HEX


def write_config(codes: dict):
    """重写 config.json：codes = {产品号: 码}。"""
    cfg = {"auto_download": False}
    if codes:
        cfg["license_codes"] = {str(k): v for k, v in codes.items()}
        cfg["license_code"] = list(codes.values())[-1]
    (_TMP / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


# ══════════════════ A. 授权核心 ══════════════════
def test_license_core():
    print("\n[A] 授权核心（license.py）")
    check("PRODUCT_BILIBILI 存在且为 4", getattr(lic, "PRODUCT_BILIBILI", None) == 4)
    check("产品表含 B 站", 4 in lic.PRODUCT_LABELS,
          str(lic.PRODUCT_LABELS))
    check("B 站在不分档列表里", 4 in lic.PRODUCTS_NO_TIER,
          str(lic.PRODUCTS_NO_TIER))

    code = lic.make_code(0, product=lic.PRODUCT_BILIBILI)
    info = lic.verify_code(code)
    check("B 站码能生成并校验通过", bool(info), code)
    check("解析出的产品 = 4", info and info["product"] == 4,
          str(info and info["product"]))
    check("B 站码不分档（tier=0）", info and info["tier"] == 0,
          str(info and info["tier"]))
    check("is_bilibili_product 为真", lic.is_bilibili_product(info))
    check("B 站码不被当成视频号",
          not lic.has_feature(info, lic.PRODUCT_VIDEO))
    check("B 站码不被当成抖音",
          not lic.has_feature(info, lic.PRODUCT_DOUYIN))
    check("展示名是「B 站视频下载版（不分档）」",
          lic.label_of_product(info) == "B 站视频下载版（不分档）",
          lic.label_of_product(info))

    # 视频号码**不含** B 站（客户明确：单独卖）
    vcode = lic.make_code(1000)
    vinfo = lic.verify_code(vcode)
    check("视频号码不含 B 站",
          not lic.is_bilibili_product(vinfo))
    check("视频号码本身仍正常（tier=1000）",
          vinfo and vinfo["tier"] == 1000, str(vinfo and vinfo["tier"]))

    # 带台数限制（发码表单默认就是 1 台，最常用的一条路）
    c_md = lic.make_code(0, product=lic.PRODUCT_BILIBILI, max_devices=1)
    i_md = lic.verify_code(c_md)
    check("B 站码可带台数限制（ver=4）", bool(i_md) and i_md["ver"] == 4,
          f"ver={i_md and i_md['ver']}")
    check("台数限制解析为 1 台", bool(i_md) and i_md.get("max_devices") == 1,
          str(i_md and i_md.get("max_devices")))
    check("产品仍是 B 站", bool(i_md) and i_md["product"] == 4)

    # 设备绑定
    c_dev = lic.make_code(0, product=lic.PRODUCT_BILIBILI, device="163145d5")
    i_dev = lic.verify_code(c_dev)
    check("B 站码可做设备绑定（ver=3）",
          bool(i_dev) and i_dev["ver"] == 3 and i_dev.get("device_bound"),
          f"ver={i_dev and i_dev['ver']}")
    check("绑定码产品仍是 B 站", bool(i_dev) and i_dev["product"] == 4)

    # 老码（已发给客户的那些）语义不能变
    legacy = lic.make_code(500)
    li = lic.verify_code(legacy)
    check("老码（ver=1）仍是视频号",
          bool(li) and li["ver"] == 1 and li["product"] == lic.PRODUCT_VIDEO,
          f"ver={li and li['ver']} product={li and li['product']}")
    check("老码不被误判成 B 站", not lic.is_bilibili_product(li))

    # 自检全绿（selftest 里已加 B 站三项）
    fails = [n for n, ok, _ in lic.selftest() if not ok]
    check("license.selftest() 全绿", not fails, "；".join(fails))


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
    check("后台常量 PRODUCT_BILI = 4",
          getattr(adm, "PRODUCT_BILI", None) == 4,
          str(getattr(adm, "PRODUCT_BILI", None)))
    check("后台把 B 站归入不分档", 4 in adm.PRODUCTS_NO_TIER)
    check("后台产品表含 B 站", 4 in adm.PRODUCT_LABEL)

    # ★ 核心：后台发出来的码，客户端必须能验
    a_code = adm.make_code(0, product=adm.PRODUCT_BILI)
    a_info = lic.verify_code(a_code)
    check("后台发的 B 站码 → 客户端校验通过", bool(a_info), a_code)
    check("…客户端识别为 B 站产品",
          bool(a_info) and a_info["product"] == lic.PRODUCT_BILIBILI,
          str(a_info and a_info["product"]))
    check("…客户端识别为不分档（tier=0）",
          bool(a_info) and a_info["tier"] == 0,
          str(a_info and a_info["tier"]))

    # 带台数（表单默认 1 台）
    a_md = adm.make_code(0, product=adm.PRODUCT_BILI, max_devices=1)
    a_md_info = lic.verify_code(a_md)
    check("后台发的 B 站限台数码 → 客户端可验", bool(a_md_info), a_md)
    check("…台数读出来是 1 台",
          bool(a_md_info) and a_md_info.get("max_devices") == 1,
          str(a_md_info and a_md_info.get("max_devices")))
    check("…ver=4", bool(a_md_info) and a_md_info["ver"] == 4)

    # 设备绑定
    a_dev = adm.make_code(0, product=adm.PRODUCT_BILI, device="163145d5")
    a_dev_info = lic.verify_code(a_dev)
    check("后台发的 B 站绑定码 → 客户端可验",
          bool(a_dev_info) and a_dev_info.get("device_bound"),
          a_dev)

    # 复制文案（客户粘贴时要一眼看出买的是什么）
    check("复制文案 = 「B 站视频下载授权码：」",
          adm.code_label(0, adm.PRODUCT_BILI) == "B 站视频下载授权码：",
          adm.code_label(0, adm.PRODUCT_BILI))
    check("台账展示名含「B 站」",
          "B 站" in adm.NO_TIER_LABEL.get(adm.PRODUCT_BILI, ""),
          adm.NO_TIER_LABEL.get(adm.PRODUCT_BILI, ""))

    # 后台不能把 B 站码的档位写成 500 之类的（不分档必须 0）
    check("后台对不分档产品强制 tier=0",
          4 in adm.PRODUCTS_NO_TIER)

    # 反向：后台发的视频号码，客户端仍认视频号（别把老产品搞坏）
    v_code = adm.make_code(2000, product=adm.PRODUCT_VIDEO, max_devices=1)
    v_info = lic.verify_code(v_code)
    check("后台发的视频号码 → 客户端仍是视频号 2000",
          bool(v_info) and v_info["product"] == 1 and v_info["tier"] == 2000,
          f"{v_info and v_info['product']}/{v_info and v_info['tier']}")
    check("…且不含 B 站", not lic.is_bilibili_product(v_info))


# ══════════════════ C. 界面按钮 ══════════════════
def test_gui():
    print("\n[C] 界面按钮判定")
    from sphdl import gui as G

    bili_code = lic.make_code(0, product=lic.PRODUCT_BILIBILI)
    video_code = lic.make_code(1000)

    def run_case(name, codes, expect_bili, expect_tier_text=None):
        write_config(codes)
        try:
            app = G.App()
        except Exception as e:                # noqa: BLE001
            check(f"{name}：启动 App", False, f"{type(e).__name__}: {e}")
            return
        try:
            got = app._refresh_bili_button()
            check(f"{name}：B 站按钮可用 = {expect_bili}", bool(got) == expect_bili,
                  f"返回 {got}")
            style = ""
            try:
                style = str(app.btn_bili.cget("style"))
            except Exception:
                pass
            want_style = "Bili.TButton" if expect_bili else "BiliLocked.TButton"
            check(f"{name}：按钮样式 = {want_style}", style == want_style, style)
            txt = ""
            try:
                txt = str(app.btn_bili.cget("text"))
            except Exception:
                pass
            has_lock = "🔒" in txt
            check(f"{name}：未授权显示 🔒 / 已授权不显示",
                  has_lock is (not expect_bili), txt)
            if expect_tier_text:
                got_txt = app._tier_source_text()
                check(f"{name}：档位来源文案", got_txt == expect_tier_text,
                      f"「{got_txt}」")
        finally:
            try:
                app.store.save()
            except Exception:
                pass
            try:
                app.root.destroy()
            except Exception:
                pass

    # 只有视频号码 → B 站锁定（这才是"单独卖"的关键）
    run_case("只有视频号码", {1: video_code}, False, "已授权 · 永久")
    # 只有 B 站码 → B 站可用，且档位文案说清"视频号仍是默认档"
    run_case("只有 B 站码", {4: bili_code}, True,
             "B 站视频下载已授权 · 视频号默认档")
    # 两个码共存
    run_case("视频号 + B 站", {1: video_code, 4: bili_code}, True,
             "已授权 · 永久")

    # 未授权点按钮 → 必须弹「购买引导」，不能什么都不发生
    write_config({})
    try:
        app = G.App()
    except Exception as e:                       # noqa: BLE001
        check("未授权点 B 站按钮：启动 App", False, f"{type(e).__name__}: {e}")
        return
    try:
        from unittest import mock
        with mock.patch.object(G.messagebox, "askyesno", return_value=False) as m:
            app._on_bilibili()
            check("未授权点 B 站按钮 → 弹出购买引导", m.called,
                  f"调用 {m.call_count} 次")
            txt = ""
            if m.called:
                txt = " ".join(str(a) for a in m.call_args[0])
            check("引导文案写明「独立产品」", "独立产品" in txt, txt[:60])
            check("引导文案写明「B 站视频下载」授权码", "B 站视频下载" in txt)
            check("引导文案写明视频号码不含 B 站", "不含 B 站" in txt)
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
    print("v3.3.0 B 站独立授权验证")
    print("=" * 66)
    try:
        test_license_core()
        test_admin_server()
        test_gui()
    except Exception:                          # noqa: BLE001
        import traceback
        traceback.print_exc()
        FAILS.append("测试脚本自身异常")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print("   ·", f)
        raise SystemExit(1)
    print("✅ 全部通过")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
