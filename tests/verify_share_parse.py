"""网盘分享文字自动解析测试（v2.2.3）。

用户需求：
  「网盘每次生成的分享文字是这种格式
   通过网盘分享的文件：微信视频号下载器-v2.1.8-win64-portable.zip
   链接: https://pan.baidu.com/s/1sAqh5NZiMPGdceG4z_TEPA?pwd=bfcr 提取码: bfcr
   我希望是粘贴后，你自动解析，别让我一个个填版本号，文件名，链接，提取码」

覆盖各种真实粘贴形态（含用户原话、多空格、中文冒号、短链、
提取码单独一行、无提取码、非百度网盘、脏输入等）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl_admin as A

print("=" * 76)
print("P1 ★用户原话格式（百度网盘标准输出）")
print("=" * 76)
raw = """通过网盘分享的文件：微信视频号下载器-v2.1.8-win64-portable.zip
链接: https://pan.baidu.com/s/1sAqh5NZiMPGdceG4z_TEPA?pwd=bfcr 提取码: bfcr"""
r = A.parse_share_text(raw)
for k in ("ver", "filename", "link", "pwd"):
    print(f"  {k:9s}= {r[k]}")
check("P1a 版本号", r["ver"] == "2.1.8", r["ver"])
check("P1b 文件名", r["filename"] == "微信视频号下载器-v2.1.8-win64-portable.zip",
      r["filename"])
check("P1c 链接", r["link"].startswith("https://pan.baidu.com/s/1sAqh5NZiMPGdceG4z_TEPA"),
      r["link"])
check("P1d 提取码", r["pwd"] == "bfcr", r["pwd"])

print()
print("=" * 76)
print("P2 提取码单独一行")
print("=" * 76)
raw2 = """通过网盘分享的文件：微信视频号下载器-v2.2.2-win64-portable.zip
链接: https://pan.baidu.com/s/1Rx67APA2vyY5WEgtcPJisg
提取码：v576"""
r2 = A.parse_share_text(raw2)
print(f"  ver={r2['ver']} pwd={r2['pwd']} link={r2['link'][:52]}")
check("P2a 版本 2.2.2", r2["ver"] == "2.2.2", r2["ver"])
check("P2b 中文冒号提取码", r2["pwd"] == "v576", r2["pwd"])

print()
print("=" * 76)
print("P3 英文冒号 + 多余空格 + 前后噪音")
print("=" * 76)
raw3 = """  【百度网盘】分享成功
  通过网盘分享的文件:   微信视频号下载器-v2.2.2-win64-portable.zip
  链接:   https://pan.baidu.com/s/1Rx67APA2vyY5WEgtcPJisg?pwd=v576   提取码:  v576
  复制这段内容打开百度网盘手机App"""
r3 = A.parse_share_text(raw3)
print(f"  ver={r3['ver']} pwd={r3['pwd']}")
print(f"  fn={r3['filename']}")
check("P3a 英文冒号也能解析版本", r3["ver"] == "2.2.2", r3["ver"])
check("P3b 噪音文字不影响文件名", "噪音" not in r3["filename"], r3["filename"][:30])
check("P3c 链接尾部无多余空白", r3["link"].endswith("v576"), r3["link"][-12:])

print()
print("=" * 76)
print("P4 链接不带 pwd，提取码只有「提取码: xxxx」")
print("=" * 76)
raw4 = """通过网盘分享的文件：微信视频号下载器-v2.0.1-win64-portable.zip
链接: https://pan.baidu.com/s/1abcdefG
提取码: q9w8"""
r4 = A.parse_share_text(raw4)
print(f"  ver={r4['ver']} pwd={r4['pwd']} link={r4['link']}")
check("P4a 版本", r4["ver"] == "2.0.1", r4["ver"])
check("P4b 提取码", r4["pwd"] == "q9w8", r4["pwd"])
check("P4c 链接不含 pwd", "pwd=" not in r4["link"], r4["link"])

print()
print("=" * 76)
print("P5 百度短链 share/init?surl=")
print("=" * 76)
raw5 = "链接: https://pan.baidu.com/share/init?surl=AbCdEfG 提取码: 88aa"
r5 = A.parse_share_text(raw5)
print(f"  link={r5['link']}  pwd={r5['pwd']}")
check("P5a 短链能识别", "share/init?surl=AbCdEfG" in r5["link"], r5["link"])
check("P5b 提取码", r5["pwd"] == "88aa", r5["pwd"])

