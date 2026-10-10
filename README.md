# panbot —— 网盘图片每日分发工具

一条命令把一组图片批量上传到 **百度网盘 / 夸克网盘 / 迅雷云盘** 的指定目录，并自动创建 **分享链接**（含提取码），汇总输出到控制台、`links.md`、`links_log.csv`，并复制到剪贴板，拿去推广即可。

```
python -m panbot run --source D:\images\2026-10-05
```

## 为什么自己写

现成的开源工具各管一摊，没有正好覆盖"多盘上传 + 创建分享 + 每日一条命令"的：

| 工具 | 能做什么 | 缺什么 |
|---|---|---|
| [BaiduPCS-Go](https://github.com/qjfoidnh/BaiduPCS-Go) | 百度上传/下载/分享 | 只支持百度 |
| [QuarkPanTool](https://github.com/ihmily/QuarkPanTool) | 夸克转存/分享 | 不能上传本地文件、只支持夸克 |
| [quark-auto-save](https://github.com/Cp0204/quark-auto-save) | 夸克自动转存 | 只转存别人的分享 |
| [OpenList (Alist)](https://github.com/OpenListTeam/OpenList) | 挂载多网盘为文件系统 | 生成的是自身代理链接，不是网盘原生分享链接 |
| [cloud-auto-save-x](https://github.com/OzoO0/cloud-auto-save-x) / [urldb](https://github.com/ctwj/urldb) | 多盘自动转存分享 | 面向资源站，不做本地上传 |

panbot 把三家网盘的"上传 + 建目录 + 创建分享"封装成统一 driver，专为每日图片分发设计。

## 安装

```bash
cd D:\github\cloud-storage
pip install -r requirements.txt
copy config.example.yaml config.yaml
```

## 配置凭证（一次性，约 15 分钟）

编辑 `config.yaml`，按下面步骤获取三家的凭证。

### 百度网盘

上传用**官方开放平台**（稳定、合规），分享用浏览器 cookie（开放平台的分享接口是付费服务，个人不建议买）。

1. 打开 [百度网盘开放平台](https://pan.baidu.com/union) → 注册开发者（需实名）→ 创建应用，拿到 **AppKey / SecretKey**，记下**应用名**。
2. 把 `app_name`、`app_key`、`secret_key` 填入 config.yaml。
3. 运行授权命令，浏览器登录后把授权码粘贴回来：
   ```bash
   python -m panbot baidu-auth
   ```
   access_token 存到 `.panbot_state/baidu_token.json`，约 30 天有效，脚本到期自动续。
4. 获取 **BDUSS**（创建分享用）：浏览器登录 [pan.baidu.com](https://pan.baidu.com) → F12 → Application → Cookies → 复制 `BDUSS`（建议连 `STOKEN` 一起）填入 config.yaml。
5. 路径：`baidu.root` 默认 `"/"`（网盘根目录），`folder: "上传图/{name}"` 就落在 `全部文件/上传图/<名字>`。**只有沙箱应用才需要把 root 改成 `"/apps/<应用名>"`**——判断方法：如果 `python -m panbot check` 报“根目录 /apps/xxx 不可访问”，说明你的应用没有全盘权限；而在网盘里能看到自己的文件列表就说明有权限，直接用 `"/"` 即可。

### 夸克网盘

无官方开放 API，走网页版 cookie 接口（与 OpenList/QuarkPanTool 同源）：

1. 浏览器登录 [pan.quark.cn](https://pan.quark.cn)。
2. F12 → Network → 随便点一个请求 → Request Headers → 复制**整条 `Cookie:`** 值，填入 config.yaml 的 `quark.cookie`。
3. panbot 每次请求会自动滚动服务器下发的 `__puus/__pus` 并存到 `.panbot_state/quark_cookie.txt`，之后优先用滚动后的值，能显著延长 cookie 寿命。

#### 推广口令（中文口令 + `/~xxxxxxxxxx~:/`）

夸克的口令由官方服务 `utoken2.uc.cn/utoken/v2/create` 签发，请求必须带 `sign`，而 sign 由**夸克客户端的原生模块**计算（密钥藏在原生层，纯 Python 无法复刻）。所以 panbot 的做法是：通过 Electron 客户端的远程调试端口，在客户端内部完成签名和请求。

同一个接口按 `shareCode` 参数产出两种口令，panbot 两个都取：

| shareCode | 产出 | 例 |
|---|---|---|
| `auto3_c_code` | 12 个汉字的口令（手机 App 文案里那串） | `十二个汉字组成的口令串` |
| `quark_cloud_disk` | `/~xxxxxxxxxx~:/` 形式的英文口令 | `/~5e3b3bHjp4~:/` |

终端会单独打印一行 `口令: xxxxx`，同时输出的推广文案与手机 App 完全一致：

```
我用夸克网盘给你分享了「标题」，点击链接或复制整段内容，打开「夸克APP」即可获取。
汉字口令（12 个字）
/~xxxxxxxxxx~:/
链接：https://pan.quark.cn/s/xxxxxxxxxxxx
```

启用条件（config.yaml 的 `quark` 段）：

```yaml
quark:
  cmd_token: true              # 生成口令（默认开）
  client_exe: ""   # 夸克客户端 quark.exe 的完整路径（按实际安装位置填写）
  client_debug_port: 9222
  client_autostart: false      # 端口没开时是否自动带调试参数拉起客户端
```

要求夸克客户端**带调试端口运行**：`quark.exe --remote-debugging-port=9222`（客户端登录状态需有效）。若端口没开，panbot 会打印提示并跳过口令（分享链接不受影响）；把 `client_autostart` 设为 `true` 可让它自动拉起。

### 迅雷云盘

无官方个人 API，走网页版同款接口（与 cloud-auto-save-x 同源，config.example.yaml 里已内置网页版客户端参数）：

1. 浏览器登录 [pan.xunlei.com](https://pan.xunlei.com)。
2. F12 → Console（控制台）→ 输入下面这行并回车，复制输出的 refresh_token：
   ```js
   Object.keys(localStorage).filter(k=>k.startsWith('credentials_')).forEach(k=>console.log(JSON.parse(localStorage.getItem(k)).refresh_token))
   ```
3. 把 refresh_token 填入 config.yaml 的 `xunlei.refresh_token`。**注意 client_id 等参数必须用网页版那套**（模板里已写好：`Xqp0kJBXWhwaTpB6`、无 secret、`package_name: pan.xunlei.com`、固定签名三元组），不要改成安卓客户端的参数，否则 token 刷新会失败。
4. 脚本会自动刷新 access_token 并保存**轮换后的 refresh_token** 到 `.panbot_state/xunlei_token.json`（迅雷的 refresh_token 是一次一换的，之后以 state 文件为准）。

## 日常使用

```bash
# 先检查三家凭证是否都有效
python -m panbot check

# 演练一遍（不发任何请求）
python -m panbot run --dry-run

# 每日任务：上传 D:\images\xxx 到三家的 <date> 目录并创建分享
python -m panbot run --source D:\images\xxx
```

输出示例：

```
========== 今日分享链接 ==========
[baidu] /apps/我的应用/2026-10-05  提取码: k3x9
    https://pan.baidu.com/s/1AbCdEf...
[quark] 2026-10-05
    https://pan.quark.cn/s/abcd1234efgh
[xunlei] 2026-10-05
    https://pan.xunlei.com/s/VOxxxx?pwd=ef2k
==================================
（链接已复制到剪贴板）
```

同时追加写入 `links.md`（按日期分节）和 `links_log.csv`（Excel 可直接打开）。

### 每天定时自动跑

Windows 任务计划程序，或管理员 PowerShell 执行一次：

```powershell
schtasks /create /tn "panbot-daily" /tr "cmd /c cd /d D:\github\cloud-storage && python -m panbot run --no-clipboard >> panbot.log 2>&1" /sc daily /st 09:00
```

## 工作原理

| 网盘 | 认证 | 上传 | 分享 |
|---|---|---|---|
| 百度 | 开放平台 OAuth2（上传）+ BDUSS（分享） | xpan precreate → superfile2 分片 → create（4MB 分片，支持秒传） | `POST pan.baidu.com/share/pset` |
| 夸克 | 网页 Cookie（自动滚动续期） | /file/upload/pre → /file/update/hash 秒传判定 → OSS 分片 → commit → finish | /share → 轮询 /task → /share/password 换链接 |
| 迅雷 | refresh_token（自动轮换） | gcid 秒传判定 → S3 分片直传（boto3） | `POST /drive/v1/share`（自动获取 X-Captcha-Token） |

接口实现参考并致谢：
[OpenList](https://github.com/OpenListTeam/OpenList)（thunder / quark_uc driver）、
[BaiduPCS-Go](https://github.com/qjfoidnh/BaiduPCS-Go)（share/pset 参数）、
[QuarkPanTool](https://github.com/ihmily/QuarkPanTool)（夸克分享三步流）、
[urldb](https://github.com/ctwj/urldb)（迅雷分享参数）、
[quark-auto-save](https://github.com/Cp0204/quark-auto-save)。

## 离线自测

```bash
python tests/test_offline.py
```

## ⚠️ 风险与注意事项

- **夸克 / 迅雷走的是非官方接口**（两家均无个人开发者 API），存在被风控的可能：高频大批量上传、密集创建分享都可能触发验证甚至临时封禁。每天一组图片、间隔执行的正常使用一般没问题，但请自担风险，勿用于灰产批量拉新。
- **接口会随客户端版本变化**：迅雷的 `algorithms` 签名盐值、夸克的接口路径都可能在某次更新后失效。届时到 OpenList 仓库看最新 driver 源码，把新值填回 config.yaml 即可（迅雷盐值已做成配置项）。
- **百度分享接口**（share/pset）依赖 BDUSS，若返回 errno=-6 说明 cookie 过期，重新复制即可；分享接口高频调用可能触发安全验证，正常每日一次的量级问题不大。
- 凭证（cookie/token）等于账号登录态，**config.yaml 和 .panbot_state/ 不要提交到 git、不要发给别人**（.gitignore 已配好）。
- 请确保分享内容拥有合法版权，遵守各网盘用户协议。
