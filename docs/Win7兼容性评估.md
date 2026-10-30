# Windows 7 兼容性评估（微信视频号下载器 / sphdl）

评估日期：2026-10-12
评估对象：`sphdl` v2.0.16（本地 HEAD `8bb5c1d`）
评估方法：**二进制级证据链**（PE 导入表扫描 + 依赖元数据 + 上游官方支持范围），非经验推测
评估结论：❌ **不支持 Windows 7，且不是配置问题，而是二进制级硬阻断**

---

## 一、结论（先看这里）

| 项目 | 结论 |
|---|---|
| 当前 v2.0.16 便携版能否在 Win7 运行 | **不能**。进程加载阶段即失败，界面都不会出现 |
| 失败表现（Win7 真机预期） | `无法启动此程序，因为计算机中丢失 api-ms-win-core-path-l1-1-0.dll`<br>（或 PyInstaller 的 `Error loading Python DLL: python311.dll`） |
| 能否通过"装个补丁/改配置"解决 | **不能**。存在两层独立硬阻断（CPython 运行时 + Rust 扩展），任一层都足以否决 |
| 能否通过"换成老版本 Python"解决 | 不能只换 Python：依赖链要求 Python ≥3.10，而 Win7 只能用到 Python 3.8 |
| 项目官方声明 | 已是 Win10/11（`README.md`、内置 `使用说明.txt`：「运行要求：Windows 10/11 64 位」） |

---

## 二、证据链（三层，逐层独立成立）

### 第 1 层：CPython 运行时 —— 决定性阻断

**事实**：本项目用 Python 3.11.6 构建，冻结版内嵌 `_internal/python311.dll`。

**实测**（`tools/pe_import_scan.py`，扫描 PE 导入表）：

```
=== python311.dll  [x64] ===
  ADVAPI32.dll / KERNEL32.dll / VCRUNTIME140.dll / VERSION.dll / WS2_32.dll
  api-ms-win-core-path-l1-1-0.dll  <<< Win7 上不存在
  api-ms-win-crt-*.dll (16 个)      ← 这些由 PyInstaller 随包提供，Win7 可用
  bcrypt.dll
```

`api-ms-win-core-path-l1-1-0.dll`（PathCch* API 集合）**是 Windows 8 起才提供的**，
Win7 无此 DLL 且无法用 UCRT 补丁补齐（UCRT 只提供 `api-ms-win-crt-*`）。

**上游官方佐证**（CPython 主分支 `Doc/using/windows.rst` 第 1312–1313 行，2026-10-12 取自 GitHub raw）：

> Python |version| supports Windows 10 and newer.
> **If you require Windows 7 support, please install Python 3.8.**

即：CPython 官方立场 = 当前 Python 需 Win10+，**Win7 最高只能到 Python 3.8**。

### 第 2 层：Rust 扩展 mitmproxy_rs —— 决定性阻断（独立于第 1 层）

**事实**：嗅探引擎依赖 mitmproxy 11.0.2 → `mitmproxy_rs 0.10.7`（Rust/PyO3 扩展）。

**实测 A（是否是加载期硬依赖）**：

```
$ python -c "import mitmproxy.addons.proxyserver; print([m for m in sys.modules if 'mitmproxy_rs' in m])"
proxyserver 导入后已加载: ['mitmproxy_windows', 'mitmproxy_rs.mitmproxy_rs', 'mitmproxy_rs.certs',
 'mitmproxy_rs.dns', 'mitmproxy_rs.local', 'mitmproxy_rs.process_info', 'mitmproxy_rs.tun',
 'mitmproxy_rs.udp', 'mitmproxy_rs.wireguard', 'mitmproxy_rs']
```

→ 只要启动引擎就必然加载 `mitmproxy_rs.pyd`，**无法绕过、无法按需延迟加载**。
（本项目 `.spec` 也把它显式 `collect_all('mitmproxy_rs')` 打进包里。）

**实测 B（该 pyd 的 PE 导入）**：

```
=== mitmproxy_rs.pyd  [x64] ===
  ...
  api-ms-win-core-synch-l1-2-0.dll
  bcryptprimitives.dll            ← Windows 8 起提供，Win7 无
  dwmapi.dll / iphlpapi.dll / ntdll.dll / oleaut32.dll ...
```

**上游官方佐证**（Rust 官方 `RELEASES.md`，Rust 1.78.0 / 2024-05-02 条目）：

> — [Windows tier 1 targets now require at least Windows 10]

mitmproxy_rs 0.10.7 是 2025 年用 Rust ≥1.78 构建 → 其 Windows 基线是 **Win10**。

