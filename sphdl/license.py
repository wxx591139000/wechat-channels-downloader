"""授权码（license）—— 列表上限档位的唯一解锁入口（v2.0.17）。

设计目标
  · 档位由作者掌控，**客户不能自己配置**（界面无档位下拉，只有「输入授权码」）
  · 作者用 tools/license_tool.py 生成授权码：一个码解锁一个档位
  · 纯离线校验：不联网、不需要激活服务器

码格式（24 字符，便于微信/短信传抄）
    SPHDL-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
    15 字节 = 5 字节载荷 + 10 字节 HMAC-SHA256 截断
    载荷: ver(1) | tier(1) | days//8(1) | rand(2)
    tier 编码: 1=500, 2=1000, 3=2000, 4=20000（旗舰版；v2.0.17 起不再有「不限量」）
    字母表: 去掉 I/L/O/U 的 32 字符（避免与 1/0 混淆，解码时容忍 O→0、I/L→1）

密钥
  sphdl/_lic_key.py —— **不入库**：release.py 构建时从仓库根的 .license_secret
  注入（同样不入库）；作者本地开发用 tools/license_tool.py init 生成。
  文件缺失时 verify_code() 一律返回 None：即使源码公开，别人也造不出可用码。

安全边界（如实交代）
  本工具是 PyInstaller 打包的 Python 字节码，反编译后可以直接改档位判定；
  授权码能挡住「改配置文件 / 猜码 / 抄别人的码 / 换台机器重装」这类普通绕过，
  挡不住愿意逆向的人。要更强需把档位判定下沉到原生层（当前不划算）。
"""
import hashlib
import hmac
import time
from typing import Optional

# 档位 ↔ 码内编码
#   v2.0.17 定稿：4 档全部确定数值，不再有「不限量」概念
#     1=轻量版 500 / 2=标准版 1000 / 3=专业版 2000 / 4=旗舰版 20000
TIER_TO_CODE = {500: 1, 1000: 2, 2000: 3, 20000: 4}
CODE_TO_TIER = {v: k for k, v in TIER_TO_CODE.items()}

# 档位展示名（与 config.MAX_RECORDS_LABELS 一致，单一事实源在 config）
TIER_LABELS = {500: "轻量版（500 条）", 1000: "标准版（1000 条）",
               2000: "专业版（2000 条）", 20000: "旗舰版（2 万条）"}

CODE_PREFIX = "SPHDL"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"      # 去掉 I L O U
_PAYLOAD_LEN = 5
_MAC_LEN = 10
_VERSION = 1
_MSG_PREFIX = b"SPHDLv1"
_OBF_MASK = b"sphdl-lic-v1"      # 与 tools/license_tool.py 保持一致


def label_of_tier(tier: int) -> str:
    """档位展示名（轻量版 / 标准版 / 专业版 / 旗舰版）。"""
    return TIER_LABELS.get(int(tier), f"{int(tier)} 条")


# ---------------------------------------------------------------- 密钥
def _unobfuscate(blob: bytes) -> bytes:
    return bytes(b ^ _OBF_MASK[i % len(_OBF_MASK)] for i, b in enumerate(blob))


def obfuscate(secret: bytes) -> bytes:
    """供 tools/license_tool.py / release.py 生成 _lic_key.py 使用。"""
    return _unobfuscate(secret)      # 异或自反


_secret_cache = None


def get_secret() -> Optional[bytes]:
    """取内置校验密钥；未注入（开发/源码泄漏场景）→ None。"""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache or None
    try:
        from . import _lic_key                     # 构建期注入，不入库
        _secret_cache = _unobfuscate(_lic_key.K) or b""
    except Exception:
        _secret_cache = b""
    return _secret_cache or None


def license_available() -> bool:
    """当前程序是否内置了授权密钥（否则任何码都无效）。"""
    return get_secret() is not None


# ---------------------------------------------------------------- base32
def b32encode(data: bytes) -> str:
    bits = "".join(f"{b:08b}" for b in data)
    bits += "0" * ((5 - len(bits) % 5) % 5)
    return "".join(ALPHABET[int(bits[i:i + 5], 2)] for i in range(0, len(bits), 5))


def b32decode(text: str) -> Optional[bytes]:
    t = (text or "").strip().upper().replace("-", "").replace(" ", "")
    t = t.replace("O", "0").replace("I", "1").replace("L", "1").replace("U", "V")
    for ch in t:
        if ch not in ALPHABET:
            return None
    if not t:
        return None
    bits = "".join(f"{ALPHABET.index(c):05b}" for c in t)
    nbytes = len(bits) // 8
    out = bytearray()
    for i in range(nbytes):
        out.append(int(bits[i * 8:(i + 1) * 8], 2))
    return bytes(out)


def _mac(payload: bytes) -> bytes:
    secret = get_secret()
    if not secret:
        return b"\x00" * _MAC_LEN
    return hmac.new(secret, _MSG_PREFIX + payload, hashlib.sha256).digest()[:_MAC_LEN]


