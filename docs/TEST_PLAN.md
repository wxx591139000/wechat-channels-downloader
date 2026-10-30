# 项目测试计划 TEST_PLAN — 微信视频号下载器 (sphdl)

> 归档日期：2026-08-25 ｜ 当前版本：v1.1.0

## 1. 测试范围与策略

| 层级 | 范围 | 策略 |
|------|------|------|
| 单元/离线 | parser（object_desc 解析）、decryptor（Isaac64）、downloader 续传逻辑 | 用真实 API 响应样例 + 真实加密视频样例做离线断言；不依赖网络/微信 |
| 集成 | 嗅探引擎（mitmproxy + addon）、证书/端口/代理设置 | `--selftest`：18899 测试端口起完整引擎，验证依赖/证书/监听 |
| E2E（真机） | 微信播放 → 捕获 → 下载 → 解密 → 可播放 | **用户操作 GUI + 助手结果侧监控**（downloads/ 新文件、logs/sphdl.log、端口、系统代理）；不用截图驱动 UI |
| 冒烟（冻结版） | 便携 exe 的 --selftest / --paste 字节级一致 / GUI 启动 / 单实例互斥 | 每次打包后必跑；结果记录于本节与交接文档 |

原则：
- 真机测试由用户亲自操作（「我来操作，遇到问题反馈你」），助手只监控结果侧
- 每次交付物变更（代码/打包）必须重跑冒烟；每次文档变更后归档需重打并回填 sha256

## 2. 测试用例清单（已执行 ✅ / 待执行 ❌）

### 2.1 离线/本地（`sphdl/tests/`，2026-08-24 全过，17 项）
| 用例 | 结果 |
|------|------|
| parser：真实 API 响应（decode_key=2136343393）解析出 url/size/key | ✅ |
| parser：snake/camel 双字段兼容 | ✅ |
| decryptor：真实加密样例解密 → ftyp 头（`00 00 00 20 66 74 79 70`） | ✅ |
| decryptor：与上游 WASM 路线结果一致性对照（算法同源） | ✅ |
| downloader：Range 断点续传（模拟 400KB 掐断） | ✅ |
| 双路线解密对照（Evil0ctal WASM vs 纯 Python Isaac64） | ✅ |
| **v1.2.1（08-25）** addon 活性/预检 8 项：真实嵌套 objectDesc 捕获 / 无 objectDesc 跳过解析（存活仍更新）/ 心跳路径 / 非 bridge 请求不受影响 / 206 流活动跟踪 / 首捕计数 / 重复去重 / JS 回调路径 | ✅ 全过（mitmproxy 11 合成 flow 单测） |

### 2.2 真机 E2E（PC 微信视频号，用户操作 + 助手监控）
| 用例 | 结果 |
|------|------|
| 08-24 晚：5 个视频 播放→捕获→下载→解密 全链路 | ✅ ftyp 头验证通过 |
| 08-25 早：2 个视频 全链路 | ✅ ftyp 头验证通过 |
| 捕获路径④（bridge 钩子，PC 微信主路径） | ✅ 日志坐实 |
| 捕获路径②（API 直嗅，浏览器备用路径） | ⚠️ 代码就绪，真机命中以路径④为主 |
| 图片帖（mediaType=9）捕获 + 🖼 前缀 + 下载 | ⚠️ 识别逻辑已实现；真机图片帖下载未单独留证 |
| 同一视频重放 → 去重（不重复下载） | ✅ 去重键=video_id 修复后验证（曾出现 4 份同文件，修复后不再复现） |
| 「停止下载」：下载中 ~1s 生效 / 排队中剔除 | ✅ |
| 引擎停止前已打开的页面 → 无钩子（预期失败）→ 重新进入后成功 | ✅（已写入 SOP/README FAQ） |

