# 星曈云同步 · 设备模拟器 / Xteink cloud sync · device simulator

中文：在电脑上模拟设备，走真接口跑「导入图书 → 云同步」。签名算法从 CrossLink
pixel-pico 固件逆向得到并已用真接口验证（返回 `200 pending`）。
English: A PC-side device simulator for the "import books → cloud sync" flow against
the real API. The signature was reverse-engineered from the CrossLink pixel-pico
firmware and verified against the live endpoint (`200 pending`).

## 跑 / Run

```sh
cd build-local/xteink-device-demo
python device_sim.py --once      # 只申请绑定并显示二维码 / bind request + QR only
python device_sim.py             # 绑定后拉任务、下载、导入、回报 / full flow
python device_sim.py --timeout 600 --sdcard D:\demo-sd
```

模拟的 TF 卡目录：

| 路径 / Path | 用途 / Role |
| --- | --- |
| `<sdcard>/.readpico/cloud/xteink_sync.json` | 设备身份、令牌、已导入记录 / identity, tokens, imports |
| `<sdcard>/xteink/` | 云端下载暂存区 / cloud staging |
| `<sdcard>/books/` | 书架（导入落点）/ shelf |

## 已确认的协议 / Verified protocol

```
POST https://api-prod.xteink.cn/api/v1/third-party-device/token
头 / headers: X-Chip-Id, X-Wifi-Mac, X-Bluetooth-Mac, Content-Type: application/json
体 / body:   {appid, timestamp, nonce, sign}
sign = hex(HMAC-SHA256(key=GUID, msg="appid.chip_id.bluetooth_mac.wifi_mac.timestamp.nonce"))
appid  = "cl-20260508"
GUID   = "3d*****24"   # 仓库不保存全文，从固件镜像提取 / extract from firmware
nonce  = 32 位小写 hex（16 字节随机数）
```

命中真接口的返回：

```json
{"success": true, "status": "pending", "message": "等待用户扫码绑定", "poll_interval": 5}
```

要点 / Notes:
- 身份只放 HTTP 头；只放 body 会返回 `400 芯片ID、蓝牙MAC和WiFi MAC不能为空`。
- 签名原文用 `.` 连接，顺序固定：`appid → chip_id → bluetooth_mac → wifi_mac → timestamp → nonce`。
- `timestamp` 为秒级 Unix 时间；`sign` 为小写 hex（不是 base64）。

## 二维码信封 / QR envelope（已实现）

固件 `0x420ce758` 的二维码不是明文，而是 AES-GCM 信封：

```
key     = SHA256(GUID 字符串)                       # 32 字节
iv      = 12 字节随机
payload = AES-256-GCM(设备 JSON)，tag 16 字节
编码    = payload/iv/tag 均为无 padding 的 base64url
sign    = hex(HMAC-SHA256(GUID, "appid.payload.iv.tag"))
```

设备 JSON（解密后，字段顺序与固件一致）：

```json
{"chip_id":"…","bluetooth_mac":"…","wifi_mac":"…","device_type":"pico",
 "brand":"kiikoread","version":"0.1","timestamp":1793183000,"nonce":"0011223344556677"}
```

二维码内容（扫描到的字符串）：

```json
{"appid":"cl-20260508","firmware_name":"CrossPoint-ESP32-4.2.2T",
 "payload":"…","iv":"…","tag":"…","sign":"…"}
```

自检：`python selftest_envelope.py`（本地解密 + 复算签名）。

## 已实测通过 / Verified end-to-end

- [x] 真 App 扫描 `qr-bind.png` → 设备绑定成功，拿到 JWT（access + refresh，有效期 7 天）。
- [x] 本机从 App 发书 → `tasks/pull` 收到任务 → 下载（中文/空格 URL 正确编码）→
      EPUB CRC/mimetype 校验 → 导入 `sdcard/books` → `complete` 回执 `200 success=True`。
- [x] 重复发送同一本书时按 `task_id` 去重，本地文件名自动加序号。

实测任务字段（`task_type=download`）：

```json
{"task_id":"…","task_type":"download","command":0,
 "save_path":"/岩田先生…epub","file_url":"https://domestic-static-file.oss-cn-hangzhou.aliyuncs.com/uploads/book/…/岩田先生…epub",
 "size":775636,"created_at":…,"expires_at":…}
```

回执：`POST /api/v1/third-party-device/tasks/<task_id>/complete`
body `{"success":true,"completed":true,"error_msg":"","retry_count":0}`（批量 `/tasks/complete` 返回 404）。
失败重试另有 `POST /api/v1/third-party-device/tasks/batch-redo`（仅失败状态可重入队）。

> 真机需要注意：`file_url` 里的中文和空格必须按路径段百分号编码，否则 HTTP 客户端拒绝；
> 导入时用 `save_path` 的 basename 作为书架文件名。

## 固件移植映射 / Firmware port mapping

目标：书架 → 导入图书 增加第 5 个入口「云同步」（现有四项：浏览 TF 卡/WiFi 传书/热点传书/USB 读卡）。

| 模拟器 | 固件落点 / Firmware |
| --- | --- |
| `xteink_client.sign()` | 新模块 `main/book/book_xteink_sync.c`，用 mbedtls `mbedtls_md_hmac`（SHA-256）+ hex 编码 |
| 身份字段 | `esp_read_mac`（WiFi/BT）+ eFuse MAC 派生 chip_id；`device_type` 由板级配置给出 |
| 状态文件 | TF：`/.readpico/cloud/xteink_sync.json`（令牌只存本机） |
| 下载与导入 | 先落 `/books/.incoming`，EPUB CRC/mimetype 校验后 `rename` 到 `/books`，复用 `book_store` 重扫 |
| 线程与取消 | 参照 `main/ota_online.c`：独立 worker + 可取消 + 屏刷互斥；离页/锁屏前 join |
| 网络 | 复用 `read_pico_transfer` 的 WiFi 连接与 `esp_crt_bundle` 校验；离线/无网给出明确提示 |
| UI | `main/apps/app_book.c` 的 `draw_import()` 加第 5 项；绑定页复用 `ui_wifi_qr` 画二维码；进度页显示「等待扫码/正在拉取/正在下载/导入完成/失败」 |

安全 / Security:
- 只连 `https://api-prod.xteink.cn`，禁止明文 HTTP 和任意镜像。
- 令牌只存 TF 卡本地，日志不打印 token/sign 全文。
- 下载文件先写临时文件并校验，失败绝不上架。
