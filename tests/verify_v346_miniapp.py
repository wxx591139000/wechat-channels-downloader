"""v3.4.6 回归：小程序短剧（客户口语「绿泡泡短剧」）独立授权。

背景（2026-11-13 客户要求）：
  「绿泡泡是不是也正常？把绿泡泡独立授权卖，放到发码平台里」
  → 这个功能一直是**随视频号免费送**的（嗅探时被动落盘，没有任何闸门），
    要单独卖就必须在落盘那一步拦：未授权 → 不落盘、不建目录、不占空间。

覆盖：
  A license.py —— PRODUCT_MINIAPP=6 / 不分档 / 发码-校验往返 / 展示名
  B addon     —— 闸门开关真的能拦（False 不落盘、True 正常落盘）
  C GUI       —— 按钮存在、未授权带 🔒、已授权解锁、addon 闸门实时同步
  D 发码后台  —— 下拉里有第 6 项、台账标签齐
"""
import json
import sys
import tempfile
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


# ===================== A license =====================
def test_license():
    print("\n--- A license ---")
    from sphdl import license as lic

    check("A1 产品号 = 6", lic.PRODUCT_MINIAPP == 6, str(lic.PRODUCT_MINIAPP))
    check("A2 在 PRODUCT_LABELS 里",
          lic.PRODUCT_LABELS.get(6) == "小程序短剧下载（绿泡泡）",
          str(lic.PRODUCT_LABELS.get(6)))
    check("A3 在 PRODUCTS_NO_TIER 里", 6 in lic.PRODUCTS_NO_TIER,
          str(lic.PRODUCTS_NO_TIER))
    check("A4 FLAG_ALL 随产品表推导（含第 6 位）",
          lic.FLAG_ALL == (1 << 6) - 1, str(lic.FLAG_ALL))

    code = lic.make_code(0, days=0, product=lic.PRODUCT_MINIAPP)
    info = lic.verify_code(code)
    check("A5 发码→校验往返（product=6, tier=0）",
          bool(info) and info.get("product") == 6 and info.get("tier") == 0,
          f"{code} → {info and (info.get('product'), info.get('tier'))}")
    check("A6 is_miniapp_product 只认 6",
          lic.is_miniapp_product(info) and
          not lic.is_miniapp_product(lic.verify_code(
              lic.make_code(20000, product=1))),
          "")
    check("A7 展示名写的是不分档",
          "不分档" in lic.label_of_product(info), lic.label_of_product(info))
    check("A8 传档位也会被归一成 0（不分档产品）",
          (lic.verify_code(lic.make_code(2000, product=6)) or {}).get("tier") == 0)


# ===================== B addon 闸门 =====================
class FakeResp:
    def __init__(self, status, body=b""):
        self.status_code = status
        self.content = body
        self.headers = {}
        self.stream = False


class FakeReq:
    def __init__(self, path):
        self.path = path
        self.host = "33wozf.vodpreview.wxamedia.com"


class FakeFlow:
    def __init__(self, status, body=b"", path="/vod/abcdef0123456789abcdef/f0.mp4?t=1&us=2"):
        self.response = FakeResp(status, body)
        self.request = FakeReq(path)


def test_addon():
    print("\n--- B addon 闸门 ---")
    from sphdl import config
    from sphdl.addon import SphAddon

    td = Path(tempfile.mkdtemp(prefix="mini346_"))

    class Host:
        def __init__(self, allow):
            self._miniapp_saved = 0
            self.allow_miniapp = allow
            self._miniapp_denied_logged = False
            self.out_dir = str(td)

    # 未授权：一律不落盘
    h_deny = Host(False)
    SphAddon._save_miniapp_media(h_deny, FakeFlow(200, b"x" * 4096))
    base = td / "小程序短剧"
    files = list(base.rglob("*")) if base.exists() else []
    check("B1 未授权 → 不建目录/不落盘", not base.exists() or not files, str(files))
    check("B2 未授权只提示一次（_miniapp_denied_logged）",
          h_deny._miniapp_denied_logged is True)

    # 已授权：正常落盘
    h_ok = Host(True)
    SphAddon._save_miniapp_media(h_ok, FakeFlow(200, b"y" * 8192))
    # 只算剧集文件（目录里还有 .seq.json 这种状态文件）
    files = [p for p in base.rglob("*.mp4")] if base.exists() else []
    check("B3 已授权 → 落盘成功", len(files) >= 1, str([p.name for p in files][:2]))
    check("B4 落盘到 <下载目录>/小程序短剧",
          all(p.parent == base or base in p.parents for p in files),
          str(files[0].parent if files else ""))
    check("B5 文件名带「第NN集」", bool(files) and "第" in files[0].name,
          files[0].name if files else "")

    # 构造参数与默认值
    a = src("sphdl/addon.py")
    check("B6 __init__ 有 allow_miniapp 参数", "allow_miniapp: bool = True" in a)
    check("B7 闸门在 _save_miniapp_media 最前面",
          a.index("if not getattr(self, \"allow_miniapp\", True):")
          < a.index("resp = flow.response", a.index("def _save_miniapp_media")))


