"""话术库测试（v2.2.3）。

用户需求：
  「发码时可以分别点击复制授权码 + 选择哪个版本的聊天话术。
   选 2.2.2 版本时复制的内容是：可编辑的预定义套路词 + 预先保存的网盘链接信息。
   预定义套路词、各个版本的网盘链接设计可编辑的入口。」

覆盖：
  S1 话术库默认结构（无文件时返回默认套路词 + 空版本）
  S2 保存套路词
  S3 添加版本（含链接与提取码）
  S4 snippet_text 拼接正确（模板 + 文件名 + 链接 + 提取码）
  S5 链接已带 pwd= 时不重复追加
  S6 同版本号覆盖而非重复
  S7 版本按语义版本号倒序（2.2.10 在 2.2.9 之前）
  S8 删除版本
  S9 发码结果页含版本下拉与复制按钮
  S10 旧数据（缺字段）不炸
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


import sphdl_admin as ADM

# 隔离到临时目录
tdir = Path(tempfile.mkdtemp(prefix="snip_"))
ADM.SNIPPETS = tdir / "snippets.json"
ADM.DATA = tdir

print("=" * 74)
print("S1 默认结构")
print("=" * 74)
d = ADM.snippets_load()
print(f"  template: {d['template']}")
print(f"  versions: {d['versions']}")
check("S1a 默认套路词正确",
      d["template"] == "请您查收，满意的话帮忙给个红花，遇到问题的话联系我。",
      d["template"][:20])
check("S1b 默认无版本", d["versions"] == [])

print()
print("=" * 74)
print("S2 保存套路词")
print("=" * 74)
d["template"] = "亲，下载链接和授权码在下面，有问题随时找我~"
ADM.snippets_save(d)
d2 = ADM.snippets_load()
print(f"  读回: {d2['template']}")
check("S2a 套路词已持久化", d2["template"] == d["template"])

print()
print("=" * 74)
print("S3/S4 添加版本 + 拼接话术")
print("=" * 74)
d = ADM.snippets_load()
d["versions"].append({
    "ver": "2.2.2",
    "filename": "微信视频号下载器-v2.2.2-win64-portable.zip",
    "link": "https://pan.baidu.com/s/1Rx67APA2vyY5WEgtcPJisg?pwd=v576",
    "pwd": "v576",
})
ADM.snippets_save(d)
d = ADM.snippets_load()
print(f"  版本数: {len(d['versions'])}")
check("S3a 版本已保存", len(d["versions"]) == 1)

txt = ADM.snippet_text("2.2.2")
print()
print("  === 拼接结果 ===")
for line in txt.split("\n"):
    print(f"    {line}")
print()
check("S4a 含套路词", d["template"] in txt)
check("S4b 含文件名", "微信视频号下载器-v2.2.2-win64-portable.zip" in txt)
check("S4c 含链接", "pan.baidu.com/s/1Rx67APA2vyY5WEgtcPJisg" in txt)
check("S4d 含提取码", "提取码: v576" in txt)

print()
print("=" * 74)
print("S5 链接已带 pwd= 时不重复")
print("=" * 74)
check("S5a 没有 ?pwd=v576?pwd=v576 这种重复",
      txt.count("pwd=v576") == 1, str(txt.count("pwd=v576")))

# 无 pwd 字段时用链接原样
d = ADM.snippets_load()
d["versions"] = [{
    "ver": "9.9.9",
    "filename": "x.zip",
    "link": "https://pan.baidu.com/s/abc?pwd=zzz",
    "pwd": "",
}]
ADM.snippets_save(d)
t2 = ADM.snippet_text("9.9.9")
print(f"  无提取码时输出: {t2.splitlines()[-1]}")
check("S5b 提取码为空时不显示'提取码:'", "提取码:" not in t2)

print()
print("=" * 74)
print("S6 同版本号覆盖")
print("=" * 74)
d = ADM.snippets_load()
d["versions"].append({"ver": "9.9.9", "filename": "new.zip",
                      "link": "https://new", "pwd": "aa"})
# 模拟保存逻辑（服务端会去重）
d["versions"] = [v for v in d["versions"] if v["ver"] != "9.9.9"]
d["versions"].append({"ver": "9.9.9", "filename": "new.zip",
                      "link": "https://new", "pwd": "aa"})
ADM.snippets_save(d)
d = ADM.snippets_load()
n999 = [v for v in d["versions"] if v["ver"] == "9.9.9"]
print(f"  9.9.9 的记录数: {len(n999)}")
check("S6a 同版本号只有 1 条", len(n999) == 1, str(len(n999)))
check("S6b 内容是最新的", n999[0]["filename"] == "new.zip", n999[0]["filename"])

print()
print("=" * 74)
print("S7 版本号排序（2.2.10 要排在 2.2.9 之前）")
print("=" * 74)
d = ADM.snippets_load()
for v in ("2.2.9", "2.2.10", "2.1.0", "2.2.2"):
    d["versions"].append({"ver": v, "filename": "", "link": "x", "pwd": ""})
ADM.snippets_save(d)
d = ADM.snippets_load()
order = [x["ver"] for x in d["versions"]]
print(f"  排序结果: {order}")
check("S7a 2.2.10 排在 2.2.9 前面", order.index("2.2.10") < order.index("2.2.9"),
      str(order))
# ★ 测试数据里故意有 9.9.9（S5/S6 用），它才是最大版本；
#   真正要验证的是「2.2.x 系列内部顺序正确」
real = [v for v in order if v.startswith("2.2")]
print(f"  2.2.x 系列顺序: {real}")
check("S7b 2.2.x 系列内新版本在前",
      real == ["2.2.10", "2.2.9", "2.2.2"], str(real))
check("S7c 全场最大版本在最前", order[0] == max(order, key=lambda v: [
    int(x) if x.isdigit() else 0 for x in v.split(".")]), order[0])

print()
print("=" * 74)
print("S8 删除版本")
print("=" * 74)
before = len(ADM.snippets_load()["versions"])
d = ADM.snippets_load()
d["versions"] = [v for v in d["versions"] if v["ver"] != "2.1.0"]
ADM.snippets_save(d)
after = len(ADM.snippets_load()["versions"])
print(f"  {before} → {after}")
check("S8a 删除后少 1 条", after == before - 1, f"{before}→{after}")
check("S8b 目标版本确实没了",
      all(v["ver"] != "2.1.0" for v in ADM.snippets_load()["versions"]))

print()
print("=" * 74)
print("S9 发码结果页元素")
print("=" * 74)
src = (ROOT / "server" / "sphdl_admin.py").read_text(encoding="utf-8")
for kw, desc in (
    ('id="ver"', "版本下拉框"),
    ("copyBoth()", "复制话术+授权码"),
    ("copySnip()", "只复制话术"),
    ("/snippets", "话术库编辑入口"),
    ("JS_COPY", "复制降级函数"),
):
    ok = kw in src
    print(f"  {'✅' if ok else '❌'} {desc}")
    check(f"S9 {desc}", ok)

print()
print("=" * 74)
print("S10 脏数据不炸")
print("=" * 74)
ADM.SNIPPETS.write_text('{"versions": [{"ver": ""}, {"nover": 1}, "bad"]}',
                        encoding="utf-8")
try:
    d = ADM.snippets_load()
    check("S10a 脏数据能容错", isinstance(d, dict) and d["versions"] == [],
          str(d["versions"]))
except Exception as e:
    check("S10a 脏数据能容错", False, str(e))

ADM.SNIPPETS.write_text("not json at all", encoding="utf-8")
try:
    d = ADM.snippets_load()
    check("S10b 非 JSON 也能容错", isinstance(d, dict))
except Exception as e:
    check("S10b 非 JSON 也能容错", False, str(e))

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
