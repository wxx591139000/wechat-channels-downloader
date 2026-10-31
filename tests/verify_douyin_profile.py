"""抖音主页批量抓取（方案 B）验证。

═══ 客户需求 ═══
方案 A 是「粘贴链接批量下载」，客户要「输入主页链接 → 抓该博主全部作品」。

═══ 实现要点（不猜接口）═══
抖音 web 端作品列表请求带 `a_bogus` 签名（页面 JS 算的），自己构造请求必被拒。
所以用**真实 Edge 打开主页，让页面自己发请求，我们读响应**：
  打开 /user/<sec_uid> → 监听 aweme/post 响应 → 解析 → 滚动触发更多 → 直到到底

═══ 本测试覆盖（不需要网络）═══
U1  URL 归一化：标准主页 / 短链 / modal_id / 裸 sec_uid / 作品页 / 垃圾
U2  列表响应解析：标准字段 / 变体字段 / 空列表
U3  ★条目解析：真实结构的 aweme → DouyinVideo（含地址/title/时长）
U4  ★图文条目要跳过（没有 video.play_addr）
U5  ★bit_rate 兜底（play_addr 为空时取最高档）
U6  接口识别：只认 aweme/post，不认 aweme/detail
U7  非法链接给出明确错误（不静默失败）
U8  ★错误信息要提到"未登录"这个最常见原因
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl import douyin_profile as DP

UID = "MS4wLjABAAAA" + "x" * 30

print("=" * 74)
print("U1 URL 归一化")
print("=" * 74)
cases = [
    (f"https://www.douyin.com/user/{UID}", "profile", False),
    (f"https://www.douyin.com/user/{UID}?from_tab_name=main", "profile", False),
    (f"https://www.douyin.com/user/{UID}?modal_id=7300000000000000000",
     "profile", True),
    (UID, "profile", False),
    ("https://v.douyin.com/abc123/", "short", False),
    ("7.68 复制打开抖音 https://v.douyin.com/xyz789/ 看视频", "short", False),
    ("https://www.douyin.com/video/7300000000000000000", "video", False),
    ("随便一段话没有链接", "unknown", False),
]
for text, exp_kind, exp_modal in cases:
    r = DP.normalize(text)
    ok = r["kind"] == exp_kind and r["is_modal"] == exp_modal
    check(f"  {text[:46]:48s}", ok, f"{r['kind']}/{r['is_modal']}")

print()
print("=" * 74)
print("U2 列表响应解析")
print("=" * 74)
lst, hm, cur = DP._extract_list({
    "aweme_list": [{"aweme_id": "1"}, {"aweme_id": "2"}],
    "has_more": 1, "max_cursor": 1700000000000})
check("U2a 标准字段", len(lst) == 2 and hm is True and cur == 1700000000000)
lst2, hm2, cur2 = DP._extract_list({
    "awemeList": [{"aweme_id": "9"}], "hasMore": False, "cursor": 5})
check("U2b 驼峰变体", len(lst2) == 1 and hm2 is False and cur2 == 5)
lst3, hm3, _ = DP._extract_list({"aweme_list": [], "has_more": 0})
check("U2c 空列表+到底", lst3 == [] and hm3 is False)
check("U2d 非 dict 输入不崩", DP._extract_list(None) == ([], False, 0))

print()
print("=" * 74)
print("U3 条目解析（真实抖音结构）")
print("=" * 74)
REAL_ITEM = {
    "aweme_id": "7301234567890123456",
    "desc": "测试视频标题 #话题",
    "duration": 15500,
    "aweme_type": 0,
    "author": {"nickname": "测试博主"},
    "video": {
        "play_addr": {
            "url_list": ["https://v3-web.douyinvod.com/xxx/video.mp4?a=1"],
            "data_size": 5234567,
            "width": 1080, "height": 1920,
        },
        "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpg"]},
    },
}
v = DP._parse_item(REAL_ITEM)
check("U3a 解析成功", v is not None)
if v:
    check("U3b aweme_id", v.aweme_id == "7301234567890123456")
    check("U3c 标题", v.title == "测试视频标题 #话题")
    check("U3d 作者", v.author == "测试博主")
    check("U3e 时长", v.duration_ms == 15500, v.duration_text)
    check("U3f 下载地址", "douyinvod" in v.play_url)
    check("U3g 大小", v.size == 5234567, v.size_text)
    check("U3h 分辨率", v.width == 1080 and v.height == 1920)
    check("U3i ok 属性", v.ok is True)

print()
print("=" * 74)
print("U4 图文/无视频条目跳过")
print("=" * 74)
IMG_ITEM = {
    "aweme_id": "730999",
    "desc": "这是图文",
    "aweme_type": 68,
    "images": [{"url_list": ["https://x/1.jpg"]}],
    # 没有 video 字段
}
check("U4a 图文条目返回 None", DP._parse_item(IMG_ITEM) is None)
NOV_URL = {"aweme_id": "730888", "desc": "x", "video": {"play_addr": {}}}
check("U4b 无地址返回 None", DP._parse_item(NOV_URL) is None)
check("U4c 非 dict 输入返回 None", DP._parse_item("abc") is None)

print()
print("=" * 74)
print("U5 bit_rate 兜底")
print("=" * 74)
BR_ITEM = {
    "aweme_id": "730777",
    "desc": "bitrate 兜底",
    "duration": 8000,
    "author": {"nickname": "A"},
    "video": {
        "play_addr": {"url_list": []},          # 主地址为空
        "bit_rate": [
            {"play_addr": {"url_list": ["https://low/v.mp4"], "data_size": 100}},
            {"play_addr": {"url_list": ["https://high/v.mp4"], "data_size": 9999}},
        ],
    },
}
vb = DP._parse_item(BR_ITEM)
check("U5a 兜底成功", vb is not None)
if vb:
    check("U5b ★取了最高码率", "high" in vb.play_url, vb.play_url)
    check("U5c 大小正确", vb.size == 9999, str(vb.size))

print()
print("=" * 74)
print("U6 接口识别")
print("=" * 74)
check("U6a 认 aweme/post",
      DP._looks_like_list("https://www.douyin.com/aweme/v1/web/aweme/post/?x=1"))
check("U6b 不认 aweme/detail",
      not DP._looks_like_list("https://www.douyin.com/aweme/v1/web/aweme/detail/"))
check("U6c 不认其他接口",
      not DP._looks_like_list("https://www.douyin.com/aweme/v1/web/comment/list/"))

print()
print("=" * 74)
print("U7-U8 错误处理")
print("=" * 74)
res = DP.fetch_profile("https://www.douyin.com/video/7300000000000000000")
check("U7a 作品页链接被拒", bool(res.error), res.error[:50])
res2 = DP.fetch_profile("随便一句废话")
check("U7b 垃圾输入被拒", bool(res2.error))
# U8：未登录提示（看源码里的错误文案）
src = (ROOT / "sphdl" / "douyin_profile.py").read_text(encoding="utf-8")
check("U8 ★错误信息提到「未登录」", "未登录" in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
