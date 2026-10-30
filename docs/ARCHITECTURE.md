# 项目详细方案 ARCHITECTURE — 微信视频号下载器 (sphdl)

> 归档日期：2026-08-25 ｜ 当前版本：v1.1.0

## 1. 系统架构（文字描述）

```
┌─────────────────────────── 用户机器 (Windows 10/11) ───────────────────────────┐
│                                                                                │
│  ┌──────────────┐   系统代理(注册表)    ┌────────────────────────────────────┐  │
│  │ PC 微信       │ ───────────────────→ │  sphdl 嗅探引擎                     │  │
│  │ (liteapp:    │   127.0.0.1:8899    │  mitmproxy Master + SphAddon       │  │
│  │  HTML 外壳走  │                      │  ① 响应含 object_desc → API 直嗅    │  │
│  │  网络, 可注入) │ ←─────────────────── │  ② HTML </head> 前注入 bridge 钩子  │  │
│  └──────┬───────┘   (解密后的 HTTPS)    └───────────────┬────────────────────┘  │
│         │  JS: WeixinJSBridge.invoke 包装器              │                      │
│         │  POST {url,cmdName,arguments,result}          │                      │
│         └────────────→ 127.0.0.1:8899/__sphdl/bridge ───┘                      │
│                                                                                │
│  SphAddon 命中 → Pipeline 工作线程:  下载(单连接+续传) → Isaac64 解密 → 保存     │
│  GUI (tkinter 主线程) ← queue.Queue ← 引擎/下载线程（绝不跨线程碰 tkinter）      │
└────────────────────────────────────────────────────────────────────────────────┘
         加密视频 CDN（微信 UA 50-85KB/s，约 400KB/连接 掐断）
```

## 2. 模块划分与职责（`sphdl/sphdl/` 包）

| 模块 | 职责 | 关键设计 |
|------|------|----------|
| `gui.py` | tkinter 界面；引擎线程生命周期；单实例互斥；列表/日志/状态栏 | 主线程只做 UI 刷新（root.after 轮询队列）；引擎在独立线程跑 asyncio；跨线程一律 queue 投递 |
| `addon.py` | mitmproxy 插件：双路径捕获 | ④ bridge 钩子：HTML 的 `</head>` 前注入 invoke 包装器，把原生 IPC 回调 POST 回代理自身 `__sphdl/bridge`；addon `request()` 截获并短路响应。jsapi_resp 是「JSON 字符串套 JSON」，parser 二次解码 |
| `parser.py` | object_desc → VideoData | snake/camel 双兼容；提取 url / decode_key / size / video_id(md5sum) / mediaType |
| `downloader.py` | 加密文件下载 | 单连接顺序 + Range 断点续传（≤8 轮）+ 120s 读超时 + 微信 UA；多线程分片已弃用（实测分片饿死） |
| `decryptor.py` | Isaac64 解密 | 纯 Python RandCtx64/mix/is_aac64；只解前 131072 字节；ftyp 头校验 |
| `pipeline.py` | 任务队列 | 工作线程消费；状态机 waiting→downloading→decrypting→done/failed/stopped |
| `proxy_manager.py` | 系统代理 set/restore + CA 检测 | 注册表读写；退出/异常路径都恢复（atexit + finally） |
| `shared.py` | 同进程共享 | 捕获到的微信 webview UA 供下载器使用 |
| `config.py` | 路径常量 + **档位定义** | **冻结感知**：`sys.frozen` 时 BASE_DIR 跟随 exe（便携布局），开发时跟随源码树。档位单一事实源：`MAX_RECORDS_TIERS/NAMES/LABELS/QUOTA` + `label_of/name_of/quota_of`；档位来源解析：`locked_max_records()`（构建预置）、`build_default_max_records()` |
| `license.py` | **授权码生成/校验**（v2.0.17） | HMAC-SHA256 截断（10B）+ base32（去 I/L/O/U 字母表，解码容忍 O→0/I→1）；载荷 `ver\|tier\|days//8\|rand(2)`；档位编码 `1=500/2=1000/3=2000/4=20000`；密钥经 `.license_secret` 混淆注入 `_lic_key.py`（缺则任何码无效）；`selftest()` 做往返/篡改/容错自检 |
| `_edition.py` | **构建期档位预置**（v2.0.17） | `LOCKED_MAX_RECORDS` 非 None 即锁档（界面隐藏授权入口）；仓库内永为默认未锁档；`release.py` 构建前改写、构建后自动还原 |
| `logger.py` | 日志落盘 | `logs/sphdl.log`，冻结感知路径 |
| `entities.py` | VideoData 实体 | dataclass，parser/downloader/pipeline 共用 |

