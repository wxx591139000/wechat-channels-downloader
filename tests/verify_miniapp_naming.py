"""小程序短剧：落盘目录修复 + 按集序命名（v2.8.0）。

═══ 客户反馈（2026-10-30 第三轮）═══
1. 「深挖一下剧集」
2. 「下载的视频标题应该与剧集名称+第几集对齐，方便查阅」

═══ 深挖结果（真实抓包）═══
诊断模式下所有 JSON 响应都落盘了，**里面全是埋点/心跳**：
```
mp.weixin.qq.com     /mp/jsmonitor → {"ret":0,"errmsg":"ok"}
sh.servicewechat.com /ob/sdkmonitor → {"base_resp":{"ret":0}}
game.weixin.qq.com   /reportgamewebruntime → {"errcode":0}
```
**没有任何 title / episode / 播放地址列表**。
透传域名也只有 `wxaintpcos.wxqcloud.qq.com.cn` 等 COS 静态资源。

→ 剧集元数据**没走可拦截的 HTTP**（可能是小程序私有通道/包内 JS）。

因此本版：**先用「播放顺序」命名**（第01集_哈希8位.mp4），
等找到元数据接口再升级为「真剧名_第N集」。

═══ 同时修的严重 bug ═══
v2.7.8 把 `self.addon.out_dir = ...` 写在了 **addon 创建之前**（addon 还是 None），
AttributeError 被 `except: pass` 吞掉 → out_dir 永远 None
→ 客户配置 E:\\视频号下载，文件却全存到「程序目录/downloads」。

覆盖：
N1  ★out_dir 注入位置在 addon 创建**之后**
N2  ★注入失败不再静默（要 push_log 告警）
N3  ★文件名形如 第01集_xxxxxxxx.mp4
N4  同一集重复请求沿用同一序号（不会变成第02集）
N5  .part 分片名与最终名一致
N6  白名单含 qq.com.cn（小程序 COS）
N7  深挖诊断：JSON 落盘函数存在且有上限
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

gui = (ROOT / "sphdl" / "gui.py").read_text(encoding="utf-8")
addon = (ROOT / "sphdl" / "addon.py").read_text(encoding="utf-8")

print("=" * 74)
print("N1-N2 out_dir 注入位置")
print("=" * 74)
i_create = gui.find("self.addon = SphAddon(")
i_inject = gui.find("self.addon.out_dir = self.cfg.download_dir")
print(f"  addon 创建 {i_create} / 注入 {i_inject}")
check("N1a ★注入在 addon 创建之后", 0 < i_create < i_inject)
check("N1b 注入前面有 addon is not None 判断",
      "if self.addon is not None:" in gui[i_inject - 200:i_inject + 50])
check("N2 ★注入失败会告警（不是静默 pass）",
      "小程序短剧目录注入失败" in gui)
# 旧位置不应再有注入
check("N1c 旧位置（pipeline 之后）已无注入",
      gui.count("self.addon.out_dir = self.cfg.download_dir") == 1)


class FakeResp:
    def __init__(self, status, body=b"", cr=None):
        self.status_code = status
        self.content = body
        self.headers = {"content-range": cr} if cr else {}


class FakeFlow:
    def __init__(self, status, body=b"", cr=None, path="/vod/abcdef0123456789abcdef/f0.mp4?t=1"):
        self.response = FakeResp(status, body, cr)
        self.request = type("Q", (), {"path": path, "host": "x.wxamedia.com"})()


class Host:
    def __init__(self, out_dir):
        self._miniapp_saved = 0
        self.out_dir = str(out_dir)
        self.on_miniapp = None


print()
print("=" * 74)
print("N3-N5 命名")
print("=" * 74)
td = Path(tempfile.mkdtemp(prefix="name_"))
config.DOWNLOAD_DIR = td
h = Host(td)
SphAddon._save_miniapp_media(h, FakeFlow(
    200, b"A" * 2000, path="/vod/aaaa1111222233334444/f0.mp4?t=1"))
files = sorted(td.rglob("*.mp4"))
print(f"  第 1 个文件: {[f.name for f in files]}")
check("N3a ★文件名以 第01集_ 开头",
      files and files[0].name.startswith("第01集_"),
      files[0].name if files else "-")
check("N3b 带哈希前 8 位",
      files and "aaaa1111" in files[0].name, files[0].name if files else "-")

SphAddon._save_miniapp_media(h, FakeFlow(
    200, b"B" * 2000, path="/vod/bbbb2222333344445555/f0.mp4?t=1"))
files2 = sorted(td.rglob("*.mp4"))
print(f"  第 2 个文件: {[f.name for f in files2]}")
check("N3c 第 2 集编号递增",
      any(f.name.startswith("第02集_") for f in files2),
      str([f.name for f in files2]))

# 同一集重复（不同 t 参数）不应新增编号
before = len(list(td.rglob("*.mp4")))
SphAddon._save_miniapp_media(h, FakeFlow(
    200, b"A" * 2000, path="/vod/aaaa1111222233334444/f0.mp4?t=999"))
after = len(list(td.rglob("*.mp4")))
print(f"  重复同集: {before} → {after} 个文件")
check("N4 ★同一集不新增文件、不改序号", before == after, f"{before}→{after}")
check("N4b 序号映射已记录", "aaaa1111222233334444" in getattr(h, "_mp_names", {}))

# .part 命名
td3 = Path(tempfile.mkdtemp(prefix="part_"))
h3 = Host(td3)
SphAddon._save_miniapp_media(h3, FakeFlow(
    206, b"X" * 100, "bytes 0-99/5000", path="/vod/cccc3333444455556/f0.mp4?t=1"))
parts = list(td3.rglob("*.part"))
print(f"  分片: {[p.name for p in parts]}")
check("N5 ★.part 名与最终名一致（第01集_ 前缀）",
      parts and parts[0].name.startswith("第01集_"), parts[0].name if parts else "-")

print()
print("=" * 74)
print("N6-N7 白名单与深挖诊断")
print("=" * 74)
check("N6 ★白名单含 qq.com.cn（小程序 COS）",
      'qq\\\\.com\\\\.cn' in addon or "qq.com.cn" in addon)
check("N7a 有 JSON 落盘函数", "def _dump_miniapp_json" in addon)
check("N7b 有每域名条数上限", "200" in addon[addon.find("def _dump_miniapp_json"):][:2000])
# 调用点（不是函数定义）往前找守卫
_call = addon.find("self._dump_miniapp_json(flow)")
_guard = addon.rfind("_diag_on()", max(0, _call - 600), _call)
print(f"  调用点 {_call} / 守卫位置 {_guard}")
check("N7c ★只在诊断开关下触发", _call > 0 and _guard > 0,
      f"call={_call} guard={_guard}")

print()
ok_n = sum(1 for _, p, _ in R if p)
print(f"RESULT: {'ALL PASS' if ok_n == len(R) else 'FAIL'} ({ok_n}/{len(R)})")
for nm, p, _ in R:
    if not p:
        print("  未通过: " + nm)
sys.exit(0 if ok_n == len(R) else 1)