print()
print("=" * 76)
print("P6 只有文件名，没有链接（版本仍能抽出）")
print("=" * 76)
raw6 = "微信视频号下载器-v2.2.10-win64-portable.zip"
r6 = A.parse_share_text(raw6)
print(f"  ver={r6['ver']} fn={r6['filename']}")
check("P6a 双位版本号 2.2.10", r6["ver"] == "2.2.10", r6["ver"])
check("P6b 文件名", r6["filename"].endswith(".zip"), r6["filename"])

print()
print("=" * 76)
print("P7 空输入 / 垃圾输入不炸")
print("=" * 76)
for label, txt in (("空字符串", ""), ("None", None), ("纯文字", "你好啊"), ("只有链接", "https://example.com/x")):
    try:
        rr = A.parse_share_text(txt)
        ok = isinstance(rr, dict) and set(rr) == {"ver", "filename", "link", "pwd"}
        print(f"  {label:8s} → {rr}")
        check(f"P7 {label} 不炸且结构正确", ok)
    except Exception as e:
        check(f"P7 {label} 不炸且结构正确", False, str(e))


print()
print("=" * 76)
print("P10 ★夸克网盘（用户原话格式）")
print("=" * 76)
quark = """我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」，点击链接或复制整段内容，打开「夸克网盘APP」即可获取。
/~f3fa3bgctl~/:/
链接：https://pan.quark.cn/s/4402de3baafd?pwd=xsSS
提取码：xsSS"""
rq = A.parse_share_text(quark)
for k in ("ver", "filename", "link", "pwd"):
    print(f"  {k:9s}= {rq[k]}")
check("P10a 版本号 2.2.2", rq["ver"] == "2.2.2", rq["ver"])
check("P10b 文件名（从「」里取）",
      rq["filename"] == "微信视频号下载器-v2.2.2-win64-portable.zip",
      rq["filename"])
check("P10c 链接是 pan.quark.cn",
      rq["link"] == "https://pan.quark.cn/s/4402de3baafd?pwd=xsSS", rq["link"])
check("P10d ★没有误抓 /~f3fa3bgctl~/ 那行",
      "f3fa3bgctl" not in rq["link"], rq["link"])
check("P10e 提取码 xsSS", rq["pwd"] == "xsSS", rq["pwd"])

print()
print("=" * 76)
print("P11 夸克：提取码单独一行 + 链接不带 pwd")
print("=" * 76)
quark2 = """我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」
链接：https://pan.quark.cn/s/4402de3baafd
提取码：xsSS"""
rq2 = A.parse_share_text(quark2)
print(f"  link={rq2['link']}  pwd={rq2['pwd']}")
check("P11a 链接不含 pwd", "pwd=" not in rq2["link"], rq2["link"])
check("P11b 提取码仍能取到", rq2["pwd"] == "xsSS", rq2["pwd"])

print()
print("=" * 76)
print("P12 夸克：链接后紧跟中文（无空格）")
print("=" * 76)
quark3 = "我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」，链接：https://pan.quark.cn/s/4402de3baafd?pwd=xsSS提取码：xsSS"
rq3 = A.parse_share_text(quark3)
print(f"  link={rq3['link']}")
check("P12a 链接不含尾部中文", not any('\u4e00' <= c <= '\u9fff' for c in rq3["link"]),
      rq3["link"])

print()
print("=" * 76)
print("P13 百度 + 夸克混合（两段都贴进来，取第一个链接）")
print("=" * 76)
mix = """通过网盘分享的文件：微信视频号下载器-v2.1.8-win64-portable.zip
链接: https://pan.baidu.com/s/1sAqh5NZiMPGdceG4z_TEPA?pwd=bfcr 提取码: bfcr
---
我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」
链接：https://pan.quark.cn/s/4402de3baafd?pwd=xsSS"""
rm2 = A.parse_share_text(mix)
print(f"  ver={rm2['ver']} link={rm2['link'][:56]}")
check("P13a 取到百度那条（先出现的）",
      "pan.baidu.com" in rm2["link"], rm2["link"][:40])


print()
print("=" * 76)
print("P14 ★同一版本双网盘并存（百度 + 夸克各存一份）")
print("=" * 76)
import tempfile, json
tdir2 = Path(tempfile.mkdtemp(prefix="chan_"))
A.SNIPPETS = tdir2 / "snippets.json"

def save(raw_text):
    """模拟服务端保存（只传 raw，走兜底解析）"""
    d = A.snippets_load()
    pd = A.parse_share_text(raw_text)
    ver = pd["ver"]
    link = pd["link"]
    chan = A.channel_of(link)
    d["versions"] = [v for v in d["versions"]
                     if not (v["ver"] == ver
                             and (v.get("channel") or A.channel_of(v.get("link", ""))) == chan)]
    d["versions"].append({"ver": ver, "channel": chan,
                          "filename": pd["filename"], "link": link,
                          "pwd": pd["pwd"]})
    A.snippets_save(d)
    return ver, chan