### 2.3 便携版冒烟（PyInstaller onedir，每次打包后跑）
| 用例 | v1.0（08-25 13:4x） | v1.1（08-25 14:2x） |
|------|------|------|
| `--selftest`（18899 引擎起停，banner 显示版本号） | ✅ | ✅（banner v1.1.0） |
| `--paste` 输出与开发版字节级一致 | ✅ | ✅（沿用 v1.0 验证 + 代码无下载路径变更） |
| GUI 启动（进程存活、欢迎日志、窗口标题带版本号） | ✅ | ✅ |
| 单实例互斥（第二实例弹「已在运行」，不抢端口/代理） | ✅ | ✅（第二实例停留对话框、无引擎/代理副作用） |
| 冻结布局 BASE_DIR 跟随 exe（config/日志/下载落 exe 目录） | ✅ | ✅ |
| 冻结 exe GBK stdout 打 emoji 不崩溃 | ✅ | ✅（reconfigure utf-8/replace） |
| 证书按钮 → _internal/install-cert.bat fallback | ✅（bdfedb2 修复） | ✅（spec 双份保留） |
| 重新下载按钮（v1.1 新）：已完成/失败/停止记录重新入队 | ⚠️ 按钮逻辑已实现并冒烟启动；**真实 CDN 重下覆盖旧文件**待真机留证 |

> **v1.2.1（08-25 18:1x）冒烟**：冻结 exe 从最终 zip 解包后 `--selftest` ✅（18899 起停、
> banner v1.2.1）；无参启动 → GUI 窗口（标题 v1.2.1，PrintWindow 截图渲染完整，
> 含状态栏「钩子」段）✅；单实例互斥沿用 v1.2 机制（未重跑，代码未变）。
> 开发版 GUI（--gui）状态栏「钩子: -」+ 布局视觉验证 ✅（docs/screenshots/02-v121.png）。

### 2.4 交付物合规（分享包，每次打包后跑）
| 用例 | 结果 |
|------|------|
| PII 扫描：用户名 / GitHub 账号 / 邮箱域 / 本机路径，全量含二进制 | ✅ v1.0、v1.1 均零用户命中（仅 3 个上游第三方二进制含 PyPI 构建机通用路径 `C:\Users\runneradmin`，非用户信息） |
| 包内无 logs/ downloads/ config.json records.json 等运行时产物 | ✅（v1.1 首打包曾混入 logs/downloads，已清理重打） |
| 包内含 使用说明.txt + install-cert.bat（%USERPROFILE% 无硬编码路径） | ✅ |
| **v1.2.1 zip（30.0MB）**：PII 扫描零用户命中（第三方 runneradmin 路径/CPython 致谢 gmail 同 v1.2 情况）+ testzip 完整 + 根目录三件套（exe/使用说明/install-cert.bat） | ✅ 08-25 |

### 2.5 列表上限 4 档 + 授权码（v2.0.17，新增，实测全过）

| 套件 | 项数 | 覆盖 | 结果 |
|------|------|------|------|
| `tests/verify_max_records.py` | A/B/C 段 | A1–A7 RecordStore（4 档满档拒收/降档裁剪/原子落盘/2万条性能/启动裁剪）；B1–B5 授权码（4 档往返/长度/有效期/篡改拒绝/无密钥无效）；C0–C9 GUI 端到端 | ✅ ALL PASS |
| `tests/verify_tier_reload.py` | D/E/P 段 52 项 | **D 段**满档→删记录→列表低于上限→继续加载（含循环可用性、门闩复位）；**E 段**换码立即生效（升级/降级/清除/无效码不变量，实测 12ms）；**P 段**满档不丢数据（捕获/重启/换码/删记录四路径 + 磁盘文件不删） | ✅ ALL PASS（连跑 3 次稳定） |
| 文案/文档一致性 | 29 项 | 4 档名在 config/license/gui/release/文档五处一致；无「不限量」残留；满档/删记录/换码文案齐备 | ✅ ALL PASS |
| 使用说明一致性 | 37 项 | 说明里每个按钮名/文案/承诺与代码逐项核对；授权码长度声明与实测一致 | ✅ ALL PASS |
| 容量阶梯压测 | 1/2/3/5 万条 | 插入耗时/落盘/records.json 体积/内存/启动加载/列表刷新/排序 | ✅ 实测（见 SPEC §4） |
| 授权自检 | — | `exe --selftest-license`（密钥注入 + 4 档往返 + 篡改拒绝） | ✅ ALL PASS |
| 交付码实测 | 4 个码 | 用户手上的 4 个码在打包 exe 的密钥体系下逐条校验 | ✅ 全部有效 |