**授权体系架构（v2.0.17）**

```
作者侧（绝不随包分发）                     客户侧（冻结包内）
─────────────────────────                 ─────────────────────────
tools/license_tool.py                     sphdl/license.py
  gen 500|1000|2000|20000                   verify_code(用户输入的码)
   │ 用 .license_secret                      │ 用内置 _lic_key.K
   ▼                                        ▼
SPHDL-XXXX-… (HMAC 码)  ──微信/QQ──▶  授权管理弹窗 → _activate_license()
                                          → _apply_tier() → 立即生效(无需重启)

档位来源优先级（唯一解析入口 App._resolve_tier）：
  ① _edition.LOCKED_MAX_RECORDS（构建预置，写死）
  ② 有效授权码里的 tier（未过期）
  ③ config.DEFAULT_MAX_RECORDS（默认 500）
```

## 3. 数据流 / 调用链路

**主链路（PC 微信播放）：**
```
微信播放视频号
 → liteapp HTML 外壳请求 channels.weixin.qq.com/web/pages/home（过代理）
 → addon response() 注入 bridge 钩子 JS（</head> 前）
 → 页面 JS: WeixinJSBridge.invoke("finderH5ExtTransfer", …) 被包装器拦截
 → POST 127.0.0.1:8899/__sphdl/bridge {url, cmdName, arguments, result}
 → addon request() 截获 → parser 二次解码 jsapi_resp → VideoData
 → GUI 列表出现「待下载」条目（不自动下载）
 → 用户选中 → 「⬇ 下载选中」/ 双击
 → Pipeline: downloader（单连接+续传+微信UA）→ decryptor（Isaac64 前128KB）
 → downloads/<video_id>.mp4（ftyp 校验）→ 列表状态「已完成 ✓」
```

**备用链路（浏览器网页版）：** 直接嗅探含 `object_desc` 的 API JSON 响应（API 直嗅），
同一 parser 入口。

**手动链路：** `--paste file.json` / `--paste-text '…'` → parser → Pipeline，无代理。

## 4. 关键设计决策

| 决策 | 原因 |
|------|------|
| 捕获与下载分离（不自动全量下载） | 用户需求（2026-08-24 原话）：「避免无脑下所有监听的视频，本身下载速率就慢」；GUI 必须多选显式入队 |
| 单连接顺序下载，弃多线程分片 | 实测分片饿死；CDN 每连接掐 ~400KB，8 轮 Range 续传已最大化单连接 |
| 微信 UA 用于下载 | CDN 对微信 UA 限速 50-85KB/s，非微信 UA 仅 10-20KB/s 且晾连接 |
| 去重键 = media.md5sum（video_id） | 同一视频重放会带新 token/URL，按 URL 去重会重复下载（曾致 4 份同文件） |
| bridge 钩子而非纯 API 嗅探 | PC 微信 objectDesc 走原生 IPC（WeixinJSBridge.invoke），网络不可见；HTML 外壳走网络可注入 |
| 纯 Python Isaac64 而非 WASM | 无运行时依赖；上游两条路线算法同源，纯 Python 更稳（见 reference/ 两路线对照） |
| tkinter 而非 Web/Electron | 双击出窗口、零新增依赖、冻结体积小 |
| PyInstaller onedir + 冻结感知 BASE_DIR | 便携版配置/日志/下载全跟随 exe，整目录拷走即迁移 |
| GUI 不自动起引擎 | 用户显式点「启动嗅探」；避免误接管系统代理 |
| 版本单一事实源 `sphdl/__init__.py __version__` | v1.1.0 起：窗口标题 / CLI banner / 打包交付物统一引用，禁止散落硬编码 |
| bridge 回传字节级预检（v1.2.1） | body 不含 object_desc/objectDesc 字面键时跳过 json.loads+递归扫描（零漏报：解析器只按字面键搜索）；消除大 JSON 解析对事件循环的拥塞——长时播放捕获停摆事故主因 |
| 钩子存活看门狗：5s 心跳 + 90s 播放无钩子数据告警（v1.2.1） | 页面重载/回收后钩子丢失曾完全静默；现页面 JS 心跳 + 未包装补包，代理端跟踪钩子/流活性，状态栏「钩子」指示灯 + 日志警告 + 60s 心跳日志 |

