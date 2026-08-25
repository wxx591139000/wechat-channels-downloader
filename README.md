# 微信视频号下载器 (sphdl)

一键下载微信「视频号」加密视频，并自动解密为可播放的 MP4（Windows 10/11）。

```
微信里播放视频 → 本地代理嗅探到 (url + decode_key)
→ 单连接顺序下载加密文件（断点续传）→ Isaac64 解密前 128KB → 保存到 downloads/
```

> ⚠️ **声明**：本工具仅供个人学习研究与备份自己有权访问的内容使用。
> 请勿用于商业用途或批量抓取，由此产生的一切后果由使用者自负。
> 技术原理基于公开资料（视频号视频前 131072 字节为明文，其余与
> `Isaac64(decode_key)` 密钥流 XOR；url 与 decode_key 须来自同一次 API 响应）。

---

## 功能特性

- 🖥 **tkinter 图形界面**：一键启动/停止嗅探引擎，列表实时状态（待下载/排队中/下载中/解密中/已完成/失败/已停止）
- 🎯 **捕获与下载分离**：嗅探到的视频只进列表（状态「待下载」），**不会无脑全量下载**；多选后点「⬇ 下载选中」或双击该行才入队
- ⏸ 下载中可「停止下载」（~1s 生效，连接级中断），排队中的可直接剔除
- 🔁 **断点续传**：CDN 每连接约 400KB 会掐断，自动 Range 续传（≤8 轮），120s 读超时保护
- ↻ **重新下载**：「已完成/失败/已停止」的记录可重新入队，成功后覆盖旧文件（链接过期需重新嗅探）
- 🎭 **微信 UA 加速**：捕获到微信 webview UA 自动用于下载（CDN 对微信 UA 约 50-85KB/s，非微信 UA 仅 10-20KB/s）
- 🖼 图片帖（mediaType=9）自动识别，标题加 🖼 前缀
- 🔑 **去重**：按 video_id(md5sum) 去重，同一视频重放（URL/token 会变）不会重复下载
- 💾 列表与配置持久化（records.json / config.json），重启不丢
- 🩺 **自检**：`--selftest` 一键验证依赖/证书/端口
- 📦 **免安装便携版**：zip 解压即用，配置/日志/下载全部跟随 exe，整个文件夹拷走就能用
- 🔒 Windows 单实例互斥，防止多开抢端口/抢系统代理
- ↩ **恢复网络代理**（应急按钮）：停止嗅探 + 按快照还原系统代理（原 VPN 还原 VPN、原无代理则关闭）
- 🖱 **双击 exe 即 GUI**：无参数启动一律弹图形界面（v1.2 修复无参进嗅探模式的断网事故）

## 捕获原理（两条路径，任一命中即可）

1. **④ bridge 钩子（主路径）**：视频号网页 HTML（`channels.weixin.qq.com/web/pages/home`）
   走网络时可被代理注入 `WeixinJSBridge.invoke` 包装器；PC 微信内置视频号的
   objectDesc 走原生 IPC 回调（网络不可见），钩子把它截获后 POST 回代理本身
   （`/__sphdl/bridge`），由插件解析出 url + decode_key。
2. **API 直嗅（备用路径）**：直接嗅探含 `object_desc` 的 JSON 响应（浏览器网页版路径）。

## 快速开始

### 方式 A：免安装便携版（推荐，无需 Python 环境）

1. 下载 `dist/微信视频号下载器-v1.2-win64-portable.zip`，解压到任意目录
   （建议路径不含中文/空格，如 `D:\sphdl\`）
2. 双击 `微信视频号下载器.exe`
3. 首次使用先装 CA 证书（见下节）
4. 点「启动嗅探」→ 打开微信视频号播放视频 → 列表里选中要下的 → 「⬇ 下载选中」

便携版所有产物都落在 exe 同级目录：`downloads/`（视频）、`logs/sphdl.log`（日志）、
`config.json` / `records.json`（配置与列表）。整个文件夹拷走即迁移。

### 方式 B：开发版（需要 Python 3.10+）

1. 安装 [Python 3.10+](https://python.org)（勾选 "Add to PATH"）
2. 双击 `run.bat`（首次自动建 `.venv` 并装依赖，约 1-2 分钟）

## 安装 CA 证书（一次性，仅嗅探模式需要）

工具通过本地代理（127.0.0.1:8899）拦截 HTTPS 流量，系统必须信任 mitmproxy 的 CA：

**GUI 一键**：界面顶部「安装证书」按钮会直接唤起 Windows 证书安装向导。

**手动**：
1. 先运行一次工具（生成证书到 `C:\Users\<你>\.mitmproxy\`）
2. PowerShell 执行：
   ```powershell
   certutil -addstore -user Root "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
   ```
3. 或双击 `mitmproxy-ca-cert.p12` → 导入到「受信任的根证书颁发机构」（密码 `mitmproxy`）

## 使用流程（GUI）

1. 点「**启动嗅探**」（可选勾选"自动设置系统代理"）
2. **重新进入**微信视频号页面（引擎启动前已打开的页面没有注入钩子，见 FAQ）
3. 播放视频 → 列表出现「待下载」条目（标题、大小、时间）
4. 选中一条或多条 → 点「**⬇ 下载选中**」（或双击某行）
5. 下载+解密完成后状态变「已完成 ✓」，文件在 `downloads/`
6. 用完后点「停止嗅探」，系统代理自动恢复

### CLI 模式

```bat
:: 嗅探模式（自动设置系统代理，Ctrl+C 退出并恢复代理）
run.bat
run.bat --no-auto-proxy          :: 不自动改系统代理
run.bat -p 9999                  :: 换端口