**v2.0.17 回归复跑命令**（在 `sphdl/` 下）：
```
..\.venv\Scripts\python.exe tests\verify_max_records.py   # A/B/C 段
..\.venv\Scripts\python.exe tests\verify_tier_reload.py   # D/E/P 段
..\.venv\Scripts\python.exe tests\test_sniff_smoke.py     # 嗅探冒烟 15/15
..\.venv\Scripts\python.exe tests\test_offline.py         # 离线 11/11
..\.venv\Scripts\python.exe tools\license_tool.py selftest
```

**v2.0.17 冒烟实测**：冻结 exe `--selftest-license` ✅ / `--selftest-threaded` ✅；
解压 zip（1119 条目）后 exe 两项自检均通过；GUI 截图 + 本地 PaddleOCR 复核界面文案 ✅。

## 3. 已知测试缺口

1. **bridge 捕获无自动化 E2E**：依赖真实微信 + 页面注入，无法离线复现；每次微信大版本更新后需重跑真机链路
2. **重新下载的 CDN 过期边界**：链接过期后「重新下载」应失败且**保留旧文件**——失败路径有代码保障，真机留证未做
3. **图片帖真机下载**：mediaType=9 的完整下载（非视频流）未单独留证
4. **无网络/无 VPN 环境**：仅在有 Clash(7897) 的机器上测试；纯直连环境未验证（CDN 可达性应不受影响，但未测）
5. **长时运行**：连续多视频（>10）队列稳定性未压测
6. **杀毒误报**：本地代理工具被个别杀软误报属预期（README 已告知），未做签名/白名单

## 4. 回归触发条件

出现以下任一情况，重跑 2.2 真机 E2E + 2.3 冒烟：
- 微信客户端版本更新
- mitmproxy / 依赖升级
- addon.py / parser.py / downloader.py / decryptor.py 变更
- PyInstaller 版本变更或 spec 变更

## 6. v2.1.8 测试套件全景（21 套）

| 测试文件 | 项数 | 覆盖 |
|---|---|---|
| `test_sniff_smoke.py` | 15 | 嗅探冒烟（★最关键回归哨兵） |
| `test_offline.py` | 11 | 离线基础 |
| `verify_max_records.py` | — | 档位 + 授权 + GUI |
| `verify_tier_reload.py` | 52 | 删记录恢复 / 换码生效 / 不丢数据 |
| `verify_seen_release.py` | 21 | 删记录可重捕（v2.0.18） |
| `verify_batch_driver.py` | 27 | 批量驱动状态机（v2.0.18） |
| `verify_v218_delivery.py` | 42 | 交付前综合 |
| `verify_ledger.py` | 45 | 发码台账 |
| `verify_license_server.py` | 33 | 上报服务端 |
| `verify_telemetry_e2e.py` | 27 | 客户端→服务端端到端 |
| `verify_mp_article.py` | 33 | 公众号视频解析 |
| `verify_mp_gui.py` | 22 | 公众号 GUI 入口 |
| `verify_unified_list.py` | 29 | 统一清单（来源列/双击筛选） |
| `verify_quota_isolation.py` | 19 | 档位隔离 |
| `verify_tier_resolve.py` | 34 | 档位解析 + 授权分槽 |
| `verify_admin.py` | 36 | 手机发码后台 |
| `verify_device_bind.py` | 25 | 设备绑定 4 项取 2 |
| `verify_device_limit.py` | 21 | 设备数上限 |
| `verify_upgrade.py` | 32 | 改档位/续期 |
| `verify_customer_ops.py` | 27 | 客户管理（改名/作废/恢复） |
| **`verify_upgrade_keeps_devices.py`** | **9** | **★改档位必须保住台数（事故回归）** |

### 运行注意（血的教训）

⚠️ **`verify_mp_gui` / `verify_tier_reload` / `verify_max_records` 会自己删
`_lic_key.py`**（finally cleanup）→ 连跑多个需要密钥的测试时，
**每跑一个前都要重新导出一次**，否则第二个报"未注入授权密钥"。

