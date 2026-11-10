#!/usr/bin/env python3
"""真课全量下载验证（v3.4.0 小鹅通）。

跑一节真实公开课：解析 → 并发下分片 → AES 解密 → 拼接 → remux。
输出耗时、大小、ffprobe 结果，作为「能不能下」的硬证据。

用法：python tests/dl_xiaoe_real.py [输出目录]
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sphdl import xiaoe  # noqa: E402

URL = ("https://appkxt2fvhl1688.h5.xiaoeknow.com/p/course/video/"
       "v_64b4b3cce4b007b201a6d98f?product_id=p_62d7b05de4b00a4f372892372")


def probe(path: str) -> str:
    exe = xiaoe.find_ffmpeg()
    if not exe:
        return "(无 ffmpeg，跳过探测)"
    probe_exe = str(Path(exe).with_name("ffprobe.exe"))
    if not Path(probe_exe).exists():
        probe_exe = "ffprobe"
    try:
        p = subprocess.run(
            [probe_exe, "-v", "error", "-show_entries",
             "stream=codec_name,width,height:format=duration,size,bit_rate",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=60)
        return p.stdout.strip() or p.stderr.strip()[:200]
    except Exception as e:                       # noqa: BLE001
        return f"探测失败：{e}"


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else
               Path.home() / "Downloads" / "xiaoe_test")
    links = xiaoe.extract_links(URL)
    t = xiaoe.parse_target(links[0])
    print(f"[1] 解析 {t['resource_id']}")
    res = xiaoe.fetch_video(t)
    if not res.get("ok"):
        print("解析失败：", res.get("error"))
        return 2
    v = res["video"]
    print(f"    标题={v.title} 时长={xiaoe.fmt_dur(v.duration)} "
          f"清晰度={v.quality}")

    t0 = time.time()
    last = [0.0]

    def _prog(done, total, nbytes):
        now = time.time()
        if now - last[0] < 5:
            return
        last[0] = now
        pct = done * 100 // max(1, total)
        speed = nbytes / 1024 / 1024 / max(0.1, now - t0)
        print(f"    {done}/{total} 片 ({pct}%)  {xiaoe.fmt_size(nbytes)}  "
              f"{speed:.2f} MB/s", flush=True)

    print(f"[2] 下载 → {out}")
    r = xiaoe.download_video(v, out, on_progress=_prog,
                             on_log=lambda lv, m: print(f"    [{lv}] {m}"))
    dt = time.time() - t0
    if not r.get("ok"):
        print(f"[FAIL] {r.get('error')}  用时 {dt:.1f}s")
        return 1

    p = Path(r["path"])
    print(f"\n[3] 完成：{p.name}")
    print(f"    大小 {p.stat().st_size:,} 字节（{xiaoe.fmt_size(p.stat().st_size)}）")
    print(f"    用时 {dt:.1f}s（{dt/60:.1f} 分钟）  "
          f"平均 {p.stat().st_size/1024/1024/max(0.1,dt):.2f} MB/s")
    print(f"    TS 兜底：{r.get('ts_only')}  备注：{r.get('note') or '—'}")
    print("\n[4] ffprobe：")
    for ln in probe(str(p)).splitlines():
        print("    " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