## 5. 部署架构（两种形态）

> 本项目**无 VPS 部署形态**（纯本地单机工具，无公网服务）。对应关系如下：

**形态 A：免安装便携版（对外交付）**
```
微信视频号下载器-v1.1-win64-portable.zip（30MB，已脱敏）
└── 微信视频号下载器/
    ├── 微信视频号下载器.exe     ← PyInstaller onedir 入口
    ├── _internal/               ← 冻结依赖（含 mitmproxy、tkinter 运行时）
    ├── install-cert.bat         ← 证书一键安装（%USERPROFILE%，无硬编码路径）
    └── 使用说明.txt             ← 用户手册
运行后自动产生（全跟随 exe 所在目录）：downloads/ logs/ config.json records.json
```

**形态 B：开发版（本机迭代）**
```
E:\myHermesWorkSpace\wechat-channels-downloader\sphdl\   ← git 仓库（repo 根 = sphdl/）
├── run.bat        ← 双击即 GUI；首次自动建 .venv 装依赖（优先 sphdl/.venv，退回项目根 .venv）
├── main.py        ← 入口
├── sphdl/         ← 核心包
└── 微信视频号下载器.spec  ← PyInstaller 配置（datas: install-cert.bat → 顶层+_internal 双份，GUI 有 fallback）
启动：run.bat [--gui|--paste|--selftest|嗅探参数]
```

## 6. 双目录结构说明

- **项目根** `E:\myHermesWorkSpace\wechat-channels-downloader\`：
  归档/交接层。含 `docs/`（本 5 文档 + 交接文档 + 使用SOP + UI需求）、`reference/`
  （上游参考工具 Evil0ctal/ThinkerWen + 解包产物）、`tool/`（原始 tool.exe）、
  `tools/`（pyinstxtractor 等）、`state/`、`downloads/`（用户内容，隐私，不入归档）。
- **仓库根** `…\wechat-channels-downloader\sphdl\`：
  git 仓库 `wxx591139000/wechat-channels-downloader`（PRIVATE），只含工具本体
  （main.py / sphdl 包 / README / requirements / spec / dist 的 v1.1 zip）。
  归档 tar 以项目根为根打包，仓库是其子集。

## 8. 授权与防盗架构（v2.1.8 新增）

> 完整方案：`防盗体系-VPS完整方案.md`（四层防护、VPS 部署、运营动作、常见排查）

### 8.1 四层防护结构

```
第 1 层  授权码自身      HMAC-SHA256 签名 + 码内嵌档位/产品/台数/设备绑定
第 2 层  设备绑定        ver=3，4 项取 2，死绑单机
第 3 层  台数上限        ver=4，激活时服务器统计执行
第 4 层  追溯            水印(截图带设备码) + 激活上报 + 台账
```

前 3 层是"锁"，第 4 层是"眼睛"。**锁能被撬，眼睛摘不掉。**

### 8.2 数据流

```
客户端                                     VPS
──────────────────────────────────────────────────────────────
解密授权码 (ver 分流)
  ├─ ver=3 → 校验设备分量 (4项取2) ────────→ 不需联网
  └─ ver=4 → 激活时上报 ─────────────────→ license_server:3010
                                            ├ 查该码已用台数
                                            └ 返回 allowed true/false
                                            license.db (SQLite)
                                                  ↑ 只读
手机后台 ─────────────────────────────────→ sphdl_admin:3011
                                            └ licenses.jsonl (台账)
