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

# ============================ 列表容量档位（v2.0.17） ============================
# 列表（records.json / 界面清单）容量档位。
# **档位由作者掌控，客户端不可自行配置**：只认两条通道 ——
#   ① 授权码（sphdl/license.py，客户在界面输入）→ 解锁对应档位
#   ② 构建预置（release.py --max-records N）→ 该版本档位写死
# 语义：填满档位即**停止加载后续视频**（不顶掉已有记录）。
#       填满后客户删除部分记录使列表低于上限 → 自动恢复接收新视频
#       （小套餐客户只要勤删记录即可长期使用）。
#
# v2.0.17 定稿：4 档全部为**确定数值**（不再有「不限量」概念）。
#   至尊 = 20000 条（实测：插入 0.12s / 落盘 1.14s / 内存 +18MB /
#   启动加载 0.29s；20000 条为经实测的容量上限，超过后界面排序等操作明显变慢）。
MAX_RECORDS_TIERS = (500, 1000, 2000, 20000)
DEFAULT_MAX_RECORDS = 500           # 未授权版本的默认档位

# 档位名称（v2.0.17 定稿，参考软件行业档位梯度命名）
#   轻量(500) / 标准(1000) / 旗舰(2000) / 至尊(20000)
# 界面、状态栏、标题、弹窗统一走这里，改档位名只改这一处。
MAX_RECORDS_NAMES = {500: "轻量", 1000: "标准",
                     2000: "旗舰", 20000: "至尊"}

# 档位完整展示名（名称 + 容量括号），如「标准（1000 条）」
MAX_RECORDS_LABELS = {
    500: "轻量（500 条）",
    1000: "标准（1000 条）",
    2000: "旗舰（2000 条）",
    20000: "至尊（2 万条）",
}

# 档位容量部分的展示名（用于「满 N 条」等只讲容量的句子）
MAX_RECORDS_QUOTA = {500: "500 条", 1000: "1000 条",
                     2000: "2000 条", 20000: "2 万条"}


def label_of(max_records: int) -> str:
    """档位的展示名（如「标准（1000 条）」）；未知值退回「N 条」。"""
    n = int(max_records)
    return MAX_RECORDS_LABELS.get(n, f"{n} 条")


def name_of(max_records: int) -> str:
    """档位名称（如「标准」）；未知值退回「N 条档」。"""
    n = int(max_records)
    return MAX_RECORDS_NAMES.get(n, f"{n} 条档")


def quota_of(max_records: int) -> str:
    """档位容量展示名（如「1000 条」，至尊为「2 万条」）。"""
    n = int(max_records)
    return MAX_RECORDS_QUOTA.get(n, f"{n} 条")


def _edition_attr(name):
    try:
        from . import _edition  # noqa: WPS433（未注入时按默认处理）
        return getattr(_edition, name, None)
    except Exception:
        return None


def locked_max_records():
    """构建预置的**锁定**档位（release.py --max-records 写 sphdl/_edition.py）。

    返回 None = 未锁档（档位由授权码决定）；返回 int = 档位写死
    （500/1000/2000/20000；v2.0.17 起不再支持 0=不限量），界面隐藏授权入口。
    """
    v = _edition_attr("LOCKED_MAX_RECORDS")
    return None if v is None else int(v)


def build_default_max_records() -> int:
    """未授权时的默认档位（release.py --default-records 可覆盖，默认 500）。"""
    v = _edition_attr("DEFAULT_MAX_RECORDS_OVERRIDE")
    if v is None:
        return DEFAULT_MAX_RECORDS
    try:
        return max(0, int(v))
    except Exception:
        return DEFAULT_MAX_RECORDS



# ============================================================================
# ★★★ 激活上报总开关（作者侧防盗版追溯）★★★
# ============================================================================
# 这是整个上报机制的**唯一总开关**。改这一行即可彻底开关：
#
#   TELEMETRY_ENABLED = False   →  完全关闭（默认）
#        · 不发起任何网络请求
#        · 不采集设备指纹（不调 WMIC / PowerShell，不读注册表）
#        · 无日志、无文件、无界面痕迹
#        · 即使代码里误调了 telemetry.report_activation() 也是空操作
#
#   TELEMETRY_ENABLED = True    →  开启
#        · 授权激活成功后，后台线程上报一次（超时 3 秒，失败静默）
#        · 只传：授权码 + 设备码 + 版本 + 时间戳
#        · 不传：原始硬件 ID、用户名、路径、IP
#        · **绝不影响客户使用**：上报失败只是少一条记录，软件照常用
#
# 上报地址：sphdl/telemetry.py 的 DEFAULT_ENDPOINT
#           （可用环境变量 SPHDL_TELEMETRY_URL 覆盖，便于本地测试）
#
# ⚠️ 开启后请确保使用说明/授权条款中已告知用户"会记录激活设备信息"。
#    使用说明的「隐私说明」一节已写明（2026-10-17 加入）。
#
# 2026-10-17：按作者要求**已开启**（用于识别转卖：同一授权码出现在多台
# 设备即视为可疑）；需要关闭时改回 False 重新打包即可。
TELEMETRY_ENABLED = True