⚠️ **跑 `verify_ledger` 前 `unset SPHDL_LEDGER`**（残留环境变量会让 L7a 假失败）。

⚠️ **改档位名后必跑**：`verify_upgrade` / `verify_customer_ops` / `verify_admin`
—— 这三套里有硬编码的档位名字符串。

⚠️ **新测试全绿 ≠ 没有回归**。新测试只覆盖新功能，必须跑全量既有测试。

### 截图核对脚本（人工/视觉模型复核，非断言）

```bash
tests/shot_unified.py      # 统一清单（来源列 + 双击筛选）
tests/shot_mp_button.py    # 公众号按钮（灰底🔒 / 橙底）
tests/shot_mp_window.py    # 公众号解析窗口
```

**截图后必须用视觉模型看**；小图标（🔒）要放大裁图才看得清。
**只适合判"有没有"，不适合判"合不合适"**（浓度/字号这类交给用户看）。

## 7. 测试套件全景（v2.1.8）

| 测试套 | 项数 | 覆盖 | 需要 _lic_key |
|---|---|---|---|
| `test_sniff_smoke` | 15 | ★嗅探核心回归哨兵（改任何核心代码必跑） | ✗ |
| `test_offline` | 11 | 离线基础功能 | ✗ |
| `verify_max_records` | ALL PASS | 满档/裁剪/原子落盘/2万条规模 | ✓ |
| `verify_tier_reload` | 52 | 换码立即生效 / 档位重载 | ✓ |
| `verify_seen_release` | 21 | 去重与删记录释放 | ✓ |
| `verify_batch_driver` | 27 | 批量驱动（失焦容忍/续跑/到底判定） | ✓ |
| `verify_v218_delivery` | 42 | 交付物完整性 | ✗ |
| `verify_ledger` | 45 | 发码台账 | ✓ |
| `verify_license_server` | 33 | 授权上报服务端 | ✓ |
| `verify_telemetry_e2e` | 27 | 设备指纹 + 上报端到端 | ✗ |
| `verify_mp_article` | 33 | 公众号文章视频解析 | ✗ |
| `verify_mp_gui` | 22 | 公众号 GUI 入口 | ✓ |
| `verify_unified_list` | 29 | 统一清单 + 来源筛选 | ✗ |
| `verify_quota_isolation` | 19 | 产品线配额隔离 | ✓ |
| `verify_tier_resolve` | 34 | ★档位解析（公众号码不能把上限变 0） | ✓ |
| `verify_admin` | 36 | 手机发码后台 | ✓ |
| `verify_device_bind` | 25 | 设备绑定 4 项取 2 | ✗ |
| `verify_device_limit` | 21 | 限台数（第 N+1 台拒绝） | ✗ |
| `verify_upgrade` | 32 | 改档位 / 续期 | ✓ |
| `verify_upgrade_keeps_devices` | 9 | ★改档位必须保住台数 | ✓ |
| `verify_customer_ops` | 27 | 改名 / 作废 / 恢复 | ✓ |
| **`verify_download_gate`** | **12** | **★未授权可捕获不可下载** | ✓ |
| **`verify_batch_resume_on_upgrade`** | **11** | **★满档停批量→升档自动恢复** | ✓ |
| **`verify_config_backup`** | **11** | **★配置备份/损坏恢复** | ✓ |
| **`verify_license_backward_compat`** | **31** | **★老码向后兼容（含台账真实码）** | ✓ |

### 7.1 归档前必跑（三条红线）

```bash
# 1) 交付物脱敏
python ~/.hermes/skills/项目归档/scripts/audit_delivery_pii.py <zip> <用户名> ...

# 2) ★已售凭据兼容性（发新版必跑，不能凭推理）
python tests/verify_license_backward_compat.py

# 3) 核心回归哨兵
python tests/test_sniff_smoke.py
```

### 7.2 运行注意