def _group(code: str) -> str:
    body = code[len(CODE_PREFIX) + 1:] if code.startswith(CODE_PREFIX) else code
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


# ---------------------------------------------------------------- 生成（作者侧）
def make_code(tier: int, days: int = 0, rand: bytes = b"") -> str:
    """生成授权码（**仅作者侧使用**，依赖内置密钥；客户拿不到密钥）。

    tier: 0/500/1000/2000；days: 有效天数（0=永久，按 8 天粒度取整）；
    rand: 2 字节唯一性盐（默认用系统随机）。
    """
    if tier not in TIER_TO_CODE:
        raise ValueError(f"不支持的档位: {tier}（可选 {sorted(TIER_TO_CODE)}）")
    if not get_secret():
        raise RuntimeError("未注入授权密钥（sphdl/_lic_key.py 缺失）→ 无法生成授权码")
    import os
    if len(rand) < 2:
        rand = os.urandom(2)
    days_step = min(255, max(0, int(days) // 8))     # 粒度 8 天，最多 2040 天
    payload = bytes([_VERSION, TIER_TO_CODE[tier], days_step]) + rand[:2]
    blob = payload + _mac(payload)
    return f"{CODE_PREFIX}-{_group(f'{CODE_PREFIX}-' + b32encode(blob))}"


# ---------------------------------------------------------------- 校验（客户端）
def verify_code(code: str) -> Optional[dict]:
    """校验授权码。返回 dict 或 None（格式错/签名错/档位非法）。

    返回: {"tier": 1000, "days": 0, "code": 规范化后的码, "ok": True}
      days = 0 → 永久；>0 → 自首次激活起算的有效天数（由调用方记录激活时间）
    """
    if not code:
        return None
    t = code.strip()
    if t.upper().startswith(CODE_PREFIX):
        t = t[len(CODE_PREFIX) + 1:]
    blob = b32decode(t)
    if not blob or len(blob) < _PAYLOAD_LEN + _MAC_LEN:
        return None
    payload, mac = blob[:_PAYLOAD_LEN], blob[_PAYLOAD_LEN:_PAYLOAD_LEN + _MAC_LEN]
    if not hmac.compare_digest(mac, _mac(payload)):
        return None
    if payload[0] != _VERSION:
        return None
    tier_code = payload[1]
    if tier_code not in CODE_TO_TIER:
        return None
    tier = CODE_TO_TIER[tier_code]
    days = payload[2] * 8
    return {"tier": tier, "days": days, "ok": True,
            "code": f"{CODE_PREFIX}-{_group(f'{CODE_PREFIX}-' + b32encode(blob))}"}


def license_expired(days: int, activated_at: float) -> bool:
    """按「自首次激活起算」判到期。days=0 表示永久。"""
    if not days:
        return False
    if not activated_at:
        return False
    return time.time() - float(activated_at) > days * 86400


def days_left(days: int, activated_at: float) -> Optional[int]:
    if not days or not activated_at:
        return None
    left = int(days - (time.time() - float(activated_at)) / 86400)
    return max(0, left)


def selftest() -> list:
    """自检：往返、篡改、档位、无效字符。返回 [(名称, 是否通过, 说明)]。"""
    out = []
    if not license_available():
        out.append(("密钥可用", False, "未注入 _lic_key（运行 tools/license_tool.py init）"))
        return out
    out.append(("密钥可用", True, ""))
    for tier in (500, 1000, 2000, 20000):
        code = make_code(tier, days=0)
        info = verify_code(code)
        out.append((f"往返 档位 {label_of_tier(tier)}",
                    bool(info) and info["tier"] == tier, f"{code} → {info}"))
    code = make_code(1000, days=365)
    info = verify_code(code)
    out.append(("有效期解析", bool(info) and info["days"] == 360, f"{info}"))
    # 篡改档位字符 → 必须失败
    bad = code[:-1] + ("A" if code[-1] != "A" else "B")
    out.append(("篡改末位被拒", verify_code(bad) is None, bad))
    body = code.replace("-", "")
    tampered = "SPHDL-" + "1" + body[len(CODE_PREFIX) + 1:]
    out.append(("篡改载荷被拒", verify_code(tampered) is None, tampered))
    out.append(("乱码被拒", verify_code("SPHDL-！！！") is None, ""))
    out.append(("空码被拒", verify_code("") is None, ""))
    out.append(("小写/含空格可解析", verify_code(code.lower().replace("-", " ")) is not None, ""))
    out.append(("混淆自反", obfuscate(obfuscate(b"\x01\x02")) == b"\x01\x02", ""))
    return out


if __name__ == "__main__":
    ok = True
    for name, passed, detail in selftest():
        print(f"{'[PASS]' if passed else '[FAIL]'} {name} {detail}")
        ok = ok and passed
    raise SystemExit(0 if ok else 1)
