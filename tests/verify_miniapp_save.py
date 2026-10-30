"""小程序短剧「边播边存」验证（v2.7.7）。

═══ 客户需求（2026-10-30）═══
评估 `#小程序://绿泡泡短剧/U197oDp3WM94kVp` 能否批量下载。

═══ 实测结论（真实播放抓包）═══
· 视频是**完整 MP4**：`xxx.vodpreview.wxamedia.com/<vod>/<episode>/f0.mp4`
· 单集 3~7 MB，HTTP 206 + `content-range`
· ★URL 带 `t=` 时间戳签名，几分钟后 403
· ★用微信 UA + Referer + Range 等 5 种组合**外部重下全部 403**
  → 依赖小程序会话，外部下载这条路走不通
· ★播放器**没有下滑切集**，必须点「选集」逐集选

═══ 因此方案只能是「边播边存」═══
把流经代理的响应体直接落盘：不需要 cookie、不需要签名、不怕过期。

覆盖：
D1  完整 200 响应 → 直接落盘
D2  ★206 分片按 content-range 偏移写入，凑满 total 后改名
D3  分片未凑满时保留 .part（不产生残缺 .mp4）
D4  同一集重复请求不重复存（幂等）
D5  只处理 .mp4，不碰其它资源
D6  403/404 不写文件
D7  落盘目录是 download_dir/小程序短剧
D8  日志提示带文件名和大小
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f"  {detail}" if detail else ""))


from sphdl import config
from sphdl.addon import SphAddon


class FakeResp:
    def __init__(self, status, body=b"", cr=None):
        self.status_code = status
        self.content = body
        self.headers = {}
        if cr:
            self.headers["content-range"] = cr


class FakeReq:
    def __init__(self, path):
        self.path = path
        self.host = "33wozf.vodpreview.wxamedia.com"


class FakeFlow:
    def __init__(self, status, body=b"", cr=None,
                 path="/vod/abcdef0123456789abcdef/f0.mp4?t=1&us=2"):
        self.response = FakeResp(status, body, cr)
        self.request = FakeReq(path)


class Host:
    """只提供 _save_miniapp_media 需要的字段。"""

    def __init__(self):
        self._miniapp_saved = 0


td = Path(tempfile.mkdtemp(prefix="drama_"))
config.DOWNLOAD_DIR = td
h = Host()
save = lambda flow: SphAddon._save_miniapp_media(h, flow)

print("=" * 74)
print("D1 完整 200 响应直接落盘")
print("=" * 74)
save(FakeFlow(200, b"MP4DATA" * 1000))
files = sorted(td.rglob("*.mp4"))
print(f"  产出: {[f.name for f in files]}")
check("D1a 生成了 .mp4", len(files) == 1, str(len(files)))
check("D1b 大小正确", files and files[0].stat().st_size == 7000,
      str(files[0].stat().st_size) if files else "-")
check("D7 目录是 download_dir/小程序短剧",
      files and "小程序短剧" in str(files[0]), str(files[0].parent.name))

print()
print("=" * 74)
print("D2 206 分片拼装")
print("=" * 74)
td2 = Path(tempfile.mkdtemp(prefix="drama2_"))
config.DOWNLOAD_DIR = td2
h2 = Host()
save2 = lambda flow: SphAddon._save_miniapp_media(h2, flow)
EP = "/vod/111122223333444455556666/f0.mp4?t=1"
# 乱序上传三个分片，验证按偏移写入
for start, payload in ((2000, b"C" * 1000), (0, b"A" * 1000), (1000, b"B" * 1000)):
    save2(FakeFlow(206, payload, f"bytes {start}-{start + 999}/3000", path=EP))
files2 = sorted(td2.rglob("*.mp4"))
parts2 = sorted(td2.rglob("*.part"))
print(f"  产出: {[f.name for f in files2]}  残留分片: {len(parts2)}")
check("D2a 凑满后生成 .mp4", len(files2) == 1, str(len(files2)))
check("D2b 内容按偏移拼对",
      files2 and files2[0].read_bytes() == b"A" * 1000 + b"B" * 1000 + b"C" * 1000,
      files2[0].read_bytes()[:8] if files2 else "-")
check("D2c 无残留 .part", len(parts2) == 0, str(len(parts2)))

print()
print("=" * 74)
print("D3 未凑满保留 .part")
print("=" * 74)
td3 = Path(tempfile.mkdtemp(prefix="drama3_"))
config.DOWNLOAD_DIR = td3
h3 = Host()
save3 = lambda flow: SphAddon._save_miniapp_media(h3, flow)
save3(FakeFlow(206, b"X" * 500, "bytes 0-499/3000", path=EP))
files3 = list(td3.rglob("*.mp4"))
parts3 = list(td3.rglob("*.part"))
print(f"  mp4: {len(files3)}  part: {[p.name for p in parts3]}")
check("D3a ★未凑满不生成 .mp4（避免残缺文件）", len(files3) == 0, str(len(files3)))
check("D3b 保留 .part 供续写", len(parts3) == 1, str(len(parts3)))

print()
print("=" * 74)
print("D4 幂等 / D5 只处理 mp4 / D6 错误码")
print("=" * 74)
before = h2._miniapp_saved
save2(FakeFlow(206, b"D" * 1000, "bytes 0-999/3000", path=EP))
check("D4 ★重复请求不重复保存", h2._miniapp_saved == before,
      f"{before} → {h2._miniapp_saved}")

td5 = Path(tempfile.mkdtemp(prefix="drama5_"))
config.DOWNLOAD_DIR = td5
h5 = Host()
save5 = lambda flow: SphAddon._save_miniapp_media(h5, flow)
save5(FakeFlow(200, b"JPGDATA", path="/vod/aaa/f0.jpg"))
check("D5 非 mp4 不处理", len(list(td5.rglob("*.*"))) == 0,
      str(list(td5.rglob("*.*"))))

save5(FakeFlow(403, b"err", path=EP))
save5(FakeFlow(404, b"", path=EP))
check("D6 403/404 不写文件", len(list(td5.rglob("*.mp4"))) == 0)

print()
print("=" * 74)
print("D8 源码检查：日志与路径解析")
print("=" * 74)
src = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")
i = src.find("def _save_miniapp_media")
j = src.find("def response", i)
seg = src[i:j]
check("D8a 完成日志带文件名和 MB", "小程序短剧已保存" in seg and "1048576" in seg)
check("D8b 从路径解析集号", "[a-f0-9]{20,}" in seg)
check("D8c 用 content-range 解析偏移", "content-range" in seg)
check("D8d 目录名 小程序短剧", "小程序短剧" in seg)
check("D8e 只在 host 是 wxamedia 时调用",
      'host.endswith("wxamedia.com")' in src)

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
