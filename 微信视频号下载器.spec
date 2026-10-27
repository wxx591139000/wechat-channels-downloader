# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('install-cert.bat', '.'),
         ('诊断-双击我.bat', '.')]
binaries = []
hiddenimports = ['mitmproxy.addons.anticache', 'mitmproxy.addons.anticomp', 'mitmproxy.addons.asgiapp', 'mitmproxy.addons.block', 'mitmproxy.addons.blocklist', 'mitmproxy.addons.browser', 'mitmproxy.addons.clientplayback', 'mitmproxy.addons.command_history', 'mitmproxy.addons.comment', 'mitmproxy.addons.core', 'mitmproxy.addons.cut', 'mitmproxy.addons.disable_h2c', 'mitmproxy.addons.dns_resolver', 'mitmproxy.addons.dumper', 'mitmproxy.addons.errorcheck', 'mitmproxy.addons.eventstore', 'mitmproxy.addons.export', 'mitmproxy.addons.intercept', 'mitmproxy.addons.keepserving', 'mitmproxy.addons.maplocal', 'mitmproxy.addons.mapremote', 'mitmproxy.addons.modifybody', 'mitmproxy.addons.modifyheaders', 'mitmproxy.addons.next_layer', 'mitmproxy.addons.onboarding', 'mitmproxy.addons.onboardingapp', 'mitmproxy.addons.proxyauth', 'mitmproxy.addons.proxyserver', 'mitmproxy.addons.readfile', 'mitmproxy.addons.save', 'mitmproxy.addons.savehar', 'mitmproxy.addons.script', 'mitmproxy.addons.server_side_events', 'mitmproxy.addons.serverplayback', 'mitmproxy.addons.stickyauth', 'mitmproxy.addons.stickycookie', 'mitmproxy.addons.strip_dns_https_records', 'mitmproxy.addons.termlog', 'mitmproxy.addons.tlsconfig', 'mitmproxy.addons.update_alt_svc', 'mitmproxy.addons.upstream_auth', 'mitmproxy.addons.view']
tmp_ret = collect_all('mitmproxy_rs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ★ v2.4.1：抖音批量下载需要 playwright（驱动系统已装的 Edge/Chrome，
#   **不打包 Chromium**，所以只多 playwright 自己的 driver ≈104MB）。
#   用 collect_all 确保 driver/ 目录（含 node 可执行文件）被完整带上，
#   否则打包后运行会报 "Executable doesn't exist"。
try:
    _pw = collect_all('playwright')
    datas += _pw[0]; binaries += _pw[1]; hiddenimports += _pw[2]
except Exception as _e:      # 没装 playwright 时不阻断构建
    print('WARN: collect_all(playwright) 失败:', _e)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='微信视频号下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='微信视频号下载器',
)