# ===================== C GUI =====================
def _make_cfg(with_code: bool) -> Path:
    from sphdl import license as lic
    td = Path(tempfile.mkdtemp(prefix="mini346gui_"))
    cfg = {}
    if with_code:
        cfg["license_codes"] = {"6": lic.make_code(0, days=0, product=6)}
    (td / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (td / "records.json").write_text("[]", encoding="utf-8")
    return td


def test_gui():
    print("\n--- C GUI ---")
    import sphdl.gui as G

    td1 = _make_cfg(False)
    G.CONFIG_FILE = td1 / "config.json"
    G.RECORDS_FILE = td1 / "records.json"
    app = G.App()
    app.root.withdraw()
    app.root.update()
    check("C1 有「🧩 绿泡泡短剧」按钮", hasattr(app, "btn_mini"))
    check("C2 未授权显示 🔒", "🔒" in str(app.btn_mini.cget("text")),
          str(app.btn_mini.cget("text")))
    check("C3 未授权时 addon 闸门关着",
          getattr(app.addon, "allow_miniapp", None) is False,
          str(getattr(app.addon, "allow_miniapp", None)))
    check("C4 未授权样式是 MiniLocked",
          str(app.btn_mini.cget("style")) == "MiniLocked.TButton",
          str(app.btn_mini.cget("style")))
    app.root.destroy()

    td2 = _make_cfg(True)
    G.CONFIG_FILE = td2 / "config.json"
    G.RECORDS_FILE = td2 / "records.json"
    app2 = G.App()
    app2.root.withdraw()
    app2.root.update()
    check("C5 已授权去掉 🔒", "🔒" not in str(app2.btn_mini.cget("text")),
          str(app2.btn_mini.cget("text")))
    check("C6 已授权时 addon 闸门打开",
          getattr(app2.addon, "allow_miniapp", None) is True)
    check("C7 已授权样式是 Mini", str(app2.btn_mini.cget("style")) == "Mini.TButton",
          str(app2.btn_mini.cget("style")))
    # 实时同步：撤销授权后刷新按钮 → 闸门立刻关上（不用重启）
    app2.cfg.license_codes.pop(6, None)
    app2._refresh_mini_button()
    check("C8 授权撤销后闸门立刻关闭（不用重启）",
          app2.addon.allow_miniapp is False and
          "🔒" in str(app2.btn_mini.cget("text")))
    app2.root.destroy()


# ===================== D 发码后台 =====================
def test_admin():
    print("\n--- D 发码后台 ---")
    s = src("server/sphdl_admin.py")
    check("D1 后台有 PRODUCT_MINIAPP = 6", "PRODUCT_MINIAPP = 6" in s)
    check("D2 后台归到不分档", "PRODUCT_MINIAPP" in
          s.split("PRODUCTS_NO_TIER = (")[1].split(")")[0])
    check("D3 发码页有第 6 项下拉",
          '<option value="6">小程序短剧下载（绿泡泡 · 不分档）</option>' in s)
    check("D4 台账标签齐", "小程序短剧下载（不分档）" in s)
    check("D5 话术前缀齐", "小程序短剧下载授权码：" in s)


def main():
    test_license()
    test_addon()
    test_gui()
    test_admin()
    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