**推论**：社区里那种「塞一个假的 `api-ms-win-core-path-l1-1-0.dll` 转发壳让 Python 3.9+ 在 Win7 跑」
（Electron/Rust 项目常用）的偏方，**对本项目无效**——第 2 层是 Rust DLL 的 Win10 基线，
不是单个路径 API 缺失。

### 第 3 层：依赖生态 —— 锁死"降级 Python"这条路

**实测**（`importlib.metadata`，取各依赖声明的 `Requires-Python`）：

```
mitmproxy            v11.0.2   Requires-Python: >=3.10
mitmproxy_rs         v0.10.7   Requires-Python: >=3.10
mitmproxy-windows    v0.10.7   Requires-Python: >=3.10
requests             v2.34.2   Requires-Python: >=3.10
werkzeug             v3.1.8    Requires-Python: >=3.9
cryptography         v44.0.3   Requires-Python: >=3.7, !=3.9.0, !=3.9.1
```

依赖链整体要求 Python ≥3.10，而 Win7 的上限是 Python 3.8（第 1 层）。
**唯一出口**是把整套依赖降到 2022 年水平（详见第四节路线 A）。

### 附：PyInstaller 已经尽力了（说明为何不是打包参数问题）

冻结包的 bootloader（`微信视频号下载器.exe`）导入表只有
`ADVAPI32 / COMCTL32 / GDI32 / KERNEL32 / USER32`，是 Win7 可加载的；
`_internal/` 里也带了 `ucrtbase.dll` + 38 个 `api-ms-win-core-*` / `api-ms-win-crt-*` 转发 DLL
（这正是 PyInstaller 对 Win7 的兼容补偿）。**卡点不在打包层，而在被冻进去的 CPython 3.11 与 Rust 扩展本身。**

---

## 三、复现方法（任何人可独立复核）

```bash
cd E:/myHermesWorkSpace/wechat-channels-downloader
export PYTHONIOENCODING=utf-8
D="sphdl/dist/v2.0.16/微信视频号下载器"

# 1) 扫描 PE 导入表（找 api-ms-win-core-path 即 Win7 阻断）
.venv/Scripts/python.exe tools/pe_import_scan.py \
  "$D/微信视频号下载器.exe" \
  "$D/_internal/python311.dll" \
  "$D/_internal/mitmproxy_rs/mitmproxy_rs.pyd"

# 2) 确认 mitmproxy_rs 是加载期硬依赖
.venv/Scripts/python.exe -c "import mitmproxy.addons.proxyserver,sys; \
print([m for m in sys.modules if 'mitmproxy_rs' in m])"

# 3) 查看依赖的 Python 版本下限
.venv/Scripts/python.exe -c "import importlib.metadata as md; \
print(md.metadata('mitmproxy_rs').get('Requires-Python'))"

# 4) 上游官方支持范围
curl -s https://raw.githubusercontent.com/python/cpython/main/Doc/using/windows.rst | \
  grep -A2 -i "supports Windows"
```

**真机确认（唯一还需做的验证，约 2 分钟）**：找一台 Win7 SP1 x64，解压便携版双击 exe，
记录弹窗原文与 `%TEMP%` 下的 PyInstaller 日志。预期即在第二节所述报错。
本仓库开发机为 Win10/11，**无 Win7 真机，故第 1/2 层结论基于二进制与上游官方证据，非真机复现**——
但两层阻断均为"库加载失败"级别，不依赖运行时行为，结论稳定。

---

## 四、如果确实必须支持 Win7：三条路线与成本

### 路线 A：老栈分支（Python 3.8.10 + mitmproxy 8.x + PyInstaller 5.x）❌ 不推荐

| 项 | 内容 |
|---|---|
| 工作量 | 3–5 人日 + **必须有 Win7 真机**（含微信 PC 客户端）做端到端验证 |
| 必需改造 | `sphdl/addon.py`（31KB，按 mitmproxy **11** API 写）需按 mitmproxy 8 API 重写；`proxy_manager.py` 引擎启停、`gui.py` 46 处 UI 逻辑需回归 |
| 交付风险 | Python 3.8 **2024-10 已 EOL**（无安全更新）；mitmproxy 8（2022-09）已停更；PyInstaller 5 的 bootloader 在 Win7 需另测 |
| 产品终局 | Win7 上微信 PC 客户端只能停在 3.9.x（微信 4.x 要求 Win10+），目标机是持续萎缩的死胡同 |
| 附加成本 | 之后每个新功能都要**双栈维护**（v11 分支 + v8 分支） |

### 路线 B：跨机嗅探（工具跑在 Win10 机器，Win7 只当"被嗅探的客户端"）✅ 可行且成本低

Win7 上**不装 Python、不装本工具**，只做两件事：系统代理指向 Win10 机器、装一次 CA 证书。
工具本体跑在 Win10/11 机器上（当前架构完全支持）。

