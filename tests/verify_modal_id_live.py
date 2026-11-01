"""用打包版 exe 验证 modal_id 链接能否解析（真实网络）。

客户原链接：https://www.douyin.com/jingxuan?modal_id=7702018171426868531
之前报「没找到抖音链接」，修复后应能解析。

注意：解析作品**不需要登录**（只有主页抓取需要），所以这个应该能跑通。
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl")
sys.path.insert(0, str(ROOT))

from sphdl.douyin import resolve_many  # noqa: E402

URLS = [
    "https://www.douyin.com/jingxuan?modal_id=7702018171426868531",
]

print("=" * 70)
print("验证 modal_id 链接解析（客户原链接）")
print("=" * 70)
for u in URLS:
    print(f"  输入: {u}")

results = []


def on_item(i, url, v):
    results.append((url, v))


def on_log(lv, msg):
    print(f"  [{lv}] {msg}")


t0 = time.time()
try:
    resolve_many(URLS, on_item=on_item, on_log=on_log)
except Exception as e:
    print(f"  ❌ 异常: {type(e).__name__}: {e}")

dt = time.time() - t0
print()
print(f"耗时 {dt:.1f} 秒，解析出 {len(results)} 条")
for url, v in results:
    if v is None:
        print(f"  ❌ {url} → None")
    elif v.ok:
        print(f"  ✅ 成功！")
        print(f"     标题: {v.title[:60]}")
        print(f"     作者: {v.author}")
        print(f"     时长: {v.duration_text}")
        print(f"     大小: {v.size_text}")
        print(f"     地址: {v.play_url[:80]}...")
    else:
        print(f"  ❌ {url} → 错误: {v.error}")

ok = any(v is not None and v.ok for _, v in results)
print()
print(f"RESULT: {'✅ 通过 —— modal_id 链接能正常解析' if ok else '❌ 失败'}")
sys.exit(0 if ok else 1)