- 需要 `_lic_key.py` 的测试，先导出密钥（见 SKILL.md 的典型命令），跑完 `rm -f sphdl/_lic_key.py`
- **GUI 测试必须先屏蔽弹窗**，否则 `showwarning` 会卡住等点击：
  ```python
  import tkinter.messagebox as _mb
  _mb.showwarning = lambda *a, **k: None
  _mb.askyesno = lambda *a, **k: False
  ```
- 连跑多个测试时，前面的测试会清理 `_lic_key.py` → 后面的报"未注入密钥"。
  每个测试前重新导出密钥。

---

## 8. v2.2.x 新增测试套件

| 套件 | 项数 | 覆盖 |
|---|---|---|
| `verify_multiselect_hint.py` | **14** | 提示条存在/四种操作文案/全选反选按钮/selectmode/Ctrl-A 绑定/换行 |
| `verify_index_column.py` | **19** | 序号列位置/连续编号/排序后重编/**列偏移修复**/提示条含反选/列宽/values 对齐 |
| `verify_stop_sniff_keeps_download.py` | **6** | `_keep_tmp` 语义（主动停不保留/意外中断保留）/停嗅探检查任务/等下载完再停 |
| `verify_capture_order.py` | **14** | seq 字段/递增/删记录不重复/「序号」排序键=抓取顺序/持久化/老记录补号/诊断开关 |
| `verify_seq_on_delete.py` | **6** | 删部分不重复/清空重置为 1/清空重启从1/删除重启从max+1/序号列连续 |

### 关键：`verify_seq_on_delete` 是「先红后绿」

修复前 `_reset_seq_on_clear` 不存在 → AttributeError（红），
修复后 6/6（绿）。**这是 TDD 的正确姿势，能证明修的是真问题。**

### 测试环境注意事项（本项目特有）

1. **每个测试结束会删 `sphdl/_lic_key.py`** → 连跑时后续测试全崩。
   跑多个测试时每个之前都要重新生成：
   ```bash
   python -c "
   import sys, pathlib; sys.path.insert(0,'.')
   from sphdl import license as lic
   s = bytes.fromhex(pathlib.Path('.license_secret').read_text().strip())
   pathlib.Path('sphdl/_lic_key.py').write_text('K = %r\n' % lic.obfuscate(s), encoding='utf-8')
   "
   ```
2. **档位只由授权码决定**：配置里的 `default_max_records` 是 v2.0.17 遗留字段，
   **已不被读取**。测试要给真实授权码，否则档位=0、`fill_to_full` 填 0 条、
   断言在空列表上"通过"（v2.1.9 修过这个假通过）。
3. **真实记录会污染测试**：`records.json` 里有几百条历史记录时，
   测试插入的行会被挤走。断言用**相对变化**（如 `len(sel)==total`），
   或用独立临时目录。

---

## 9. 发码后台新增测试（2026-10-21/23）

| 套件 | 项数 | 覆盖 |
|---|---|---|
| `verify_share_parse.py` | **49** | 网盘分享文字解析（百度/夸克/UC/阿里/115） |
| `verify_snippets.py` | **24** | 话术库增删改、拼接、排序、脏数据 |
| `verify_mp_device_limit.py` | **14** | 公众号授权码的台数限制（生成/客户端/服务器/端到端） |
| `verify_unlicensed_capture_only.py` | **6** | 未授权：可捕获可看清单、不可下载 |

### verify_share_parse 的用例分组

| 组 | 内容 |
|---|---|
| P1 | ★百度标准格式（用户原话） |
| P2-P5 | 提取码单独一行 / 英文冒号+噪音 / 链接不带 pwd / 百度短链 |
| P6-P7 | 只有文件名 / 空与垃圾输入不炸 |
| **P10-P13** | **★夸克专项**（原话格式、无 pwd、链接后紧跟中文、百度+夸克混合） |
| **P14** | **★同版本双网盘并存**（百度+夸克各一份，同渠道覆盖） |
| P15 | 六种网盘域名识别 |
| P8/P9 | 页面元素 + JS 无双重反斜杠 + 服务端兜底 |

### 端到端验证（真服务器，不污染台账）

