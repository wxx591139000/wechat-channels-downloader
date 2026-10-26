"""授权码吊销校验（方案 B，v2.3.0）。

═══ 为什么要这个模块（2026-10-26）═══

客户问：「发码平台的某用户授权码作废后，用户当前在下载的视频任务是
       立刻停止显示授权码失效？还是可以继续下载？」

实测结论：**原来作废对客户端完全无效**。原因：

  · `license.py` 纯本地 HMAC 校验，`verify_code()` 从不联网
  · 授权码里只有 tier/days/maxdev/flags/prod，**没有"作废"这个字段**
  · 服务器「作废」只是往 licenses.jsonl 追加事件，**没有下发通道**
  · 下载门禁 `_has_download_license()` 只读本地配置

所以作废后：正在下的继续下、新点下载照样能下、界面一个提示都没有。

═══ 方案 B：云端吊销名单 ═══

```
服务器 /api/revoked  →  [{code, reason}, ...]
              ↑
客户端 启动时 + 每 30 分钟拉一次
              ↓
        命中 → 立即降档 + 停掉正在下载 + 醒目弹窗
```

设计取舍：
  · **工具本身就要联网**（下视频必须联网），所以联网校验不增加使用门槛
  · 但仍保留断网宽限，避免服务器抖动误伤正版客户：
      - 网络失败 → 用本地缓存（≤7 天）
      - 连缓存都没有 → **放行**（不能因为我们的服务器挂了让客户以为码坏了）
  · 缓存文件：`%LOCALAPPDATA%\\sphdl\\revoked_cache.json`

★★ 客户红线（2026-10-26）：「工具跟服务器失联时不能降档」★★

  只有**能证明**作废时才降档：
    · 本次联网成功 且 服务器明确列出该码 → 降档（来源 net）
    · 断网，但 6 小时内有过成功响应、且里面明确列出该码 → 降档（来源 cache）
    · 其它一切情况（404/500/HTML/畸形/超时/无缓存/缓存过期/缓存损坏）
      → **绝不降档**（来源 none）
  另外：服务器恢复该码后，下次校验自动恢复档位（可逆）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

# 与 telemetry 同一个域名，便于统一运维
DEFAULT_ENDPOINT = "https://license.zhuanlu.xyz/api/revoked"
ENDPOINT = os.environ.get("SPHDL_REVOKE_URL", DEFAULT_ENDPOINT)

# ★ v2.3.1（客户红线）：「工具跟服务器失联时不能降档」
#
#   原设计给 7 天缓存宽限（断网时用缓存里的作废记录继续降档）。
#   客户明确要求失联不得降档 → 收紧为「当天有效」：
#     · 缓存只有和服务器成功通信时才会刷新（每次成功都重写）
#     · 失联时用缓存，但有 CACHE_TTL 上限
#     · 超过 TTL → 视为「无法证明作废」→ 放行
#
#   为什么不是 0：客户机器时钟/网络抖动时，若严格要求"实时"，
#   会导致刚被作废的码在断网瞬间重新可用，白做功能。
#   取 6 小时是个平衡：当天内断网仍认已知事实，隔夜就放行。
CACHE_TTL = 6 * 3600           # 缓存只在 6 小时内有效（失联宽限）
TIMEOUT = 8


def _data_home() -> Path:
    """数据目录：优先用户级（换版本不丢），退回程序目录。"""
    la = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if la:
        p = Path(la) / "sphdl"
    else:
        p = Path(__file__).resolve().parent.parent
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def cache_path() -> Path:
    return _data_home() / "revoked_cache.json"


def _load_cache() -> dict:
    try:
        d = json.loads(cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_cache(codes: dict, verified: bool = True):
    """写缓存。verified=True 表示这份数据来自一次**成功的服务器响应**。

    ★ 只有 verified=True 的缓存才会在断网时被采信。
      手动写/损坏/半截数据一律视为不可信 → 失联时放行。
    """
    try:
        cache_path().write_text(
            json.dumps({"ts": int(time.time()),
                        "verified": bool(verified),
                        "codes": codes},
                       ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def _get_json(codes: list[str], endpoint: str = "",
              timeout: float = TIMEOUT):
    """GET /api/revoked?codes=a,b,c 并返回 JSON。失败返回 None。

    ★ 为什么用 GET 不用 POST（2026-10-26 实测）：
      服务器 `do_POST` 在路由分发前有 `_sess_ok()` 会话校验，
      客户端没有后台会话 → POST 拿到的是登录页 HTML（HTTP 200 但不是 JSON）。
      `do_GET` 里 `/api/revoked` 放在会话校验**之前**，GET 正常返回 JSON。
      另外 GET + 查询参数也便于将来加 CDN 缓存。
    """
    url = endpoint or ENDPOINT
    if codes:
        q = urllib.parse.urlencode({"codes": ",".join(codes[:200])})
        url = url + ("&" if "?" in url else "?") + q
    req = urllib.request.Request(
        url, headers={"User-Agent": "sphdl-revoke/1.0",
                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace").strip()
        if not txt.startswith("{") and not txt.startswith("["):
            return None                      # 拿到 HTML（登录页）→ 视为失败
        return json.loads(txt)
    except Exception:
        return None


def fetch_revoked(codes: list[str], endpoint: str = "",
                  timeout: float = TIMEOUT) -> tuple[dict, str]:
    """拉吊销名单并筛出我们关心的码。

    返回 (命中字典 {code: reason}, 来源)：
      来源 = "net"   联网成功
           = "cache" 网络失败，用本地缓存
           = "none"  网络失败且无缓存（调用方应放行）
    """
    codes = [c for c in (codes or []) if c]
    resp = _get_json(codes, endpoint=endpoint, timeout=timeout)

    # 只有「ok:true + revoked 是列表」才算一次**可信的成功响应**
    if (isinstance(resp, dict) and resp.get("ok")
            and isinstance(resp.get("revoked"), list)):
        full = {}
        for it in resp.get("revoked") or []:
            try:
                if isinstance(it, dict):
                    full[str(it.get("code"))] = str(it.get("reason") or "已作废")
            except Exception:
                continue
        # 服务器给全量名单 → 缓存全量（下次短暂断网也能用，限 TTL）
        _save_cache(full, verified=True)
        hit = {c: full[c] for c in codes if c in full}
        return hit, "net"

    # ═══ 联网失败 → 用缓存（客户红线：失联不得凭空降档）═══
    #
    # 只认「服务器成功确认过」的记录，且报文必须完整：
    #   · cache["verified"] 必须是 True（表示这份缓存来自一次成功响应）
    #   · 缓存未过期（≤ CACHE_TTL）
    #   · 只命中缓存里**明确列在 revoked 中**的码
    #     绝不能因为"缓存里没有这个码"就推断它作废或正常
    cache = _load_cache()
    ts = int(cache.get("ts") or 0)
    cached = cache.get("codes")
    verified = bool(cache.get("verified"))
    age = (time.time() - ts) if ts else -1

    if (not verified) or (not isinstance(cached, dict)) or age < 0             or age > CACHE_TTL:
        # 没有可信证据 → 放行（绝不降档）
        return {}, "none"

    hit = {c: str(cached[c]) for c in codes if c in cached}
    return hit, "cache"


def cache_age() -> float:
    """缓存多久之前更新的（秒）。没有缓存返回 -1。"""
    ts = int(_load_cache().get("ts") or 0)
    return (time.time() - ts) if ts else -1


def is_revoked(code: str, endpoint: str = "") -> tuple[bool, str, str]:
    """单个码是否被吊销。返回 (是否吊销, 原因, 来源)。"""
    if not code:
        return False, "", "none"
    hit, src = fetch_revoked([code], endpoint=endpoint)
    if code in hit:
        return True, hit[code], src
    return False, "", src
