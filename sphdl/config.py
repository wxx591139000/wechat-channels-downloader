"""全局配置"""
import sys
from pathlib import Path


def _app_home() -> Path:
    """应用主目录（下载目录/日志/配置的根）：
    - 冻结模式（PyInstaller 打包）：exe 所在目录 → 便携布局，
      downloads/ logs/ config.json records.json 全部跟 exe 放一起，
      整个文件夹拷走就能用；
    - 开发模式：sphdl/ 目录（main.py 所在目录）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# 项目根目录（sphdl/ 的上一级；冻结时为 exe 目录）
BASE_DIR = _app_home()

# 默认下载目录
DOWNLOAD_DIR = BASE_DIR / "downloads"

# 系统代理状态快照（引擎接管代理前保存；「恢复网络代理」按钮据此恢复）
PROXY_BACKUP_FILE = BASE_DIR / "proxy_backup.json"

# 代理监听
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8899

# 浏览器 UA（视频号 CDN 需要，否则可能 403）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 微信 web 视频号 JS 注入标记（该文件里含 get media(){ 钩子点）
INJECT_JS_MARKER = "virtual_svg-icons-register.publish"

# JS 回调通道（注入的 JS 把 objectDesc POST 到这里，由本插件拦截）
CALLBACK_HOST = "wxapp.tc.qq.com"
CALLBACK_PATH = "/res-downloader/wechat"

# ④ bridge 钩子（2.7 方案）：往视频号网页 HTML 注入 WeixinJSBridge.invoke 包装器。
#    适用场景：PC 微信内置视频号（webview 页面 HTML 走网络，可注入；
#    objectDesc 经原生 bridge IPC 返回，API 直嗅看不见，必须靠 JS 钩子截获）。
#    注入的 JS 把每次 invoke 的参数+回调结果 POST 回代理本身，
#    由本插件 request() 钩子截获解析。
BRIDGE_HOOK_HOSTS = ("channels.weixin.qq.com",)
BRIDGE_CALLBACK_PATH = "/__sphdl/bridge"

# ⚡ 批量拉取（v2.0.8）：页面自动滚动驱动的心跳/完成回传路径
BATCH_CALLBACK_PATH = "/__sphdl/batch"

# 嗅探范围：只关注这些域名的 JSON 响应
SNIFF_HOST_SUFFIXES = ("qq.com",)
