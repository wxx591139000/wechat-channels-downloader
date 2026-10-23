"""用户数据目录解析 + 跨版本自动迁移（v2.2.5）。

═══ 为什么需要（2026-10-23 真实事故）═══

客户报障：「为何我换新版本 2.2.4 时，之前授权好的视频号与公众号授权没了」

排查：
```
dist/v2.2.2/微信视频号下载器/config.json   授权码 1 个（视频号）
dist/v2.2.3/微信视频号下载器/config.json   授权码 2 个（视频号+公众号）
dist/v2.2.4/微信视频号下载器/config.json   授权码 0 个   ← 新目录，空的
```

根因：`BASE_DIR = exe 所在目录`（便携布局），
每发一个新版客户解压到**新文件夹** → `config.json` 从头开始 →
授权码、列表、设置全没了。客户得重新贴一遍授权码，体验极差。

═══ 做法 ═══

配置与记录改存**用户级目录**（`%LOCALAPPDATA%\\sphdl`），
换版本目录后自动沿用。首次启动时按顺序找旧数据并迁移：

  ① 用户级已有 → 直接用（常态）
  ② 程序目录有 config.json → 迁移过去（老用户首次升级）
  ③ 相邻版本目录里有授权码最多的 config.json → 迁移（跨版本升级）
  ④ 都没有 → 用空的

★ 下载目录（downloads/）**保持跟 exe 放一起** —— 视频文件体积大，
  不该塞进系统盘；只把"小而重要"的配置/列表搬到用户级。

★ 可用环境变量 `SPHDL_DATA_DIR` 覆盖（做绿色便携版或调试时用）。
"""

import json
import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "sphdl"
ENV_OVERRIDE = "SPHDL_DATA_DIR"

_MIGRATED = {}          # 本进程内缓存解析结果，避免重复扫描


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录（exe 目录 / 开发时的项目根）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_dir() -> Path:
    """用户级数据目录。

    - 开发模式：不折腾，仍用项目目录（避免污染本机 %LOCALAPPDATA%）
    - 冻结模式：%LOCALAPPDATA%\\sphdl，换版本目录不丢
    - 环境变量 SPHDL_DATA_DIR 优先（便携/调试）
    """
    env = (os.environ.get(ENV_OVERRIDE) or "").strip()
    if env:
        p = Path(env)
        return p if p.is_absolute() else (app_dir() / p).resolve()
    if not is_frozen():
        return app_dir()
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or "").strip()
    if base:
        return Path(base) / APP_DIR_NAME
    return app_dir()          # 极端情况兜底：还是放程序目录


def _license_count(cfg_path: Path) -> int:
    """配置里有多少个授权码（用于挑选"最有价值"的旧配置）。

    ★ 必须容错：这个函数会被拿去给 records.json 排序（那是 **JSON 数组**），
      原来直接 `d.get(...)` → `AttributeError: 'list' object has no attribute 'get'`
      → 客户 v2.2.5 启动即崩（2026-10-23 事故）。
      任何非 dict 的 JSON 一律返回 0，绝不抛。
    """
    try:
        d = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(d, dict):
        return 0            # records.json 是 list；其他脏数据同理
    try:
        lic = d.get("license_codes")
        if isinstance(lic, dict):
            return sum(1 for v in lic.values() if v)
        # 兼容单码的极老版本
        return 1 if d.get("license_code") else 0
    except Exception:
        return 0