```bash
# 发一个测试码，检查结果页元素，然后清掉
curl -X POST -d "product=1&tier=500&customer=__test_snips__" .../gen
curl .../?code=$CODE | grep -q 'id="ver"'      # 版本下拉
curl .../?code=$CODE | grep -q 'copyBoth'      # 复制按钮
sudo grep -v "__test_snips__" licenses.jsonl > tmp && mv tmp licenses.jsonl
```
---

## 10. 评论 / 代理 / 配置迁移（v2.2.12 ~ v2.2.15 新增）

| 测试文件 | 项数 | 覆盖 |
|---|---|---|
| `verify_comment_extract.py` | 13 | 提取器对字段变体（驼峰/下划线）、单条、嵌套、JSON 字符串二次解码的容错；畸形数据拒绝；不误判视频数据 |
| `verify_comments_e2e.py` | 18 | 识别→去重→写入→落盘→**重启不丢**→弹窗内容完整 |
| `verify_comment_replies.py` | 16 | **嵌套回复**解析、多层对答、`count_tree` 计数、回复不被当根、拍平缩进、导出 json/txt |
| `verify_comment_download.py` | 39 | 拦截策略（含 9 个曾漏掉的域名）、重型媒体仍透传、**批量导出**、嵌套保留、文件名安全、无评论跳过 |
| `verify_real_size_display.py` | 9 | `≈` 前缀规则、加载时用磁盘修正、完成事件写真实值、文件丢失不崩 |
| `verify_bridge_diag.py` | 9 | bridge 全量诊断开关（定位未知接口） |
| `diag_capture_chain.py` | 7 | **端到端断点定位**：引擎起→bridge POST→解析→回调→进清单 |
| `verify_no_duplicate_helpers.py` | 11 | **模块级函数名禁止重名**、`_pick` 必须带 `*names`、真实 objectDesc 能解出视频 |
| `verify_sniff_passthrough.py` | 18 | 只拦微信、其余透传 + IPv6 修正（判据为「先排除后拦截」） |
| `verify_ipv6_netfix.py` | 6 | IPv6 不通时只回 IPv4 |
| `verify_auth_inherit_rule.py` | 13 | 压缩包默认不带授权、迁移时清授权保设置 |
| `verify_zip_clean_for_new_pc.py` | 11 | 模拟另一台电脑：包内无清单/授权/开发机标题 |
| `verify_config_migration.py` | 23 | 配置迁移、records.json 是数组不崩、启动兜底 |
| `verify_stop_and_license_gate.py` | 10 | `cancel_all` 清队列、自动下载必须过授权门禁 |

### 两个「诊断型」测试的用法（不是回归，是排障）

```bash
# 抓不到视频时：定位断在哪一段
python tests/diag_capture_chain.py

# 不知道某数据类型走哪个接口时：开诊断抓 bridge
#   在程序目录建 开启原始数据记录.txt → 复现 → 看 diag_bridge/bridge_calls.jsonl
```
---

## 11. 授权 / 吊销 / CA / 列表列（v2.2.17 ~ v2.3.1）

| 测试文件 | 项数 | 覆盖 |
|---|---|---|
| `verify_revocation_safety.py` | 29 | **★失联绝不降档**：404/500/HTML/ok:false/畸形/超时/无缓存/缓存过期/无 verified 标记全部不降档；降档可逆 |
| `verify_revocation_plan_b.py` | 25 | 端到端：真实服务器 + 客户端 → 命中/缓存/降档/停任务/弹窗 |
| `diag_void_license_effect.py` | — | **诊断型**：证明改造前"作废对客户端无效"（纯本地校验、无下发通道） |
| `verify_clear_license.py` | 16 | 「清除授权」按钮：4 个字段全清、磁盘无残留、重启不复活、单产品线隔离 |
| `verify_ca_install_fix.py` | 18 | bat 换行符 CRLF、`.gitattributes`、打包自检、ca_install 幂等 |
| `verify_ca_nowindow_and_hide_seq.py` | 24 | 证书零窗口（CREATE_NO_WINDOW）、序号列隐藏、`_col_index` 不错位 |
| `verify_cert_prompt_visibility.py` | 24 | 提示框醒目度：20pt 红色加粗、黄底强调条、失败含手动步骤 |
| `verify_capture_watchdog.py` | 22 | **★抓不到看门狗**：4 个计数器、3 种判定分支、诊断框文字正确 |
| `verify_hide_size_column.py` | 16 | 大小列隐藏、values 仍 8 字段、`Record.size` 保留 |
| `verify_sop_pdf_in_package.py` | 16 | SOP PDF 在包内顶层、与源文件逐字节一致 |

