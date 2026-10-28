# Xteink device cloud protocol (unofficial)

非官方整理的「星曈 / Xteink」第三方设备云同步协议说明与可跑通的参考实现。
Unofficial notes and a working reference implementation for the Xteink third-party device
cloud sync API.

> 本项目不是官方 SDK：协议是通过分析公开可下载的第三方固件镜像得到的，可能随服务端更新失效。
> This is not an official SDK. The protocol was derived from a publicly downloadable third-party
> firmware image and may break when the service changes.

## 目录 / Layout

| 路径 | 内容 |
| --- | --- |
| `docs/protocol.md` | 端点、请求头、签名、二维码信封、任务流程、字段与错误码 |
| `docs/demo-readme.md` | 早期逆向记录与设备模拟说明（保留原始笔记） |
| `python/xteink_cloud.py` | 协议客户端：签名、信封、令牌、拉任务、回执 |
| `python/device_sim.py` | 设备模拟器：菜单、二维码、绑定轮询、下载导入 |
| `python/selftest_envelope.py` | 二维码信封自检（本地解密 + 复算签名） |

## 快速开始 / Quick start

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install cryptography

# 1) 自检信封实现
python python/selftest_envelope.py

# 2) 模拟一台设备：打印二维码并等待手机 App 扫码绑定，然后拉取任务
python python/device_sim.py --timeout 900
# 绑定成功后再发一本书，模拟器会下载、校验并导入它自己的 sdcard/books
python python/device_sim.py --watch --timeout 900
```

模拟器把设备身份、令牌和书籍都放在本地 `sdcard/` 目录，不会上传任何东西到设备。

## 已确认的事实 / Verified behaviour

- 签名与二维码信封在真实服务端和真实 App 上均验证通过（绑定、拉任务、下载、回执全链路）。
- 任务文件 URL 里的中文和空格必须按路径段百分号编码，否则多数 HTTP 客户端会拒绝。
- 回执必须逐条 `POST /tasks/<task_id>/complete`，批量端点不存在。

## 免责声明 / Disclaimer

- 仅供互通性研究与个人学习；不得用于绕过授权、批量注册或其它滥用。
- 协议常量来自公开固件镜像，服务端可能随时变更或回收权限。
- 与设备厂商、云服务提供方无任何关联，未获其背书。

## License

Apache-2.0，见 [LICENSE](LICENSE)。协议本身归其所有者。
