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
        from sphdl import netfix                    # ★ v2.2.4
        from sphdl.addon import SphAddon

        # ★ v2.2.4：确认 netfix 模块被打进包、且逻辑可用
        #   （打包后 .py 会被编进 exe，这里实际调用一遍才算真的验证到）
        _v6 = netfix.ipv6_available()
        _patched = netfix.install_prefer_ipv4()
        logger.ok(f"网络修正：IPv6 可用={_v6}  已启用 IPv4 优先={_patched}")

        opts = options.Options(listen_host=config.PROXY_HOST,
                               listen_port=port, ssl_insecure=True)
        master = Master(opts)
        master.addons.add(*addons.default_addons())
        _addon = SphAddon(Pipeline(args.dir))
        master.addons.add(_addon)
        # ★ 确认「只拦微信其余透传」的白名单已加载
        logger.ok(f"流量策略：拦截 {len(_addon._intercept_re)} 类微信域名，"
                  f"其余透传={'开' if _addon.passthrough_others else '关'}")
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


def run_selftest_douyin(args):
    """抖音模块自检（v2.4.1）。

    ★ 这个自检存在的理由：PyInstaller 打包后 playwright 经常因为
      driver 目录没被完整收集而报「Executable doesn't exist」。
      打包机上测试通过 ≠ 客户机器能用，所以必须有这条自检，
      而且要能在**装好的 exe 里**跑。
    """
    print("=" * 60)
    print("抖音模块自检")
    print("=" * 60)
    ok_all = True

    # ① 模块导入
    try:
        from sphdl import douyin as D
        print("  ① 导入 sphdl.douyin           ✅")
    except Exception as e:                          # noqa: BLE001
        print(f"  ① 导入 sphdl.douyin           ❌ {e}")
        return 1

    # ② playwright 能否 import
    try:
        import playwright
        print(f"  ② import playwright           ✅ "
              f"{getattr(playwright, '__version__', '')}")
    except Exception as e:                          # noqa: BLE001
        print(f"  ② import playwright           ❌ {e}")
        return 1

    # ③ driver 可执行文件是否在
    try:
        from playwright._impl._driver import compute_driver_executable
        drv = compute_driver_executable()
        drv_path = drv[0] if isinstance(drv, (tuple, list)) else str(drv)
        exists = Path(drv_path).exists()
        print(f"  ③ driver 存在                {'✅' if exists else '❌'} "
              f"{drv_path}")
        ok_all &= exists
    except Exception as e:                          # noqa: BLE001
        print(f"  ③ driver 检查                 ❌ {e}")
        ok_all = False

    # ④ 真的启动一次浏览器（用系统 Edge/Chrome）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = None
            which = ""
            for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
                try:
                    b = pw.chromium.launch(headless=True, **kw)
                    which = kw.get("channel") or "bundled-chromium"
                    break
                except Exception:
                    continue
            if b is None:
                print("  ④ 启动浏览器                  ❌ 无可用浏览器")
                return 1
            ver = b.version
            pg = b.new_page()
            pg.set_content("<h1>ok</h1>")
            txt = pg.text_content("h1")
            b.close()
        print(f"  ④ 启动浏览器                  ✅ {which} v{ver} "
              f"(页面读取={txt})")
    except Exception as e:                          # noqa: BLE001
        print(f"  ④ 启动浏览器                  ❌ {type(e).__name__}: {e}")
        return 1

    # ⑤ 配置与目录
    try:
        print(f"  ⑤ profile 目录                ✅ {D.profile_dir()}")
        print(f"  ⑤ 缓存文件                    {D._cache_path()}")
        print(f"  ⑤ 未登录节流                  {D.PACE_ANON} / "
              f"单批 {D.BATCH_ANON}")
    except Exception as e:                          # noqa: BLE001
        print(f"  ⑤ 配置检查                    ❌ {e}")
        ok_all = False

    # ⑥ 链接解析（纯逻辑）
    try:
        got = D.extract_urls("看这个 https://v.douyin.com/abc/ 和 "
                             "https://www.douyin.com/video/123")
        exp = ["https://v.douyin.com/abc/",
               "https://www.douyin.com/video/123"]
        good = got == exp
        print(f"  ⑥ 链接解析                    {'✅' if good else '❌'} {got}")
        ok_all &= good
    except Exception as e:                          # noqa: BLE001
        print(f"  ⑥ 链接解析                    ❌ {e}")
        ok_all = False

    print("=" * 60)
    print("结果:", "✅ 自检通过" if ok_all else "❌ 自检未通过")
    return 0 if ok_all else 1