b = save("""通过网盘分享的文件：微信视频号下载器-v2.2.2-win64-portable.zip
链接: https://pan.baidu.com/s/1BAIDUxxx?pwd=v576 提取码: v576""")
print(f"  存百度: ver={b[0]} channel={b[1]}")
q = save("""我用夸克网盘给你分享了「微信视频号下载器-v2.2.2-win64-portable.zip」
链接：https://pan.quark.cn/s/4402de3baafd?pwd=xsSS
提取码：xsSS""")
print(f"  存夸克: ver={q[0]} channel={q[1]}")

d = A.snippets_load()
print(f"  版本条目数: {len(d['versions'])}（同版本两个网盘应各存一份）")
for v in d["versions"]:
    print(f"    v{v['ver']} [{v['channel']}] {v['link'][:44]}")
check("P14a 同版本百度+夸克各存一份", len(d["versions"]) == 2, str(len(d["versions"])))
check("P14b 两个渠道都在",
      sorted(v["channel"] for v in d["versions"]) == ["夸克", "百度"],
      str([v["channel"] for v in d["versions"]]))

# 同版本同渠道 → 覆盖
save("""通过网盘分享的文件：微信视频号下载器-v2.2.2-win64-portable.zip
链接: https://pan.baidu.com/s/1NEWLINK?pwd=aaaa 提取码: aaaa""")
d = A.snippets_load()
print(f"  同渠道再存后条目数: {len(d['versions'])}")
check("P14c 同版本同渠道是覆盖不是新增", len(d["versions"]) == 2,
      str(len(d["versions"])))

# 话术按渠道取
t_b = A.snippet_text("2.2.2", "百度")
t_q = A.snippet_text("2.2.2", "夸克")
print(f"  百度话术含 baidu: {'baidu.com' in t_b}")
print(f"  夸克话术含 quark: {'quark.cn' in t_q}")
check("P14d 按渠道取到正确话术",
      "baidu.com" in t_b and "quark.cn" in t_q)

print()
print("=" * 76)
print("P15 channel_of 域名识别")
print("=" * 76)
for link, want in (
    ("https://pan.quark.cn/s/abc", "夸克"),
    ("https://pan.baidu.com/s/abc", "百度"),
    ("https://drive.uc.cn/s/abc", "UC"),
    ("https://www.aliyundrive.com/s/abc", "阿里"),
    ("https://115.com/s/abc", "115"),
    ("https://example.com/x", "其他"),
):
    got = A.channel_of(link)
    print(f"  {link[:40]:<42} → {got}")
    check(f"P15 {want}", got == want, got)

print()
print("=" * 76)
print("P8 页面含粘贴框 + 自动解析脚本")
print("=" * 76)
src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
for kw, desc in (
    ('name="raw"', "粘贴文本框（name=raw，服务端兜底可用）"),
    ("autoParse()", "前端自动解析函数"),
    ("JS_PASTE", "解析脚本常量"),
    ("id=\"f_ver\"", "版本号输入框"),
    ("id=\"f_fn\"", "文件名输入框"),
    ("id=\"f_lk\"", "链接输入框"),
    ("id=\"f_pw\"", "提取码输入框"),
):
    ok = kw in src
    print(f"  {'✅' if ok else '❌'} {desc}")
    check(f"P8 {desc}", ok)

# 检查 JS 里的正则没被双重转义（\\\\s 是错的，\s 是对的）
print()
print("  检查 JS 正则转义：")
js = A.JS_PASTE
bad = "\\\\s" in js
print(f"    JS 含双重反斜杠 \\\\s : {bad} {'❌' if bad else '✅'}")
check("P8b JS 正则未被双重转义", not bad)

print()
print("=" * 76)
print("P9 服务端兜底：只提交 raw 也能保存")
print("=" * 76)
import tempfile
tdir = Path(tempfile.mkdtemp(prefix="raw_"))
A.SNIPPETS = tdir / "snippets.json"
parsed = A.parse_share_text(raw)
print(f"  仅粘贴 raw → 解析出 ver={parsed['ver']} pwd={parsed['pwd']}")
check("P9a 兜底解析能拿到版本", parsed["ver"] == "2.1.8", parsed["ver"])
check("P9b 兜底解析能拿到链接", parsed["link"].startswith("https://"), parsed["link"][:40])

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