```
[Win7: 微信PC 3.9.x + 系统代理 → 192.168.x.x:8899 + 已装 CA]
        │
        └──→ [Win10/11: sphdl v2.x 引擎（监听 0.0.0.0:8899）] → 下载/解密/落盘
```

需要的小改动（约 0.5–1 人日）：

1. `sphdl/proxy_manager.py`：`host="127.0.0.1"` 改为可配置，新增 `--listen-host 0.0.0.0`；
2. `sphdl/addon.py`：bridge 注入 JS 里的 `fetch("http://127.0.0.1:__PORT__/__sphdl/bridge")`
   目前**硬编码 127.0.0.1**（第 44/119/152 行），跨机时会打到 Win7 自己的 localhost →
   需新增 `__HOST__` 占位符，注入代理机的可达 IP；
3. 文档：Win7 侧证书安装 + 代理设置 + Windows 防火墙放行 8899。

**未验证前提（必须先验）**：Win7 上的微信 PC 3.9.x 视频号页面是否同样走
`channels.weixin.qq.com/web/pages/home` 的网络加载（bridge 钩子注入的前提）。
若老版微信走不同路径，路线 B 也会失效 —— 这一条只能在 Win7 真机上试。

### 路线 C：明确不支持 Win7（维持现状）✅ 默认推荐

保持 Win10/11 要求，把 Win7 明确写进"不支持"清单；确有需求的用户升级系统
（Win7 → Win10 升级通道仍开放）。零成本、零技术债，避免为了一个衰退平台背上双栈维护。

---

## 五、建议

1. **默认取路线 C**：v2.0.16 已在文档声明 Win10/11，本次评估把"不支持 Win7"从"未声明"变成"有证据的明确结论"即可。
2. 若 Win7 需求来自某个具体的人/机器 → **取路线 B**（跨机嗅探），投入小、不引入老栈技术债；
   实施前先在 Win7 真机上验第四节"未验证前提"。
3. **不要走路线 A**：为一个 2024 年已 EOL 的运行时 + 停更的 mitmproxy 8 背上双栈维护，
   收益（一台老机器）与成本（3–5 人日 + 永久双栈 + 无安全更新）严重不匹配。

---

## 六、附：真机验证清单（若拿到 Win7 机器，按此逐项记录）

| # | 步骤 | 预期 | 记录 |
|---|---|---|---|
| 1 | 记录 `winver` 版本与是否 SP1 | Win7 SP1 7601 | |
| 2 | 解压便携版到 `D:\sphdl\`，双击 exe | **弹错误框，不进界面** | 弹窗原文： |
| 3 | 查 `%TEMP%\_MEI*.log` 或事件查看器 | 加载 `python311.dll` 失败 | |
| 4 | 命令行运行 `微信视频号下载器.exe --selftest` | 同上失败 | |
| 5 | （若仍想尝试）用户态代理方案：工具跑 Win10，Win7 设代理 + 装 CA | 见路线 B | |
| 6 | Win7 微信版本 + 视频号页面是否走 `channels.weixin.qq.com` | 决定路线 B 是否可行 | |

---

## 七、本次评估同时发现的维护风险（非 Win7 相关，建议一并处理）

| # | 问题 | 证据 | 风险 |
|---|---|---|---|
| 1 | **GitHub 远程严重滞后** | `origin/main` = v1.2.2（2026-08-26），本地 HEAD = v2.0.16，`git rev-list --count origin/main..HEAD` = **22 个提交未推送** | 开发机故障即丢失 v2.0.0~v2.0.16 全部迭代 |
| 2 | 仓库体积 2.1 GB | `dist/`（20 个分享 zip 共 616 MB + 各版本展开目录）、`dist123x/` 51M、`dist_v121/` 63M、`build/` 40M、`build_tmp_release/` 40M 均在项目内 | 备份/克隆/传输成本高；dist 产物混入工作树 |
| 3 | 文档双份、版本不一致 | 根 `docs/交接文档.md`（含 v2.0.16）vs `sphdl/docs/交接文档.md`（2026-08-26，早于 v2.0.16） | 接手者读错版本 |
| 4 | 根 `README.md` 引用旧版本 | 第 55 行仍写 `微信视频号下载器-v1.2-win64-portable.zip`，实际最新为 v2.0.16 | 用户拿到旧包 |
| 5 | 测试为手工触发，无 CI | `tests/` 有 4 个套件，本次实测 **12/12 通过**（含真实加密样例解密出 `ftyp`）；但无自动化门禁 | 每次发版靠人肉记得跑 |

> 基线记录：本次评估运行 `tests/test_offline.py` → **12 通过 / 0 失败**
> （解析 7 项 + 解密往返 4 项 + 真实样例 1 项，样例 13.44 MB，解密头 `000000206674797069736f6d`）。
