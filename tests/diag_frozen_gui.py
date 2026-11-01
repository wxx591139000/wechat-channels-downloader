"""在打包后的 _internal 环境中直接运行 GUI，抓真实异常。

═══ 背景 ═══
v2.9.7 exe 双击后进程立刻退出，日志只写了 1 行，
没有任何 traceback。说明 GUI 初始化异常被吞了。

PyInstaller 打包后 stderr 可能不可见，所以这里直接
用打包环境的 _internal 作为 sys.path，import sphdl.gui
并手动执行，把异常完整打出来。

用法：
  python tests/diag_frozen_gui.py <打包目录>
"""
import os
import sys
import traceback
from pathlib import Path

DIST = Path(sys.argv[1] if len(sys.argv) > 1 else
            r"E:/myHermesWorkSpace/wechat-channels-downloader/sphdl/dist/v2.9.7/微信视频号下载器")
INTERNAL = DIST / "_internal"

if not INTERNAL.exists():
    print(f"❌ 找不到 {INTERNAL}")
    sys.exit(1)

# 用打包的 _internal 作为模块搜索路径
sys.path.insert(0, str(INTERNAL))
os.chdir(DIST)

print("=" * 74)
print("在打包环境中直接初始化 GUI，抓异常")
print("=" * 74)
print(f"_internal: {INTERNAL}")

# 让 config.BASE_DIR 指向打包目录
os.environ["SPHDL_BASE_DIR"] = str(DIST)

try:
    import tkinter as tk
    print(f"✅ tkinter 可导入，TkVersion={tk.TkVersion}")
except Exception as e:
    print(f"❌ tkinter 导入失败: {e}")
    sys.exit(1)

try:
    from sphdl import gui as G
    print(f"✅ sphdl.gui 导入成功")
except Exception:
    print("❌ sphdl.gui 导入失败：")
    traceback.print_exc()
    sys.exit(1)

# 直接构造 App，看在哪一步炸
try:
    print("\n尝试 App() …")
    app = G.App()
    print("✅ App() 构造成功")
    print("\n尝试 run()（3 秒后自动关闭）…")
    app.root.after(3000, app.root.destroy)
    app.run()
    print("✅ run() 正常返回")
except Exception:
    print("\n❌ 异常：")
    traceback.print_exc()
    sys.exit(1)