### 关于「误报」的判定

`precheck_git_leak.py` 会按文件名关键词把 `tests/verify_clear_license.py`、
`tests/diag_void_license_effect.py` 标成"密钥类文件"——**这是误报**。
它们只是**运行时读取** `.license_secret` 来生成测试码，
不含硬编码密钥、不含真实授权码、不含 PII。判定方法：

```bash
grep -nE "bytes\.fromhex\(.{[20,}|[0-9a-f]{40,}" tests/*.py   # 无硬编码密钥
grep -oE "SPHDL-[0-9A-Z-]{20,}" tests/*.py | grep -v TEST       # 无真实码
```

---

## 12. 本轮新增测试（v2.5.0 ~ v2.6.4，2026-10-28）

| 测试 | 项数 | 覆盖 |
|---|---|---|
| `verify_video_stats.py` | 59 | 统计字段提取（扁平/下划线/嵌套/JSON串）、畸形值、显示格式、只显示点赞列 |
| `verify_stats_real_sample.py` | 19 | ★用客户真实 payload 固件核对（135/94/0/527 等逐值一致） |
| `verify_like_capability.py` | 43 | 能力位生成/识别/叠加、篡改拒绝、★ver=1/2/3/4 老码回归、UI 三态 |
| `verify_admin_caps.py` | 22 | 后台文案格式 6 种、★后台生成→客户端解析、设备绑定+caps 拒绝 |
| `verify_douyin_window.py` | 24 | ✕ 不中断任务、最小化/最大化、销毁后回填安全 |
| `verify_douyin_batch.py` | 53 | 链接解析 8 形态、字段解析、bit_rate 降级、403 不留残文件 |
| `verify_douyin_ratelimit.py` | 39 | 五层限流对策参数、缓存过期、批量上限、冷却 |
| `verify_douyin_dash_merge.py` | 19 | ★DASH 音视频分离 → ffmpeg 合并 → ffprobe 确认有音轨 |
| `verify_sop_pdf_in_package.py` | 16 | SOP PDF 随包、逐字节一致 |
| `verify_cap_checkbox_visual.py` | 20 | ★真实浏览器点击，抽线上真实 CSS 验证 ✓ 出现 |
| `verify_license_page_features.py` | 16 | 授权管理页四项状态 + 叠加识别 + 可点击 |
| `verify_admin_routes_smoke.py` | 26 | ★全路由冒烟（9 GET + 5 页面 + 发码结果页） |
| `verify_license_backward_compat.py` | 4 | ★★已售码兼容性审计（22 组合 + 46 个真实台账码逐个校验） |

### 本轮新增的两条「防假绿」原则

**① 验证样式/页面时，必须确认被测的是同一份源码**

反例：`verify_cap_checkbox_visual` 第一版按 `<style>` 抽 CSS，但真实 CSS 是
模块常量 `CSS = ...`（三引号字符串）在 `page()` 里用 `{CSS}` 插入 ——
抽到空串，页面完全没样式，于是「appearance=auto」白白通过（没 CSS 当然不是 none）。

做法：抽真实常量，并断言「抽到的 CSS 长度 > 2000 且含关键规则」。

**② 改后台必须跑路由冒烟，不能只测函数**

反例：只测了 `code_label()` 函数（22 项全绿），但 `_page_new` 里
`NameError: tier` 导致页面 500 → 生产 Cloudflare 502。

做法：`verify_admin_routes_smoke.py` 进程内起服务，逐个打所有路由。

### 已售凭据兼容性审计（发版前红线）

`verify_license_backward_compat.py` 三类断言：

1. 每种码型（ver=1/2/3/4/5）× 每种档位全组合 → 22 组
2. ★真实台账（生产 licenses.jsonl）里每个码逐个校验
3. 台账记录的 tier/product/caps 与码内解析结果一致