:: 手动模式：已有 object_desc JSON（抓包/TikHub 等），不需要代理
run.bat --paste response.json
run.bat --paste-text "{\"media\":[{\"url\":\"...\",\"decodeKey\":123,\"fileSize\":999}]}"

:: 自检（依赖/证书/端口）
run.bat --selftest
```

## 常见问题

**Q: 播放了视频但列表没动静？**
- 引擎启动**之前**已打开的视频号页面没有注入钩子 → 退出视频号重新进入（或重启微信）再播
- 看 `logs/sphdl.log`：有 `📡` 流量日志说明代理在工作；完全没有 → 系统代理没生效
  （其他代理软件/Clash TUN 模式会抢流量，测试期间请暂停）
- 已缓存的旧视频可能不再产生新 API 请求，换一条没播过的视频

**Q: 下载很慢？**
CDN 对单连接限速（约 400KB 一掐 + 微信 UA 50-85KB/s），这是上游限制，工具已用
断点续传最大化利用单连接。10MB 视频约 2-5 分钟属正常。

**Q: 停止后浏览器/微信报代理错误？**
引擎停止瞬间 Chromium 系应用可能缓存死代理，重启微信/浏览器即可。

**Q: 多开多个窗口会怎样？**
新版有单实例互斥：第二个实例会弹「已在运行」提示并退出。

## 目录结构

```
├── 微信视频号下载器.exe 相关        dist/微信视频号下载器/   免安装便携版（zip 内）
├── main.py                         入口（GUI/嗅探/手动/自检）
├── run.bat                         开发版启动器（自动建 venv）
├── install-cert.bat                证书安装辅助
├── requirements.txt                依赖（mitmproxy 11 + requests + Brotli）
├── sphdl/                          核心包
│   ├── addon.py                    mitmproxy 插件（bridge 钩子注入 + API 直嗅）
│   ├── parser.py                   object_desc 解析（snake/camel 双兼容）
│   ├── downloader.py               单连接顺序下载 + 断点续传
│   ├── decryptor.py                Isaac64 前 128KB 解密
│   ├── pipeline.py                 下载任务队列
│   ├── gui.py                      tkinter 界面 + 单实例互斥
│   ├── proxy_manager.py            系统代理设置/恢复 + 证书检测
│   └── ...
├── tests/                          离线/本地冒烟测试
└── 微信视频号下载器.spec            PyInstaller 打包配置
```

## 重新打包（开发者）

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --clean 微信视频号下载器.spec
:: 产物: dist\微信视频号下载器\  （exe + _internal + install-cert.bat）
```

## 版本

当前版本：**v1.2.0**

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.2.0 | 2026-08-25 | 修复双击 exe 无参数直接进嗅探模式的断网事故（现双击必弹 GUI）；新增「↩ 恢复网络代理」应急按钮（停引擎 + 按快照还原代理） |
| v1.1.0 | 2026-08-25 | 新增「↻ 重新下载」按钮；窗口标题/CLI 显示版本号；交付物脱敏并附使用说明.txt |
| v1.0.0 | 2026-08-25 | 首个版本：GUI 捕获/多选下载分离、bridge 钩子 + API 直嗅双路径、断点续传、免安装便携版 |

## 已知限制

- 仅 Windows（依赖 Windows 系统代理注册表 + tkinter）
- 微信版本更新可能改变 objectDesc 结构/IPC 通道，需跟进
- 视频号部分视频可能走纯本地缓存（无任何网络请求），此类无法捕获