```

### 8.3 关键技术决策

| 决策 | 理由 |
|---|---|
| 4 项取 2 而非整串哈希 | 换硬盘/换主板/重装系统不失效（行业惯例 3~5 取 N） |
| 台数限制靠服务器统计 | "最多 N 台"无法纯离线实现；断网放行是取舍 |
| 作废只记账不拦截 | 软件离线工作，联网校验与便携定位冲突（用户明确选择） |
| 台账 append-only | 历史不可篡改；改名/作废都是追加事件 |
| 上报"只报不验" | 失败静默，服务器挂了客户照常用，不产生联网激活的体验损失 |
| 密钥放 VPS | 手机发码必须；风险用 600 权限 + 只监听 127.0.0.1 缓解 |

### 8.4 VPS 服务清单（零影响纪律）

| 服务 | 端口 | 归属 |
|---|---|---|
| bot（转录机器人） | 3001/3002/3003 | **原有业务，勿碰** |
| cloudflared | 隧道 | 原有，改配置要备份 |
| sphdl-license | 3010 | 本项目新增 |
| sphdl-admin | 3011 | 本项目新增 |

**新目录 + 新端口 + 独立 systemd + 纯标准库**。每次部署后 diff 端口快照，
确认只多出新端口。

## 9. 客户端配置持久化（v2.1.8）

### 9.1 存储布局（便携式：全部跟 exe 放一起）

```
<解压目录>/
├── 微信视频号下载器.exe
├── config.json          ← 设置 + 授权码（按产品线分槽）
├── config.json.bak      ← ★ 自动备份（v2.1.8）
├── records.json         ← 列表记录
├── proxy_backup.json    ← 系统代理快照（用于恢复）
├── logs/                ← 运行日志
└── downloads/           ← 默认下载目录
```

`BASE_DIR = exe 所在目录`（冻结模式）→ 整个文件夹拷走就能用。

### 9.2 配置读写流程

```
启动
 └─ AppConfig._load()
     ├─ 读 config.json
     │   ├─ 成功            → 正常加载
     │   ├─ FileNotFoundError → 读 config.json.bak（有则恢复，记 _config_load_error）
     │   └─ JSON 解析异常     → 读 config.json.bak（有则恢复，记 _config_load_error）
     └─ 都失败 → 回默认档 + 记 "授权需要重新激活"

App.__init__ 末尾
 └─ _config_load_error 非空 → push_log("warn") + root.after(600, 弹窗)

任意保存（save）
 ├─ 写 config.json
 └─ 成功后 shutil.copy2(config.json → config.json.bak)
```

### 9.3 授权码存储（按产品线分槽）

```json
{
  "license_codes": {"1": "<视频号码>", "2": "<公众号码>"},
  "license_activated_at": {"1": "2026-10-17T14:31:24+08:00", "2": "..."}
}
```

**为什么分槽**：v2.0.19 前只存单个 `license_code`，导致"激活视频号码
把公众号码顶掉"。现在一个码只能解锁一条产品线，两条线可并存。

**兼容**：`license_code` / `license_activated_at_legacy` 字段仍保留读取，
老配置不会丢授权。

---

## 10. 清单交互层设计（v2.2.x）

### 10.1 序号：显示位置 vs 抓取顺序

```
Record.seq            捕获时递增分配（max(已有)+1），永不改变
      ↓
_apply_sort()         点「序号」表头时按 seq 排序
      ↓
_renumber_rows()      无论怎么排序/筛选，序号列重编为 1..N
```

**为什么必须分离**：
- 客户要"下前 20 个" → 需要序号列是**当前显示位置**（1..N 连续）
- 客户要"顺序和视频号一致" → 需要按**抓取先后**排序（seq）
- 两者混用会导致：删记录后序号留空档（客户以为乱）、
  或按显示位置排序（换个排序就乱）

### 10.2 停止嗅探 ≠ 停止下载（但会断下载通道）

```
下载器 → requests（trust_env=True）→ 读系统代理 → 经 mitmproxy
                                          ↓
                        「停止嗅探」= mitmproxy 退出 + 系统代理恢复
                                          ↓
                              正在下载的连接被 RST
```

**这是架构决定的，不是 bug**。处理方式：
1. 停嗅探前检查 `store` 里有无 `downloading/pending`
2. 有则让客户选「等下载完再停」（`_check_pending_stop` 每 800ms 轮询）
3. 失败保留 `.tmp`（`_keep_tmp()`：主动停的不保留）
4. 启动清理 7 天以上 `.tmp`（`_cleanup_stale_tmp`）

### 10.3 列索引不能硬编码

v2.2.0 加「序号」列后，「来源」列从 `#3` 变 `#4`，
但代码里硬编码 `col == "#3"` 和 `vals[2]` → **双击筛选静默失效**。