def run_selftest_license(args):
    """授权码自检（v2.0.17）：验证构建产物里授权密钥已正确注入，
    且 4 档往返生成/校验一致。release.py 构建后自动调用 —— 密钥注入
    失败（漏注入/写坏）会在这里立刻暴露，避免发出去的包"所有授权码都无效"。
    """
    from sphdl import license as lic

    print("授权密钥:", "已注入" if lic.license_available() else "缺失 ❌")
    if not lic.license_available():
        raise SystemExit("❌ 授权密钥缺失：构建未注入 sphdl/_lic_key.py")
    bad = 0
    for name, ok, detail in lic.selftest():
        print(f"{'[PASS]' if ok else '[FAIL]'} {name} {detail}")
        bad += 0 if ok else 1
    # 再真实生成一个码打印出来（作者可用来核对发布包的档位行为）
    demo = lic.make_code(1000, days=0)
    print("示例授权码(1000 档):", demo)
    if lic.verify_code(demo) is None:
        bad += 1
    print("授权自检:", "ALL PASS" if not bad else f"{bad} 项失败")
    raise SystemExit(1 if bad else 0)


def run_selftest_mp(args):
    """公众号视频模块自检（v2.0.19）：验证打包产物里解析逻辑可用。

    默认离线（只测解析逻辑，不联网）；加 --online 则用真实文章端到端验证。
    这个入口既能验证自己的构建，也能让客户自查"功能是否可用"。
    """
    from sphdl import mp_article as mp

    bad = 0
    for name, ok, detail in mp.selftest():
        print(f"{'[PASS]' if ok else '[FAIL]'} {name} {detail}")
        bad += 0 if ok else 1

    if getattr(args, "online", False):
        url = ("https://mp.weixin.qq.com/s/BAvjuF4k6BLWlyCHTY58xA")
        print(f"\n联网验证：{url}")
        try:
            h = mp.fetch_article(url)
            vs = mp.parse_article(h)
            print(f"  文章标题: {mp.article_title(h)}")
            print(f"  解析到 {len(vs)} 个视频")
            for i, v in enumerate(vs, 1):
                print(f"    {i}. {v.vid}  清晰度 {len(v.renditions)} 档")
            ok = len(vs) > 0
            print(f"{'[PASS]' if ok else '[FAIL]'} 联网解析")
            bad += 0 if ok else 1
        except Exception as e:                          # noqa: BLE001
            print(f"[FAIL] 联网验证异常: {type(e).__name__}: {e}")
            bad += 1
    else:
        print("\n（离线自检；加 --online 可联网验证真实文章）")

    print("公众号视频自检:", "ALL PASS" if not bad else f"{bad} 项失败")
    raise SystemExit(1 if bad else 0)


def run_selftest_telemetry(args):
    """激活上报自检（v2.0.19）：确认总开关状态与设备指纹采集。

    ⚠️ 不发起网络请求，只验证本地逻辑；客户可用它确认"程序是否会上报"。
    """
    from sphdl import config as _cfg
    from sphdl import telemetry as tel

    print("总开关 TELEMETRY_ENABLED =", _cfg.TELEMETRY_ENABLED,
          "（False = 完全不采集、不上报）")
    bad = 0
    for name, ok, detail in tel.selftest():
        print(f"{'[PASS]' if ok else '[FAIL]'} {name} {detail}")
        bad += 0 if ok else 1
    print("埋点自检:", "ALL PASS" if not bad else f"{bad} 项失败")
    raise SystemExit(1 if bad else 0)


def run_selftest_threaded(args):
    """线程自检：完全复刻 GUI 的引擎启动路径（后台线程 + 独立事件循环），
    验证冻结环境下后台线程启动引擎不死亡（2026-08-26「启动嗅探失败」
    事故的回归测试入口）。

    ⚠️ 正确模式（v1.2.0/v1.2.1 行为，已反复验证）：mitmproxy 的首次
    导入（含 mitmproxy_rs 原生库加载）必须发生在【引擎线程】内。
    主线程预导入在冻结环境下会让引擎线程进事件循环时 C 级死亡
    （无异常、无 finally、端口永不监听）——2026-08-26 对照实验坐实。
    故本自检刻意【不在主线程导入任何 mitmproxy 模块】。
    """
    import socket
    import threading
    import time as _time

    port = 18899  # 固定测试端口，避开正式 8899

    state = {}

    def _eng():
        import asyncio
        # mitmproxy 在引擎线程内首次导入（关键：原生库在引擎线程上
        # 首次加载是安全路径；主线程不碰）
        from mitmproxy import addons, options
        from mitmproxy.master import Master
        from sphdl.addon import SphAddon

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state["loop"] = loop

        async def _amain():
            opts = options.Options(listen_host=config.PROXY_HOST,
                                   listen_port=port, ssl_insecure=True)
            master = Master(opts)
            master.addons.add(*addons.default_addons())
            master.addons.add(SphAddon(Pipeline(args.dir)))
            state["master"] = master
            await master.run()

        try:
            loop.run_until_complete(_amain())
        except Exception as e:
            state["error"] = e
        finally:
            try:
                loop.close()
            except Exception:
                pass
            state["done"] = True

    th = threading.Thread(target=_eng, daemon=True)
    t0 = _time.time()
    th.start()

    ok = False
    while _time.time() - t0 < 30:
        try:
            with socket.create_connection((config.PROXY_HOST, port),
                                          timeout=0.5):
                ok = True
                break
        except OSError:
            _time.sleep(0.3)
    if ok:
        logger.ok(f"线程自检通过：引擎在后台线程 {_time.time() - t0:.1f}s 内"
                  f"于 127.0.0.1:{port} 正常监听（冻结环境无死亡）")
    else:
        err = state.get("error")
        logger.error("线程自检失败：30s 内端口未监听"
                     + (f" | 引擎异常: {err}" if err
                        else " | 引擎线程无异常（静默挂起/死亡——C 级死锁特征，"
                             "请核对 mitmproxy 导入路径是否保持在引擎线程内）"))
        sys.exit(1)
    # 收尾：从主线程请求引擎退出
    loop, master = state.get("loop"), state.get("master")
    if loop and master:
        try:
            loop.call_soon_threadsafe(master.should_exit.set)
        except Exception:
            pass
    for _ in range(50):
        if state.get("done"):
            break
        _time.sleep(0.1)
    if not state.get("done"):
        logger.warn("引擎线程收尾超时（不影响自检结论）")


