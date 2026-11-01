"""客户三个报障 + 链接形态的回归验证（v2.9.1）。

═══ 客户实际反馈 ═══
1. 「刚对抖音做了授权，发现『抖音批量下载』几个字看不见，颜色要改」
2. 「点击登录后，立刻显示『已登录抖音』，没让我输入用户密码，确实是成功登录了？」
3. 「抓该博主的全部作品（最多抓 500 条，可改）这个 500 条是授权配置？
    怎么在发码平台操作？」
4. 「输入 https://www.douyin.com/jingxuan?modal_id=7702018171426868531
    提示没找到抖音链接」
5. 「输入分享文案（含 v.douyin.com/Sn2cr70xG_0/）解析报错 executable doesn't exist」

═══ 覆盖 ═══
D1  ★modal_id 链接能被提取（客户原文）
D2  ★modal_id 能提取出 aweme_id
D3  ★分享文案里的短链能提取
D4  ★各种 douyin.com 路径都能提取（jingxuan/discover/follow/user/video/share）
D5  ★ttk 主题必须是 clam（vista 会忽略按钮配色 → 字看不见）
D6  ★浏览器启动候选里含显式 Edge 路径（修 executable doesn't exist）
D7  ★500 条是抓取上限，与授权无关（代码里不该读 license）
D8  错误信息是客户能看懂的（不是裸 Playwright 异常）
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


from sphdl.douyin import extract_urls, _aweme_id_from_url  # noqa: E402

print("=" * 74)
print("D1-D4 链接形态（客户实际遇到的）")
print("=" * 74)

# ★ 客户第 4 条原文
u1 = "https://www.douyin.com/jingxuan?modal_id=7702018171426868531"
got1 = extract_urls(u1)
check("D1 ★客户原链接能提取", len(got1) == 1, str(got1))
check("D1b ★能取到 aweme_id",
      _aweme_id_from_url(u1) == "7702018171426868531",
      _aweme_id_from_url(u1))

# ★ 客户第 5 条原文（分享文案）
u2 = ("4.15 10/03 mDH:/ :1pm G@v.Fh 33天城市生存攒钱挑战今天是第14天   "
      "https://v.douyin.com/Sn2cr70xG_0/ 复制此链接，打开Dou音搜索，直接观看视频！")
got2 = extract_urls(u2)
check("D3 ★客户分享文案能提取短链",
      len(got2) == 1 and "Sn2cr70xG_0" in got2[0], str(got2))

# 各种路径形态
variants = [
    "https://www.douyin.com/video/7300000000000000000",
    "https://www.douyin.com/discover?modal_id=7300000000000000001",
    "https://www.douyin.com/follow?modal_id=7300000000000000002",
    "https://www.douyin.com/user/MS4wLjABAAAAxxxx?modal_id=7300000000000000003",
    "https://www.douyin.com/share/video/7300000000000000004",
    "https://m.douyin.com/share/video/7300000000000000005",
    "https://www.iesdouyin.com/share/video/7300000000000000006",
]
ok_all = True
for v in variants:
    e = extract_urls(v)
    if not e:
        ok_all = False
        print(f"    ❌ 提取失败: {v}")
check("D4 ★7 种 douyin.com 路径都能提取", ok_all, f"{len(variants)} 种")

# modal_id 的 aweme_id
check("D4b discover?modal_id 能取 id",
      _aweme_id_from_url(variants[1]) == "7300000000000000001")
# 用内容匹配，不硬编码索引（第一版索引错位导致误报）
share_urls = [v for v in variants if "/share/video/" in v]
ok_share = all(_aweme_id_from_url(v)[-1] == v[-1] for v in share_urls)
check("D4c share/video 能取 id", ok_share and len(share_urls) == 3,
      f"{len(share_urls)} 个 share 链接")

print()
print("=" * 74)
print("D5 主题修复（按钮字看不见）")
print("=" * 74)
gui_src = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
check("D5a ★产品切到 clam 主题", 'theme_use("clam")' in gui_src)
check("D5b 注释说明了 vista 会忽略配色",
      "vista" in gui_src and ("忽略" in gui_src or "看不见" in gui_src))

# 实测：clam 下颜色真的生效
import tkinter as tk
from tkinter import ttk
root = tk.Tk()
root.withdraw()
st = ttk.Style()
st.theme_use("clam")
st.configure("TestT.TButton", background="#26a69a", foreground="#ffffff")
bg = st.lookup("TestT.TButton", "background")
fg = st.lookup("TestT.TButton", "foreground")
check("D5c ★clam 主题下 background 生效", bg == "#26a69a", bg)
check("D5d ★clam 主题下 foreground 生效", fg == "#ffffff", fg)
root.destroy()

print()
print("=" * 74)
print("D6 浏览器启动兜底（executable doesn't exist）")
print("=" * 74)
dy_src = (ROOT / "sphdl" / "douyin.py").read_text(encoding="utf-8")
check("D6a ★有显式 Edge 路径候选", "msedge.exe" in dy_src)
check("D6b ★有显式 Chrome 路径候选", "chrome.exe" in dy_src)
check("D6c 用 executable_path 启动", "executable_path" in dy_src)
check("D8 ★错误信息是客户能看懂的",
      "找不到可用的浏览器" in dy_src and "发给开发者" in dy_src)

print()
print("=" * 74)
print("D7 500 条上限与授权无关")
print("=" * 74)
prof_src = (ROOT / "sphdl" / "douyin_profile.py").read_text(encoding="utf-8")
check("D7a ★douyin_profile 不读授权信息",
      "license" not in prof_src.lower() and "is_douyin_product" not in prof_src)
check("D7b 上限是纯参数（max_items）", "max_items" in prof_src)
ui_src = (ROOT / "sphdl" / "douyin_ui.py").read_text(encoding="utf-8")
check("D7c 界面上限可改（Spinbox）", "Spinbox" in ui_src and "var_max" in ui_src)
check("D7d 上限范围 10~2000", "from_=10" in ui_src and "to=2000" in ui_src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
