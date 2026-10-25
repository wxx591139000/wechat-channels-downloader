"""评论抓不到（白名单太窄）+ 批量下载评论（v2.2.15）。

═══ 客户反馈（2026-10-23）═══
1. 「有些视频我看有评论，但工具里却显示没有拉取到评论」
2. 「需要有下载评论的按钮，并支持批量下载评论」

═══ 问题1 根因 ═══
拦截白名单只有 9 条，而视频号的评论接口散落在
wxa.wxs.qq.com / mmfinder.qq.com / finder.qq.com / wxis.qq.com /
wss.qq.com 等子域，全都不在白名单 → tls_clienthello 直接裸透传，
一个字节都看不到。评论区能不能抓到取决于接口恰好走没走白名单 → 时好时坏。

修法：拦截整个 *.qq.com + gtimg.cn；重型媒体（腾讯视频等）显式排除。

覆盖：
  W1 之前漏掉的评论接口域名，现在都要拦
  W2 重型媒体仍要透传（v.qq.com 等，避免视频卡断）
  W3 优酷/B站/百度等非腾讯站仍透传
  W4 排除优先于拦截（v.qq.com 不被 qq.com 通配吃掉）
  W5 「下载评论」按钮存在
  W6 批量导出：多选多条 → 每条约生成 txt+json
  W7 导出内容含嵌套回复（└ 缩进）
  W8 无评论的记录被跳过（不生成空文件）
  W9 文件名安全（标题含 / : * 等不报错）
  W10 txt/json 格式正确可读回
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


print("=" * 74)
print("W1-W4 拦截策略")
print("=" * 74)
from sphdl.addon import SphAddon

a = SphAddon(None)


def will_intercept(host):
    """复现 tls_clienthello 的判定顺序：先排除，后拦截。"""
    if any(rx.search(host) for rx in a._passthrough_re):
        return False
    return any(rx.search(host) for rx in a._intercept_re)


must_intercept = [
    "channels.weixin.qq.com", "finder.video.qq.com", "wxapp.tc.qq.com",
    "mp.weixin.qq.com", "res.wx.qq.com", "mmbiz.qpic.cn", "mpvideo.qpic.cn",
    # ★ 这些是之前漏掉的评论接口域名
    "wxa.wxs.qq.com", "mmfinder.qq.com", "finder.qq.com",
    "wxis.qq.com", "wss.qq.com", "api.finder.qq.com",
    "img.finder.qq.com", "wxa.qq.com", "wxaapi.qq.com",
]
for h in must_intercept:
    hit = will_intercept(h)
    print(f"  拦  {h:30s} {'✅' if hit else '❌ 漏了'}")
    check(f"W1 拦截 {h}", hit, "" if hit else "评论抓不到")

must_pass = [
    "v.qq.com", "vv.video.qq.com", "live.qq.com", "now.qq.com",   # 腾讯视频
    "www.youku.com", "v.youku.com", "www.bilibili.com",
    "www.baidu.com", "github.com", "www.iqiyi.com",
]
for h in must_pass:
    hit = will_intercept(h)
    print(f"  透 {h:30s} {'✅' if not hit else '❌ 被拦了'}")
    check(f"W2/W3 透传 {h}", not hit, "" if not hit else "会拖慢/卡断")

print()
print("=" * 74)
print("W5 按钮存在")
print("=" * 74)
gui_src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("W5a 有「⬇ 下载评论」按钮", "⬇ 下载评论" in gui_src)
check("W5b 绑定了 _download_comments", "command=self._download_comments" in gui_src)
check("W5c 有 _download_comments 方法", "def _download_comments(self):" in gui_src)

print()
print("=" * 74)
print("W6-W10 批量导出实测")
print("=" * 74)
import sphdl.gui as G

tdir = Path(tempfile.mkdtemp(prefix="dme_"))
G.CONFIG_FILE = tdir / "config.json"
G.RECORDS_FILE = tdir / "records.json"
dl_dir = tdir / "downloads"
G.CONFIG_FILE.write_text(json.dumps({"download_dir": str(dl_dir)}),
                         encoding="utf-8")
G.RECORDS_FILE.write_text("[]", encoding="utf-8")

app = G.App()
app.root.withdraw()
app.root.update()

CMTS = [
    {"nick": "粉丝甲", "content": "主评论", "likes": 10, "time": "21:05",
     "cid": "1", "replies": [
         {"nick": "粉丝乙", "content": "回复一", "likes": 2, "time": "21:10",
          "cid": "11", "replies": [
              {"nick": "粉丝甲", "content": "回复二", "likes": 0,
               "time": "21:12", "cid": "111", "replies": []}]}]},
    {"nick": "粉丝丙", "content": "另一条主评论", "likes": 0, "time": "",
     "cid": "2", "replies": []},
]

# 两条有评论（标题含非法字符），一条没评论
recs = []
for i, (title, cmts) in enumerate([
        ("视频A/带斜杠:和星号*", CMTS),
        ("视频B 正常标题", CMTS),
        ("视频C 没评论", []),
]):
    r = G.Record(id=f"r{i}", title=title, url=f"u{i}", decode_key="",
                 size=100, source="t",
                 created=time.strftime("%Y-%m-%d %H:%M:%S"),
                 status="done", video_id=f"v{i}", seq=i + 1,
                 comments=cmts, comment_count=69 if cmts else 0)
    app.store.add(r)
    app._add_row(r)
    recs.append(r.id)
app.root.update()

# 全选后批量导出
app.tree.selection_set(app.tree.get_children())
print(f"  选中的行数: {len(app.tree.selection())}")

out_dir = dl_dir / "评论"
# 直接调方法（会弹 messagebox，先 monkeypatch 掉）
import tkinter.messagebox as mb
_orig_info = mb.showinfo
_orig_warn = mb.showwarning
_orig_err = mb.showerror
captured_msgs = []
mb.showinfo = lambda *a, **k: captured_msgs.append(("info", a))
mb.showwarning = lambda *a, **k: captured_msgs.append(("warn", a))
mb.showerror = lambda *a, **k: captured_msgs.append(("err", a))
G.messagebox.showinfo = mb.showinfo
G.messagebox.showwarning = mb.showwarning
G.messagebox.showerror = mb.showerror

try:
    app._download_comments()
except Exception as e:
    print(f"  导出异常: {type(e).__name__}: {e}")

print(f"  输出目录: {out_dir}")
files = sorted(p.name for p in out_dir.glob("*")) if out_dir.exists() else []
print(f"  生成文件: {files}")

txts = [f for f in files if f.endswith(".txt")]
jsons = [f for f in files if f.endswith(".json")]
check("W6a 两条有评论的各生成 txt", len(txts) == 2, str(txts))
check("W6b 两条有评论的各生成 json", len(jsons) == 2, str(jsons))
check("W8 没评论的没生成文件",
      not any("视频C" in f for f in files),
      str([f for f in files if "视频C" in f]))
check("W9 非法字符被替换（文件名安全）",
      all("/" not in f and ":" not in f and "*" not in f for f in files),
      str(files[:2]))

# 检查内容
if txts:
    t = (out_dir / txts[0]).read_text(encoding="utf-8")
    print()
    print("  TXT 内容预览：")
    for ln in t.split("\n")[:9]:
        print("   ", ln)
    check("W7a TXT 含嵌套回复（└ 缩进）", "└ " in t, "")
    check("W7b TXT 含回复总数 69", "69" in t)
    check("W10a TXT 含主评论与回复",
          "主评论" in t and "回复一" in t and "回复二" in t)

if jsons:
    d = json.loads((out_dir / jsons[0]).read_text(encoding="utf-8"))
    print()
    print(f"  JSON 顶层字段: {list(d.keys())}")
    print(f"  comments 条数: {len(d.get('comments') or [])}")
    check("W10b JSON 可读回", isinstance(d, dict) and "comments" in d)
    check("W7c JSON 保留嵌套",
          len((d["comments"][0].get("replies") or [])) == 1,
          str(len((d["comments"][0].get("replies") or []))))
    check("W10c JSON 含 video_title", bool(d.get("video_title")), str(d.get("video_title"))[:20])

print(f"\n  弹窗消息: {[(k, str(v[1])[:40]) for k, v in captured_msgs]}")

# 还原
mb.showinfo = _orig_info
mb.showwarning = _orig_warn
mb.showerror = _orig_err
G.messagebox.showinfo = _orig_info
G.messagebox.showwarning = _orig_warn
G.messagebox.showerror = _orig_err

app.root.destroy()

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