def run_sniff(args):
    """嗅探模式：mitmproxy 代理 + 系统代理 + 微信流量捕获"""
    import asyncio

    from mitmproxy import options
    from mitmproxy.master import Master

    from sphdl.addon import SphAddon
    from sphdl.proxy_manager import ProxyManager, get_mitmproxy_certs

    pipe = Pipeline(args.dir)
    addon = SphAddon(pipe)
    # 注入 JS 的 bridge 回传端口跟随 CLI 端口（-p），否则改端口后钩子失效
    addon.set_bridge_port(args.port)

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
    ap.add_argument("--selftest-threaded", action="store_true",
                    help="线程自检：引擎跑在后台线程（完全复刻 GUI 启动路径，"
                         "含引擎线程内首次导入 mitmproxy），验证冻结环境不"
                         "死亡后退出（2026-08-26 启动嗅探失败事故的回归测试）")
    ap.add_argument("--selftest-license", action="store_true",
                    help="授权自检：验证授权密钥已注入 + 4 档往返校验后退出")
    ap.add_argument("--selftest-mp", action="store_true",
                    help="公众号视频自检：验证文章解析逻辑后退出")
    ap.add_argument("--online", action="store_true",
                    help="配合 --selftest-mp：联网用真实文章端到端验证")
    ap.add_argument("--selftest-autopage", action="store_true",
                    help="自动翻页自检：验证滚轮/窗口查找模块完整（v2.7.0）")
    ap.add_argument("--selftest-douyin", action="store_true",
                    help="抖音模块自检：playwright 能否驱动系统浏览器")
    ap.add_argument("--selftest-telemetry", action="store_true",
                    help="埋点自检：显示总开关状态 + 设备指纹采集（不发网络请求）")
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

    # 冻结 exe 双击（不带任何参数）= GUI。
    # 修复 2026-08-25 事故：exe 无参双击原本落入 run_sniff()——
    # 无窗口构建下引擎隐形启动 + 系统代理被接管成 8899，且没有任何界面
    # 可点「停止嗅探」→ 用户/朋友电脑断网且找不到开关（v1.1.1 修复）。
    # 开发模式不受影响：python main.py 无参仍是嗅探（CLI 开发工作流），
    # run.bat 无参时会显式传 --gui。
    if not args.gui and getattr(sys, "frozen", False) and not sys.argv[1:]:
        args.gui = True

    if args.gui:
        # ★ v2.7.5：GUI 模式**默认关闭**全流量日志。
        #   原来强制 True，导致每个 HTTP 请求一行 DIM（实测占日志 76%），
        #   批量拉取时把 Text 控件灌爆 → 界面卡死。
        #   需要诊断时：设置环境变量 SPHDL_DEBUG=1，或建 `开启调试日志.txt`。
        import os as _os
        _dbg = _os.environ.get("SPHDL_DEBUG", "").strip() in ("1", "true", "yes")
        try:
            from pathlib import Path as _P
            if (_P(__file__).resolve().parent / "开启调试日志.txt").exists():
                _dbg = True
        except Exception:
            pass
        logger.debug_enabled = _dbg
        # 必须走 gui.main()（内含 Windows 单实例互斥检查），
        # 不能直接 App().run() —— 那样会绕过互斥导致多实例抢端口/抢代理。
        from sphdl import gui as sphdl_gui
        sphdl_gui.main()
        return

    banner()
    if getattr(args, "selftest_autopage", False):
        from sphdl.autopage import selftest
        return 0 if selftest() else 1
    if getattr(args, "selftest_douyin", False):
        sys.exit(run_selftest_douyin(args))
    if args.selftest:
        run_selftest(args)
    elif args.selftest_mp:
        run_selftest_mp(args)
    elif args.selftest_telemetry:
        run_selftest_telemetry(args)
    elif args.selftest_threaded:
        run_selftest_threaded(args)
    elif args.selftest_license:
        run_selftest_license(args)
    elif args.paste or args.paste_text:
        run_paste(args)
    else:
        run_sniff(args)


if __name__ == "__main__":
    main()
