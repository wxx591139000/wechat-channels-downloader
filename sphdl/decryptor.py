"""
微信视频号视频解密模块（纯 Python Isaac64 实现，零依赖）

原理：视频号视频只加密前 131072 字节（128KB）：
    明文 = 密文 XOR Isaac64密钥流
密钥流由 API 响应中的 decode_key 作为种子生成。

算法参考: https://github.com/Hanson/WechatSphDecrypt/blob/main/decrypt.go
重要：decode_key 必须与视频 url 来自同一次 API 响应，否则解密结果不可播放。
"""
import struct
from pathlib import Path
from typing import Optional

MASK64 = 0xFFFFFFFFFFFFFFFF
ENC_LEN = 131072  # 只加密前 128KB


def mix(a, b, c, d, e, f, g, h):
    a = (a - e) & MASK64; f ^= (h >> 9) & MASK64; h = (h + a) & MASK64
    b = (b - f) & MASK64; g ^= (a << 9) & MASK64; a = (a + b) & MASK64
    c = (c - g) & MASK64; h ^= (b >> 23) & MASK64; b = (b + c) & MASK64
    d = (d - h) & MASK64; a ^= (c << 15) & MASK64; c = (c + d) & MASK64
    e = (e - a) & MASK64; b ^= (d >> 14) & MASK64; d = (d + e) & MASK64
    f = (f - b) & MASK64; c ^= (e << 20) & MASK64; e = (e + f) & MASK64
    g = (g - c) & MASK64; d ^= (f >> 17) & MASK64; f = (f + g) & MASK64
    h = (h - d) & MASK64; e ^= (g << 14) & MASK64; g = (g + h) & MASK64
    return a, b, c, d, e, f, g, h


class RandCtx64:
    """Isaac64 PRNG 上下文（与微信客户端实现一致）"""

    def __init__(self, enc_key):
        self.RandCnt = 255
        self.Seed = [0] * 256
        self.MM = [0] * 256
        self.AA = 0
        self.BB = 0
        self.CC = 0
        self.rand64_init(enc_key)

    def is_aac_random(self):
        r = self.Seed[self.RandCnt]
        if self.RandCnt == 0:
            self.is_aac64()
            self.RandCnt = 255
        else:
            self.RandCnt -= 1
        return r

    def _process_pass(self, a, b, c, d, e, f, g, h, source_array):
        for i in range(0, 256, 8):
            values = [a, b, c, d, e, f, g, h]
            values = [(v + source_array[i + j]) & MASK64 for j, v in enumerate(values)]
            a, b, c, d, e, f, g, h = mix(*values)
            for j, val in enumerate([a, b, c, d, e, f, g, h]):
                self.MM[i + j] = val
        return a, b, c, d, e, f, g, h

    def rand64_init(self, enc_key):
        golden = 0x9E3779B97F4A7C13
        a = b = c = d = e = f = g = h = golden
        self.Seed[0] = enc_key & MASK64
        for _ in range(4):
            a, b, c, d, e, f, g, h = mix(a, b, c, d, e, f, g, h)
        a, b, c, d, e, f, g, h = self._process_pass(a, b, c, d, e, f, g, h, self.Seed)
        a, b, c, d, e, f, g, h = self._process_pass(a, b, c, d, e, f, g, h, self.MM)
        self.is_aac64()

    def is_aac64(self):
        self.CC = (self.CC + 1) & MASK64
        self.BB = (self.BB + self.CC) & MASK64
        for i in range(256):
            if i % 4 == 0:
                self.AA = (~(self.AA ^ (self.AA << 21))) & MASK64
            elif i % 4 == 1:
                self.AA ^= (self.AA >> 5) & MASK64
            elif i % 4 == 2:
                self.AA ^= (self.AA << 12) & MASK64
            else:
                self.AA ^= (self.AA >> 33) & MASK64
            self.AA = (self.AA + self.MM[(i + 128) % 256]) & MASK64
            x = self.MM[i]
            y = (self.MM[(x >> 3) % 256] + self.AA + self.BB) & MASK64
            self.MM[i] = y
            self.BB = (self.MM[(y >> 11) % 256] + x) & MASK64
            self.Seed[i] = self.BB


def is_encrypted(head: bytes) -> bool:
    """判断文件头是否处于加密状态。正常 MP4 的 bytes[4:8] 应为 b'ftyp'。"""
    if len(head) < 12:
        return False
    return head[4:8] != b"ftyp"


def _xor_head(data: bytearray, key: int) -> None:
    """对前 ENC_LEN 字节做 XOR 解密（原地）"""
    ctx = RandCtx64(key)
    for i in range(0, ENC_LEN, 8):
        rand_number = ctx.is_aac_random()
        temp = struct.pack(">Q", rand_number)
        for j in range(8):
            data[i + j] ^= temp[j]


def decrypt_file(input_path, decode_key, output_path=None) -> Optional[str]:
    """
    解密视频号视频文件（只处理前 128KB，内存友好）。

    Args:
        input_path:  加密文件路径
        decode_key:  解密密钥（字符串或整数）
        output_path: 输出路径；None 表示原地解密

    Returns:
        成功返回输出路径，失败返回 None

    失败时（文件头不是 ftyp）说明 key 与 url 不匹配或文件本未加密。
    """
    try:
        src = Path(input_path)
        dst = Path(output_path) if output_path else src

        n = min(ENC_LEN, src.stat().st_size)
        with open(src, "rb") as f:
            head = bytearray(f.read(n))

        _xor_head(head, int(decode_key))

        if head[4:8] != b"ftyp":
            return None  # 解密验证失败

        if dst.resolve() == src.resolve():
            # 原地写回前 n 字节
            with open(src, "r+b") as f:
                f.write(bytes(head))
        else:
            with open(dst, "wb") as f:
                f.write(bytes(head))
                with open(src, "rb") as g:
                    if n < src.stat().st_size:
                        g.seek(n)
                        while True:
                            chunk = g.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
        return str(dst)
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python decryptor.py <加密文件.mp4> <decode_key> [输出文件.mp4]")
        sys.exit(1)
    out = decrypt_file(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print("解密成功:", out) if out else print("解密失败：key 不匹配或文件未加密")
    sys.exit(0 if out else 2)
