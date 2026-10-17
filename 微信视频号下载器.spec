# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('install-cert.bat', '.'),
         ('诊断-双击我.bat', '.')]
binaries = []
hiddenimports = ['mitmproxy.addons.anticache', 'mitmproxy.addons.anticomp', 'mitmproxy.addons.asgiapp', 'mitmproxy.addons.block', 'mitmproxy.addons.blocklist', 'mitmproxy.addons.browser', 'mitmproxy.addons.clientplayback', 'mitmproxy.addons.command_history', 'mitmproxy.addons.comment', 'mitmproxy.addons.core', 'mitmproxy.addons.cut', 'mitmproxy.addons.disable_h2c', 'mitmproxy.addons.dns_resolver', 'mitmproxy.addons.dumper', 'mitmproxy.addons.errorcheck', 'mitmproxy.addons.eventstore', 'mitmproxy.addons.export', 'mitmproxy.addons.intercept', 'mitmproxy.addons.keepserving', 'mitmproxy.addons.maplocal', 'mitmproxy.addons.mapremote', 'mitmproxy.addons.modifybody', 'mitmproxy.addons.modifyheaders', 'mitmproxy.addons.next_layer', 'mitmproxy.addons.onboarding', 'mitmproxy.addons.onboardingapp', 'mitmproxy.addons.proxyauth', 'mitmproxy.addons.proxyserver', 'mitmproxy.addons.readfile', 'mitmproxy.addons.save', 'mitmproxy.addons.savehar', 'mitmproxy.addons.script', 'mitmproxy.addons.server_side_events', 'mitmproxy.addons.serverplayback', 'mitmproxy.addons.stickyauth', 'mitmproxy.addons.stickycookie', 'mitmproxy.addons.strip_dns_https_records', 'mitmproxy.addons.termlog', 'mitmproxy.addons.tlsconfig', 'mitmproxy.addons.update_alt_svc', 'mitmproxy.addons.upstream_auth', 'mitmproxy.addons.view']
tmp_ret = collect_all('mitmproxy_rs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


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
