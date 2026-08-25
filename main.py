#!/usr/bin/env python3
"""
微信视频号下载器 - 入口
========================
两种用法：

A. 嗅探模式（默认）：启动本地代理 + 自动设置系统代理，
   打开微信在视频号里播放视频，程序自动 下载 → 解密 → 保存。

B. 手动模式：已经有 object_desc JSON（微信 API 响应 / TikHub API 响应 /
   抓包结果）时，直接解析并下载解密，不需要代理：

       python main.py --paste response.json
       python main.py --paste-text '{"data":{"object_desc":{"media":[...]}}}'

参数：
  -p/--port        代理端口（默认 8899）
  -d/--dir         下载目录（默认 项目内 downloads/）
  --no-auto-proxy  不自动设置系统代理（手动设置）
  -v               调试输出
"""
import argparse
import atexit
import os
import sys
from pathlib import Path

# 冻结的无控制台 exe（--noconsole）里 sys.stdout/stderr 可能为 None，
# 或 PyInstaller 给的流是 GBK 编码（打 emoji/中文会 UnicodeEncodeError 崩溃）
# → 统一处理：能 reconfigure 就转 utf-8/replace，是 None 就落到 devnull。
# （日志仍由 logger 落盘到 logs/sphdl.log，不受影响）
if getattr(sys, "frozen", False):
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name)
        if _stream is None:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
        else:
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from sphdl import __version__, config
from sphdl.logger import logger
from sphdl.parser import load_and_extract
from sphdl.pipeline import Pipeline


def banner():
    print("=" * 62)
    print(f"  🎬 微信视频号下载器  sphdl v{__version__}")
    print("  嗅探 → 下载 → Isaac64 解密 → 保存")
    print("=" * 62)


def run_paste(args):
    """手动模式：解析 JSON → 下载 → 解密"""
    if args.paste_text:
        text = args.paste_text
    elif args.paste:
        p = Path(args.paste)
        if not p.exists():
            logger.error(f"文件不存在: {p}")
            sys.exit(1)
        text = p.read_text(encoding="utf-8")
    else:
        sys.exit("请指定 --paste 文件 或 --paste-text 文本")

    vd = load_and_extract(text)
    if not vd:
        logger.error("未能从 JSON 中解析出 media 信息（需要 media[].url + decode_key/decodeKey）")
        sys.exit(1)
    logger.info(f"解析成功: {vd.description or '(无描述)'} | 大小 {vd.size / 1024 / 1024:.1f} MB | key={vd.decode_key}")

    pipe = Pipeline(args.dir)
    pipe.submit(vd)
    pipe.drain()
    logger.ok("手动模式处理完毕")


def run_selftest(args):
    """自检：在测试端口启动完整嗅探引擎（mitmproxy + 插件）数秒，
    验证依赖/证书/端口都正常后退出。冒烟测试与用户自检用。
    """
    import asyncio
    import socket

    port = 18899  # 固定测试端口，避开正式 8899

    async def _amain():
        from mitmproxy import addons, options
        from mitmproxy.master import Master
        from sphdl.addon import SphAddon

        opts = options.Options(listen_host=config.PROXY_HOST,
                               listen_port=port, ssl_insecure=True)
        master = Master(opts)
        master.addons.add(*addons.default_addons())
        master.addons.add(SphAddon(Pipeline(args.dir)))
        task = asyncio.create_task(master.run())
        await asyncio.sleep(2)  # 等监听起来
        # 验证端口真的在听
        with socket.create_connection((config.PROXY_HOST, port), timeout=3):
            pass
        logger.ok(f"自检通过：引擎在 127.0.0.1:{port} 正常监听，依赖/证书 OK")
        master.shutdown()
        await task

    try:
        asyncio.run(asyncio.wait_for(_amain(), timeout=30))
    except Exception as e:
        logger.error(f"自检失败: {e}")
        sys.exit(1)