本轮实测：22/22 组合有效；台账 46 个码全部有效、档位/产品/caps 全部一致
（码型分布 ver2 x2 / ver3 x2 / ver4 x41 / ver5 x1）。

---

## 13. 本轮新增测试（v2.5.0 ~ v2.8.1）

| 测试文件 | 项数 | 覆盖 |
|---|---|---|
| `verify_video_stats.py` | 59 | 点赞/转发/推荐提取 + 显示格式 + 排序键为原始整数 |
| `verify_stats_real_sample.py` | 19 | **真实抓包固件**验证统计字段位置（objectDesc 兄弟键） |
| `verify_like_capability.py` | 43 | ver=5 能力位：叠加/篡改/MAC/未知能力位拒绝 |
| `verify_license_page_features.py` | 16 | 授权管理页四项功能状态（含叠加识别） |
| `verify_cap_checkbox_visual.py` | 20 | **真实浏览器**点击复选框，像素差异验证 ✓ 画出来 |
| `verify_admin_routes_smoke.py` | 26 | 发码后台全部路由 200（含 `/?code=` 结果页） |
| `verify_admin_caps.py` | 22 | 后台勾选 → 生成 ver=5 码 → 客户端可解析 |
| `verify_douyin_window.py` | 24 | ✕=隐藏不销毁 / 最小化最大化 / 登录态 |
| `verify_replay_bulk_capture.py` | 12 | **真实回放 payload 固件**：整页 8 条全抓 |
| `verify_batch_unified.py` | 26 | 删自动翻页按钮；兜底代码段 SetForegroundWindow 调用 0 次 |
| `verify_batch_stop_guard.py` | 23 | 断网停止 / 到底止损 / 有新增归零 idle |
| `verify_bottom_detect.py` | 25 | 5 个逻辑场景（含**断续增长**复现客户误停） |
| `verify_freeze_fix.py` | 17 | dim 不入 GUI（实跑验证） / batch_done_at 收手 |
| `verify_miniapp_save.py` | 16 | 边播边存：206 乱序三片拼装 / 未满只留 .part |
| `verify_miniapp_location.py` | 18 | 落盘到用户配置目录 / 回调入清单 |
| `verify_miniapp_naming.py` | 14 | out_dir 注入位置 / 第01集_ 命名 / 同集不增序号 |
| `verify_popup_once.py` | 14 | 弹窗只弹一次 / 抓到后重置 / 窗口去重 |
| `verify_license_backward_compat.py` | 4+ | ★发版红线：22 组码型 + 真实台账逐个校验 |
| `verify_scroll_e2e.py` | 6 | （v2.7.1 用，后并入批量）真实 Chromium 滚轮端到端 |

### 本轮的测试教训（三次"假绿"）

| # | 现象 | 真因 |
|---|---|---|
| 1 | 勾选框 20/20 全过，页面却没 ✓ | 按 `<style>` 抽 CSS，真实 CSS 是模块常量 → 抽到空串，页面裸奔 |
| 2 | 后台 22/22 全过，页面 500 | 只测函数，没测「页面能不能打开」 |
| 3 | 滚轮 29/29 全过，客户零效果 | 单测用注入的假 `scroll_fn`，滚轮到底有没有落到窗口**测不出来** |

**对策（已固化）**：
- 涉及样式的测试必须断言「抽到的 CSS 长度 > N 且含关键规则」
- 改后台必须跑**路由冒烟**（起真实服务逐个打）
- 涉及系统交互（滚轮/窗口）必须**真实浏览器/窗口端到端**，读页面状态判定
- 涉及真实数据的解析，固件必须是**客户真实抓包**（脱敏），不能手搓

### 数据形态覆盖教训

v2.7.3 用「连续 N 次无新增」判到底，只测了「有新增/无新增」两个状态。
真实分页是**断续增长**（一屏重复条目后突然加载一批）→ 客户 118 条被误停。

**对策**：`verify_bottom_detect.py` 用 5 个场景模拟，
其中「断续增长」专门复现客户现场。