现在用 `_col_index("platform")` 按列名动态查。
**以后再加列不会再犯。**

### 10.4 原始数据诊断开关

客户要求「顺序与视频号完全一致」，但**视频号订阅流原始数据里是否带位置序号字段
无法凭空假设**。故加开关：

```
程序目录建空文件 `开启原始数据记录.txt` → 重启生效
    ↓
每条捕获的 object_desc 存到 diag_object_desc/*.json（最多 200 个）
    ↓
拿到样本 → 确定用哪个字段 → 实现严格对齐
```

`.gitignore` 已挡 `diag_object_desc/`（客户原始数据不入库）。

---

## 11. 话术库数据流（2026-10-23）

### 11.1 存储

```
/opt/sphdl-admin/data/snippets.json     ← 独立文件，不在台账里
{
  "template": "请您查收...",
  "versions": [{"ver","channel","filename","link","pwd"}, ...]
}
```

**去重键 = (ver, channel)**。按 `ver` 去重会导致「存夸克覆盖百度」（实测踩到）。

`channel` 旧数据缺失时，`snippets_load()` 用 `channel_of(link)` 自动补。

### 11.2 两条输入路径

```
用户粘贴网盘分享文字
   ├─ 前端 JS_PASTE.autoParse()  → 即时填 4 栏 + 显示「已识别：版本/文件名/链接/提取码」
   └─ 服务端 parse_share_text()  → 前端没填全时兜底再解析
```

### 11.3 两条输出路径

```
发码结果页
   ├─ 复制授权码
   ├─ 复制话术 + 授权码   ← 套路词 + 网盘三行 + 空行 + 授权码
   └─ 只复制话术
```

### 11.4 五个 JS/f-string 坑

| 坑 | 现象 | 修法 |
|---|---|---|
| JS 大括号 vs f-string | `SyntaxError: invalid syntax` | 脚本抽独立常量 `JS_PASTE` |
| raw string 转义 | JS 收到 `\s` 而非 `\s`，正则全废 | 写 `\s`；断言无双反斜杠 |
| f-string 内反斜杠 | `f-string expression part cannot include a backslash` | 先算成变量再插入 |
| clipboard 非安全上下文 | 手机 http 下复制无反应 | `execCommand('copy')` 兜底 |
| NAV.format 参数写死 | 加导航项 → IndexError | `nav_on(idx)` |
---

## 12. 评论抓取链路（v2.2.12 ~ v2.2.15）

```
微信视频号页面
  │  JS 钩子包装 WeixinJSBridge.invoke
  ▼
POST /__sphdl/bridge（含 objectDesc 的：视频 / 不含的：评论等）
  │
  ├─ addon.request()
  │    · 含 objectDesc        → 走视频解析
  │    · 不含 → 字节预检
  │         nick/content/comment 命中 → 投给解析线程（source="__comment__"）
  │         否则 → 直接回 OK 丢弃
  ▼
_parse_worker_loop()
  │  source == "__comment__" → _handle_comment_payload()
  │      extract_comments()  → 结构识别（不看 cmdName）
  │      _parse_comment_node() → 递归解析嵌套回复
  │      去重（cid / 昵称+内容）
  ▼
on_comments(video_key, fresh, total)   ← 解析线程
  │  ui_q.put(("comments", ...))       ← tkinter 非线程安全，必须转主线程
  ▼
_apply_comments()                       ← 主线程
  │  写入 Record.comments / comment_count / comments_updated
  ▼
records.json（持久化）
```

### 关键设计约束

1. **不依赖 cmdName / 字段名**：接口会改名，改为结构识别
2. **关联到「当前播放的视频」**：`_on_capture` 时更新 `_cur_video_key`，
   评论 payload 到达时挂到它身上
3. **线程边界**：解析线程只做解析，写 UI/Store 一律投递 `ui_queue`
4. **`Record` 加字段必须同步 `_load` 白名单**（`seq` 曾因此重启丢数据）

### 拦截范围（v2.2.15 定型）