def run_sniff(args):
    """嗅探模式：mitmproxy 代理 + 系统代理 + 微信流量捕获"""
    import asyncio

    from mitmproxy import options
    from mitmproxy.master import Master

    from sphdl.addon import SphAddon
    from sphdl.proxy_manager import ProxyManager, get_mitmproxy_certs

    pipe = Pipeline(args.dir)
    addon = SphAddon(pipe)

    opts = options.Options(
        listen_host=config.PROXY_HOST,
        listen_port=args.port,
        ssl_insecure=True,
    )

    pm = ProxyManager(config.PROXY_HOST, args.port)
    if args.no_auto_proxy:
        logger.warn(f"已指定 --no-auto-proxy：请手动将系统代理设为 {config.PROXY_HOST}:{args.port}")
    else:
        if pm.setup():
            atexit.register(pm.cleanup)
            logger.ok(f"系统代理已设为 {config.PROXY_HOST}:{args.port}（退出时自动恢复）")
        else:
            logger.error("自动设置系统代理失败，可加 --no-auto-proxy 后手动设置")

    pem, p12 = get_mitmproxy_certs()
    if not (pem.exists() or p12.exists()):
        logger.warn("首次运行：证书将生成在 ~/.mitmproxy/ 目录")
        logger.warn("启动后请按 README『证书安装』一节安装 CA 证书，否则 HTTPS 解密会失败")

    print()
    logger.info("监听中... 请打开微信，进入 视频号 播放任意视频")
    logger.info("（同时启用：JS 回调嗅探 + API 直嗅，任一命中即开始下载）")
    logger.info("按 Ctrl+C 退出，系统代理会自动恢复")
    print()

    async def _amain():
        from mitmproxy import addons
        master = Master(opts)
        master.addons.add(*addons.default_addons())
        master.addons.add(addon)
        try:
            await master.run()
        finally:
            master.shutdown()

    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
    finally:
        if not args.no_auto_proxy:
            pm.cleanup()
            logger.ok("系统代理已恢复")


def main():
    ap = argparse.ArgumentParser(
        description="微信视频号下载器（嗅探 + 下载 + Isaac64 解密）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("参数：")[0],
    )
    ap.add_argument("-p", "--port", type=int, default=config.PROXY_PORT,
                    help=f"代理端口（默认 {config.PROXY_PORT}）")
    ap.add_argument("-d", "--dir", default=str(config.DOWNLOAD_DIR),
                    help="下载目录（默认项目内 downloads/）")
    ap.add_argument("--no-auto-proxy", action="store_true",
                    help="不自动设置系统代理（手动设置）")
    ap.add_argument("--paste", metavar="JSON_FILE",
                    help="手动模式：传入 API 响应 / object_desc JSON 文件")
    ap.add_argument("--paste-text", metavar="JSON",
                    help="手动模式：直接传入 JSON 文本")
    ap.add_argument("--gui", action="store_true",
                    help="图形界面模式（推荐，双击 run.bat 即进入此模式）")
    ap.add_argument("--selftest", action="store_true",
                    help="自检：启动嗅探引擎数秒验证依赖/证书后退出")
    ap.add_argument("-v", "--verbose", action="store_true", help="调试输出")
    args = ap.parse_args()

    # 未捕获异常落盘（含 tkinter 主线程与工作线程），便于排查“进程消失”类问题
    import threading
    import traceback

    def _excepthook(exc_type, exc, tb):
        logger.error("未捕获异常(主线程): " + "".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_excepthook(args):
        logger.error(
            f"未捕获异常(线程 {args.thread.name}): "
            + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip()
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    if args.verbose:
        logger.debug_enabled = True

    if args.gui:
        logger.debug_enabled = True  # GUI 模式：全流量日志（诊断用，后续可改为开关）
        # 必须走 gui.main()（内含 Windows 单实例互斥检查），
        # 不能直接 App().run() —— 那样会绕过互斥导致多实例抢端口/抢代理。
        from sphdl import gui as sphdl_gui
        sphdl_gui.main()
        return

    banner()
    if args.selftest:
        run_selftest(args)
    elif args.paste or args.paste_text:
        run_paste(args)
    else:
        run_sniff(args)


if __name__ == "__main__":
    main()
