# 星曈（Xteink）第三方设备云同步协议

本文是逆向整理的记录，不是官方文档。所有结论都在真实服务端上验证过至少一次；服务端可能变更。

## 1. 基本信息 / Basics

| 项 | 值 |
| --- | --- |
| 服务地址 | `https://api-prod.xteink.cn` |
| 渠道 appid | `cl-20260508` |
| 握手密钥 | `3d3f2e6d-9745-79eb-ad19-9b33e5b4a424`（GUID 字符串，同时用于 HMAC 与 AES 密钥派生） |
| 传输 | HTTPS，JSON |
| 身份 | 请求头 `X-Chip-Id` / `X-Wifi-Mac` / `X-Bluetooth-Mac` |
| 分页 | `tasks/pull?limit=20` |

> 以上常量来自公开固件镜像中的字符串；不同固件/渠道可能不同。

## 2. 签名 / Signature

```
sign = hex(HMAC-SHA256(key = SECRET_ASCII,
                       msg = "appid.chip_id.bluetooth_mac.wifi_mac.timestamp.nonce"))
```

- `timestamp`：Unix 秒，十进制
- `nonce`：随机字节的 **小写 hex**，令牌请求用 16 字节（32 hex）
- 输出：小写 hex（64 字符）

实测：三处身份字段顺序固定为 `chip_id → bluetooth_mac → wifi_mac`；用 4 段或 base64 会得到
`401 签名校验失败`。

## 3. 绑定二维码信封 / Bind QR envelope

二维码内容是一段 JSON：

```json
{"appid":"cl-20260508","firmware_name":"CrossPoint-ESP32-4.2.2T",
 "payload":"<b64url>","iv":"<b64url>","tag":"<b64url>","sign":"<hex>"}
```

构造过程：

1. `key = SHA-256(SECRET_ASCII)`（32 字节）
2. `payload` 明文为设备 JSON，字段顺序与固件一致：
   `chip_id, bluetooth_mac, wifi_mac, device_type, brand, version, timestamp(number), nonce`
   — 二维码用 nonce 为 8 字节随机（16 hex）。
3. `iv` = 12 字节随机；AES-256-GCM 加密，输出密文与 16 字节 tag
4. `payload` / `iv` / `tag` 使用 **无 padding 的 base64url**（`-_` 字母表）
5. `sign = hex(HMAC-SHA256(SECRET_ASCII, "appid.payload_b64.iv_b64.tag_b64"))`
6. `firmware_name` 只作为展示/兼容字段；实测可改为自定义值（例如 `kiikoread-pico`）仍能绑定。

参考实现：`python/xteink_client.py` 的 `build_qr_envelope()`，自检：`python/selftest_envelope.py`。

## 4. 令牌 / Token

`POST /api/v1/third-party-device/token`

请求头：`Content-Type: application/json` + 三处身份头。

请求体：

```json
{"appid":"cl-20260508","timestamp":1793183000,"nonce":"<32 hex>","sign":"<hex>"}
```

响应：

- 未扫码：`200 {"success":true,"status":"pending","message":"等待用户扫码绑定","poll_interval":5}`
  — 用 `poll_interval` 秒轮询同一接口，直到拿到令牌。
- 已绑定：返回 `access_token` / `refresh_token`（JWT，`expires_in` 约 7 天）。
  令牌需要保存；过期后可用同样的签名请求重新换取（服务端对已绑定设备直接发牌）。
- 失败：`401 签名校验失败`、`400 芯片ID、蓝牙MAC和WiFi MAC不能为空`、`403 第三方渠道不存在或已禁用`。

## 5. 任务列表 / Pull tasks

`GET /api/v1/third-party-device/tasks/pull?limit=20`

请求头：`Authorization: Bearer <access_token>` + 三处身份头。

响应：

```json
{"success":true,"source":"cl-20260508","tasks":[
 {"task_id":"…","task_type":"download","command":0,
  "save_path":"/书名.epub",
  "file_url":"https://domestic-static-file.oss-cn-hangzhou.aliyuncs.com/uploads/book/…/书名.epub",
  "size":775636,"created_at":1793182818,"expires_at":1793442163,
  "cover_url":"https://…/cover.jpg","user_id":"…","points_source":"import"}]}
```

注意：

- `file_url` 里的中文与空格是**原始字符**，下载前必须按路径段做百分号编码（保留 `scheme://host` 与查询串）。
- `save_path` 的首段是设备侧期望文件名；用它而不是 URL 的 basename。
- 任务类型可能是 EPUB、TTF 字体或 TXT，按扩展名分流校验与落盘。

## 6. 回执 / Complete

`POST /api/v1/third-party-device/tasks/<task_id>/complete`

```json
{"success":true,"completed":true,"error_msg":"","retry_count":0}
```

响应回带完整 task（`status` 变为 `completed`）。批量端点 `/tasks/complete` 返回 `404`。

失败重试：`POST /api/v1/third-party-device/tasks/batch-redo`，body `{"task_ids":["…"]}`；
只有失败状态的任务能重入队，否则返回 `task_not_failed`。

## 7. 设备侧身份 / Device identity

固件从芯片 MAC 构造：

- `chip_id`：WiFi STA MAC 的 6 字节大写 hex（12 字符）
- `wifi_mac` / `bluetooth_mac`：`AA:BB:CC:DD:EE:FF` 形式
- `device_type`：机型名（例如 `pico`）
- `brand` / `version` / `firmware_name`：展示字段

## 8. 失败排查 / Troubleshooting

| 现象 | 原因 |
| --- | --- |
| `401 签名校验失败` | 消息拼接字段/顺序不对，或用了 base64 而非 hex |
| `400 …不能为空` | 身份只放在 body 里，没放请求头 |
| `403 第三方渠道…` | appid 不对 |
| 下载报 URL 含控制字符 | `file_url` 未做路径百分号编码 |
| 回执 404 | 用了批量端点，必须逐条 complete |