```
先排除（裸透传）：v.qq.com / vv.video.qq.com / live.qq.com / now.qq.com
再拦截（MITM）  ：*.qq.com / *.weixin.qq.com / *.qpic.cn / *.gtimg.cn
                  / *.tc.qq.com / finder.video.qq.com / *.qlogo.cn
其余             ：全部裸透传（ignore_connection = True）
```

★ 排除项**不能写 `video.qq.com` 通配** —— 会把 `finder.video.qq.com`
（视频号视频 CDN）一起透传掉，导致抓不到视频。只写 `vv.video.qq.com`。
---

## 13. 授权校验与吊销（v2.3.1）

### 两层校验

```
第一层：本地 HMAC（license.py，零网络）
  激活时验证码签名 → 解出 tier/days/maxdev/flags/prod
  之后每次下载只读本地 cfg.license_codes

第二层：云端吊销名单（revocation.py，联网）
  启动后 5 秒 + 每 30 分钟 GET /api/revoked?codes=...
  命中 → _revoked_reason 置位 → _has_download_license() 返回 False
```

### 判定优先级（`_has_download_license`）

```python
if self._revoked_reason:            # 1. 被作废 → 一律无权限
    return False
if self.cfg.locked_max_records:     # 2. 绑定版 → 视为已授权
    return True
info = self._license_info(...)      # 3. 本地 HMAC 校验 + 有效期
return bool(has_feature(info, PRODUCT_VIDEO))
```

### 服务器部署结构

```
license.zhuanlu.xyz  → tunnel → 127.0.0.1:3010 → license_server.py
                                                  ├── POST /api/report   上报
                                                  ├── GET  /api/stats    统计
                                                  └── GET  /api/revoked  吊销名单 ★

sphdl-admin.zhuanlu.xyz → tunnel → 127.0.0.1:3011 → sphdl_admin.py（发码后台）
                                                      └── 台账 licenses.jsonl
```

★ 两个服务**在同一台机器**，所以 `license_server.py` 直接读
`/opt/sphdl-admin/data/licenses.jsonl` 生成吊销名单，不需要同步机制。

### 为什么端点用 GET 不用 POST

`sphdl_admin.py` 的 `do_POST` 在路由分发前有 `_sess_ok()` 会话校验，
客户端没有后台会话 → POST 会拿到**登录页 HTML**（HTTP 200 但不是 JSON）。
`license_server.py` 的 `do_GET` 里 `/api/revoked` 不需要鉴权，GET 正常。
客户端 `_get_json()` 还会检查响应是否以 `{` 开头，拿到 HTML 就视为失败。

---

## 14. 授权体系（v2.6.x）

### 14.1 三处实现必须算法一致

```
sphdl/license.py          客户端校验（离线 HMAC）
server/sphdl_admin.py     发码后台（线上发码）
tools/license_tool.py     本地发码 CLI
```

**三者 MAC 算法必须逐字节一致**，否则客户从后台拿到的码在客户端无效。
已验证手段：`tests/verify_admin_caps.py` 里「后台生成 → 客户端解析」实测。

### 14.2 能力位（caps）叠加语义

```python
# 客户端：遍历所有已激活码，任一含该位即解锁
def _has_like_stats(self):
    for c in self._all_license_codes():
        info = lic.verify_code(c)
        if info and lic.has_capability(info, lic.CAP_LIKE_STATS):
            return True
    return False
```

**存储格式陷阱**：`config.license_codes` 是 **dict**（键=产品线序号），
不是 list。按 list 遍历会导致「已授权客户仍显示未授权」。
`_all_license_codes()` 同时兼容 dict / list / 单字符串。

### 14.3 抖音独立授权（PRODUCT_DOUYIN=3）

- 未授权按钮显示 `🎵 抖音批量下载 🔒`（深青专属底色），仍可点
- 点击 → 弹框「独立产品，请联系发布方购买」
- 授权后自动解锁；码被作废锁回

### 14.4 授权管理页四态展示

`视频号 / 公众号视频 / 抖音 / 点赞增强包` 四项，各自 ✓ 或 ✗。
未授权项 `cursor=hand2` + `<Button-1>` 绑定 → 购买引导。

### 14.5 发码后台