def _record_value(path: Path) -> int:
    """records.json 的"价值"：记录条数（列表越长越值得迁移）。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if isinstance(d, list):
        return len(d)
    if isinstance(d, dict):
        # 极老版本可能存成 {"items": [...]}
        items = d.get("items")
        return len(items) if isinstance(items, list) else 0
    return 0


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def find_previous(fname: str, start: Path) -> Path:
    """在相邻的版本目录里找 fname，挑「授权码最多 → 最新」的那份。

    目录形态（客户常见）：dist/<版本>/微信视频号下载器/config.json
    所以从 start 往上找 2 层，扫 ``*/fname`` 和 ``*/*/fname``。
    """
    start = start.resolve()
    seen = set()
    cands = []
    for depth in (1, 2):
        base = start
        for _ in range(depth):
            base = base.parent
        if not base or not base.is_dir():
            continue
        try:
            patterns = (f"*/{fname}", f"*/*/{fname}", f"*/*/*/{fname}")
            for pat in patterns:
                for p in base.glob(pat):
                    try:
                        rp = p.resolve()
                    except Exception:
                        continue
                    if rp in seen or not rp.is_file():
                        continue
                    # 跳过自己这棵目录
                    try:
                        rp.relative_to(start)
                        continue
                    except Exception:
                        pass
                    seen.add(rp)
                    cands.append(rp)
        except Exception:
            continue
    if not cands:
        return None
    # ★ v2.2.6：按文件名选对应的"价值"算法
    #   config.json → 授权码个数；records.json → 记录条数
    #   原来两种情况都用 _license_count，records.json 是 list 会崩。
    if fname.startswith("records"):
        key = lambda p: (_record_value(p), _mtime(p))       # noqa: E731
    else:
        key = lambda p: (_license_count(p), _mtime(p))      # noqa: E731
    try:
        cands.sort(key=key, reverse=True)
    except Exception:
        # 排序出任何意外都不要让程序起不来 —— 退回按修改时间
        try:
            cands.sort(key=_mtime, reverse=True)
        except Exception:
            pass
    return cands[0]


def resolve(fname: str) -> Path:
    """返回该数据文件应使用的路径，必要时把旧数据迁移过来。

    fname: "config.json" / "records.json"

    ★ 这是**启动路径上的函数**，任何异常都会导致程序打不开。
      所以整个函数包在 try 里，出问题一律退回程序目录（老行为），
      绝不因为"迁移失败"让客户用不了软件（2026-10-23 事故教训）。
    """
    if fname in _MIGRATED:
        return _MIGRATED[fname]
    try:
        return _resolve_inner(fname)
    except Exception:
        fallback = app_dir() / fname
        _MIGRATED[fname] = fallback
        return fallback


def _resolve_inner(fname: str) -> Path:
    uhome = user_dir()
    target = uhome / fname

    # ① 用户级已有 → 直接用
    if target.exists():
        _MIGRATED[fname] = target
        return target

    try:
        uhome.mkdir(parents=True, exist_ok=True)
    except Exception:
        # 建不了目录（权限/只读盘）→ 退回程序目录，别把程序弄挂
        _MIGRATED[fname] = app_dir() / fname
        return _MIGRATED[fname]

    src = None
    # ② 程序目录（同目录，老用户首次升级）
    same = app_dir() / fname
    if same.exists() and same.resolve() != target.resolve():
        src = same
    # ③ 相邻版本目录
    #   ★ 不依赖 is_frozen()：开发模式下也可能有人把项目放在 dist/<ver>/ 里，
    #     而测试需要能覆盖这条路。find_previous 本身是安全的（找不到返回 None），
    #     所以无条件调用即可。
    if src is None:
        try:
            src = find_previous(fname, app_dir())
        except Exception:
            src = None

    if src is not None:
        try:
            shutil.copy2(src, target)
        except Exception:
            # 迁移失败 → 继续用旧文件，至少能跑
            _MIGRATED[fname] = src
            return src

    _MIGRATED[fname] = target
    return target


def migration_report() -> dict:
    """给界面用：说明本次是否发生了迁移（不迁移则返回空）。"""
    out = {}
    for fname, path in _MIGRATED.items():
        try:
            app_file = app_dir() / fname
            if path.parent != app_file.parent:
                out[fname] = str(path)
        except Exception:
            continue
    return out


def reset_for_tests():
    _MIGRATED.clear()
