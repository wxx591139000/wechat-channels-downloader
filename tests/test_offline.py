"""
离线验证（无需微信客户端）：
  1. 解析器测试：wx_response.json（snake_case）+ 合成 camelCase objectDesc
  2. 解密器往返测试：加密 → 解密 → 与原文一致
  3. 真实样例验证（如能联网）：下载上游仓库的 wx_encrypted.mp4，
     用 decode_key=2136343393 解密，验证文件头出现 ftyp
运行: python tests/test_offline.py
"""
import shutil
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent        # sphdl/
PROJECT_ROOT = BASE.parent                          # 项目根
sys.path.insert(0, str(BASE))

from sphdl.decryptor import _xor_head, decrypt_file, is_encrypted, ENC_LEN  # noqa: E402
from sphdl.parser import load_and_extract  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}  {detail}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


print("== 1. 解析器测试 ==")
sample = PROJECT_ROOT / "reference" / "channels-decrypt" / "wx_response.json"
vd = load_and_extract(sample.read_text(encoding="utf-8"))
check("wx_response.json 解析",
      vd is not None and vd.decode_key == "2136343393" and vd.url.startswith("https://finder.video.qq.com/"),
      f"key={vd.decode_key if vd else None}")
check("wx_response.json 清晰度列表解析",
      vd is not None and len(vd.renditions) >= 1
      and vd.renditions[0]["url"] == vd.url,
      f"renditions={len(vd.renditions) if vd else 0}")

# 多清晰度：标准 url + 原始 full_url（含各自尺寸/码率/大小）
multi = ('{"media":[{"url":"https://x/480.mp4","urlToken":"&t=1",'
         '"file_size":1000000,"width":"480","height":"854","bitrate":"500000",'
         '"full_url":"https://x/origin.mp4","full_file_size":10000000,'
         '"full_width":"1080","full_height":"1920","full_bitrate":"5000000",'
         '"decodeKey":"123"}]}')
vdm = load_and_extract(multi)
check("多清晰度解析（标准+原始）",
      vdm is not None and len(vdm.renditions) == 2
      and vdm.renditions[0]["url"].endswith("&t=1")
      and vdm.renditions[0]["size"] == 1000000
      and vdm.renditions[1]["url"] == "https://x/origin.mp4"
      and vdm.renditions[1]["size"] == 10000000
      and "1080x1920" in vdm.renditions[1]["label"],
      f"renditions={[(r['url'][:28], r['size']) for r in (vdm.renditions if vdm else [])]}")

camel = (
    '{"description":"测试视频","media":[{"url":"https://finder.video.qq.com/x?e=1",'
    '"urlToken":"tail123","decodeKey":12345,"fileSize":999,"mediaType":1,"coverUrl":"https://x/y.png"}]}'
)
vd2 = load_and_extract(camel)
check("camelCase objectDesc 解析",
      vd2 is not None and vd2.url.endswith("tail123") and vd2.decode_key == "12345"
      and vd2.size == 999 and vd2.cover_url.endswith(".png"),
      f"url_tail={vd2.url[-8:] if vd2 else None}")

img = '{"media":[{"url":"https://x/i.jpg","decode_key":"7","media_type":9}]}'
vd3 = load_and_extract(img)
check("图片类型识别 (mediaType=9)", vd3 is not None and vd3.is_image is True)

print("== 2. 解密器往返测试 ==")
fake = bytearray(b"\x00\x00\x00\x20ftypisom" + b"\x00\x00\x02\x00isomiso2avc1mp41")
fake += bytearray(range(256)) * (ENC_LEN // 256 + 1)
fake = fake[:ENC_LEN + 1000]

enc = bytearray(fake)
_xor_head(enc, 987654321)          # 加密（XOR 对称）
check("加密后文件头不再是 ftyp", is_encrypted(bytes(enc[:64])))

tmpdir = Path(tempfile.mkdtemp())
enc_path = tmpdir / "t.enc"
enc_path.write_bytes(bytes(enc))
out_path = decrypt_file(str(enc_path), "987654321", str(tmpdir / "t.mp4"))
check("解密输出生成", out_path is not None)
check("解密后与原文完全一致",
      out_path is not None and Path(out_path).read_bytes() == bytes(fake))

# 错误 key 应当失败
wrong_path = decrypt_file(str(enc_path), "111", str(tmpdir / "wrong.mp4"))
check("错误 key 解密失败（返回 None）", wrong_path is None)
shutil.rmtree(tmpdir, ignore_errors=True)

print("== 3. 真实样例验证（需要联网） ==")
url = "https://raw.githubusercontent.com/Evil0ctal/WeChat-Channels-Video-File-Decryption/main/wx_encrypted.mp4"
key = "2136343393"
tmp = Path(tempfile.gettempdir()) / "wx_encrypted_sample.mp4"
try:
    # urlretrieve 不带超时参数：网络异常时可能无限挂起（2026-08-26 实测），
    # 这里给 socket 级超时兜底，超时按「网络不可用」跳过并清理半截文件
    socket.setdefaulttimeout(15)
    print(f"  下载样例 {url}", flush=True)
    urllib.request.urlretrieve(url, tmp)
    print(f"  样例大小: {tmp.stat().st_size / 1024 / 1024:.2f} MB")
    out = tmp.parent / "wx_decrypted_sample.mp4"
    ok = decrypt_file(str(tmp), key, str(out))
    head = Path(out).read_bytes()[:16] if ok else b""
    check("样例解密成功（ftyp 头）",
          ok is not None and head[4:8] == b"ftyp",
          f"head={head.hex() if head else 'N/A'}")
    if ok:
        print(f"  解密产物: {out}")
except Exception as e:
    print(f"  ⚠️ 跳过真实样例验证（网络不可用或样例缺失）: {e}")
    tmp.unlink(missing_ok=True)

print()
print(f"结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