- 产品下拉含三个产品线
- ☑ 点赞增强包 勾选框 → 生成 ver=5 码
- 复制文案带标签：`轻量2台授权码：` / `公众号视频授权码：` /
  `抖音批量下载授权码：` / `点赞增强包授权码：`
- **结果页标签靠台账反查**（`label_from_ledger`）——
  `_page_new` 只拿到 code 字符串，拿不到 tier/product/caps。
  ⚠️ 曾因直接引用 `tier` 导致 NameError → 页面 500 → CF 502。

---

## 15. 直播回放与小程序短剧的技术路径（v2.6.6 ~ v2.8.1）

### 15.1 为什么回放能"滑列表就抓"、小程序必须"边播边存"

| | 直播回放 | 小程序短剧 |
|---|---|---|
| 数据载体 | `finderH5ExtTransfer` 的 bridge 响应 | `wxamedia.com` 的 HTTP mp4 响应 |
| 列表是否带下载地址 | ✅ 每条都带完整 url + file_size + md5 | ❌ 只有剧集 hash |
| 历史记录是否可枚举 | ✅ 列表 DOM/接口均可 | ❌ 无 |
| URL 是否有时效 | ❌ 无需签名（encfilekey 长期） | ✅ `t=` 几分钟过期 |
| 能否外部重下 | ✅ 可 | ❌ 403（依赖会话） |

**结论**：回放走"抓到地址后排入下载队列"；
小程序只能"流经即落盘"（tee），因为没有可复用的地址。

### 15.2 边播边存的关键实现

```
response() 钩子
  └─ host.endswith("wxamedia.com") 且 path 含 .mp4
       └─ _save_miniapp_media(flow)
            ① 解析 content-range → start / total
            ② 按 start 写入 <集号>.mp4.part
            ③ 维护已写区间列表，合并后判断是否 [0, total-1] 全覆盖
            ④ 全覆盖 → os.replace() 改名
```

**踩过的坑**：
- 用文件大小判完成 → 乱序分片时先写末尾段，size 已达 total
  但前面是空洞 → 产生残缺文件。必须按**区间覆盖**判。
- 落盘目录用了 `config.DOWNLOAD_DIR`（模块默认）而不是
  GUI 的 `cfg.download_dir` → 客户在自己目录里找不到文件。
- `self.addon.out_dir = ...` 写在 addon 创建之前 → AttributeError
  被 `except: pass` 吞掉 → out_dir 永远 None。**静默吞异常是元凶**。

### 15.3 批量拉取的滚动驱动（v2.0.8 原有 + v2.7.x 增强）

**核心**：页面内 JS 自己滚（`el.scrollTop = scrollHeight` + 派发 wheel），
不是 Win32 滚轮 —— 所以**不抢焦点、不把窗口顶到最前**。

```
页面 JS 每 2.5s GET /__sphdl/batch?f=<有焦点>&h=<scrollHeight>
代理侧返回 '1'（授权滚动）或 '0' + 'r'（有新捕获→续跑）
```

**GUI 侧兜底**（v2.7.2 起）：页面 JS 12 秒无进展才补一发
`PostMessage(WM_MOUSEWHEEL)`，**不调 SetForegroundWindow、不移动光标**。

**到底判定演进**（三段教训）：
| 版本 | 判据 | 问题 |
|---|---|---|
| v2.7.3 | 连续 5 次无新增 | ❌ 重复条目/未滚到加载点就误判 |
| v2.7.4 | 页面 scrollHeight 不再增长，8 次（曾增长 +3） | ✅ |
| v2.7.5 | + 页面 JS 报 done 时兜底立即收手 | ✅ 不再白滚 |

### 15.4 日志洪水导致界面卡死（v2.7.5）

根因：`main.py` 在 GUI 模式强制 `logger.debug_enabled = True`，
每个 HTTP 请求一行 DIM（实测占日志 76%）→ Tk Text 控件被灌爆。

**修法**：
- `logger._emit` 里 `level == "dim"` 直接 return（**落盘保留，不推 GUI**）
- GUI 模式默认关 debug，需 `SPHDL_DEBUG=1` 或建 `开启调试日志.txt`

★ 判断开关文件**必须用 `config.BASE_DIR`**，不能用 `__file__`
（PyInstaller 打包后 `__file__` 指向解压临时目录 → 开关永远读不到）
