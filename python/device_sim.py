"""
SPDX-FileCopyrightText: 2026 mindreset
SPDX-License-Identifier: Apache-2.0

中文：在电脑上模拟「墨水屏设备」跑通 导入图书 → 云同步 的完整流程：
      绑定（二维码/轮询）→ 拉任务 → 下载 → 校验 → 导入书架 → 回报。
English: PC-side device simulator for the "import books → cloud sync" flow.

冻结 / Frozen:
- 只模拟设备侧；不伪造云端响应，所有请求都发给真接口。
- 校验失败的文件不进入书架目录。
- Device side only; every request goes to the real endpoint. Failed
  verification never lands on the shelf.
"""
import argparse
import json
import os
import secrets
import sys
import time
import zipfile

# Windows 控制台默认 GBK，统一转 UTF-8，二维码才能正常输出。
# Windows consoles default to GBK; force UTF-8 so the ASCII QR renders.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xteink_client as cloud


def load_state(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"identity": cloud.new_identity(), "access_token": "", "refresh_token": "",
            "access_expires_at": 0, "imported": []}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def show_import_screen():
    print("""
┌──────────────────────── 导入图书 ────────────────────────┐
│ 选择导入方式                                              │
│   1) 浏览 TF 卡     查看目录，打开或管理已有图书           │
│   2) WiFi 传书      手机与墨水屏连接同一网络               │
│   3) 热点传书       连接 kiikoread 热点后上传图书          │
│   4) USB 读卡       连接电脑，把图书放入 books 文件夹      │
│   5) 云同步         ← 星曈云端：绑定后拉取云端下发的图书   │
└────────────────────────────────────────────────────────┘
""")


def print_qr(text, png_path, ascii_art=False):
    print("[云同步] 二维码内容 / QR payload:")
    print(text)
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(png_path)
        print("[云同步] 二维码图片 / QR image: %s" % png_path)
        if ascii_art:
            qr.print_ascii(invert=True)
    except ImportError:
        print("[云同步] 未安装 qrcode，跳过图片 / qrcode not installed")


def wait_bound(client, state_path, state, timeout=300):
    """轮询 token 接口直到绑定成功。/ Poll the token endpoint until bound."""
    deadline = time.time() + timeout
    interval = 5
    while time.time() < deadline:
        status, payload = client.request_token()
        if status == 200:
            tokens = cloud.find_token(payload)
            if tokens.get("access_token"):
                state.update(tokens)
                now = time.time()
                state["access_expires_at"] = now + float(tokens.get("expires_in", 3600))
                save_state(state_path, state)
                print("[云同步] 绑定成功，已保存令牌 / device bound, tokens stored")
                return True
            print("[云同步] 状态：%s（%s）" % (payload.get("status", "?"), payload.get("message", "")))
            interval = int(payload.get("poll_interval", interval) or interval)
        else:
            print("[云同步] 请求失败 HTTP %d %s" % (status, json.dumps(payload, ensure_ascii=False)[:160]))
            return False
        time.sleep(interval)
    print("[云同步] 绑定超时 / bind timeout")
    return False


def verify_epub(path):
    if not path.lower().endswith(".epub"):
        return True
    with zipfile.ZipFile(path) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("EPUB CRC 校验失败")
        if "mimetype" not in zf.namelist() or zf.read("mimetype") != b"application/epub+zip":
            raise RuntimeError("EPUB mimetype 不正确")
    return True


def import_to_shelf(path, shelf_dir, state):
    os.makedirs(shelf_dir, exist_ok=True)
    stem, suffix = os.path.splitext(os.path.basename(path))
    target = os.path.join(shelf_dir, os.path.basename(path))
    index = 2
    while os.path.exists(target):
        target = os.path.join(shelf_dir, "%s (%d)%s" % (stem, index, suffix))
        index += 1
    with open(path, "rb") as src, open(target + ".part", "wb") as dst:
        dst.write(src.read())
    os.replace(target + ".part", target)
    state.setdefault("imported", []).append({"file": os.path.basename(target), "at": int(time.time())})
    return target


def sync_once(client, token, sdcard, state, state_path):
    staging = os.path.join(sdcard, "xteink")
    shelf = os.path.join(sdcard, "books")
    pending, tasks = client.pull(token)
    print("[云同步] 待处理任务 %d 个 / pending tasks: %d" % (pending, pending))
    done = set(state.get("done_tasks") or [])
    for task in tasks:
        task_id = task.get("task_id", "?")
        if task_id in done:
            print("[云同步] 任务 %s 已导入过，跳过" % task_id)
            continue
        print("[云同步] 任务 %s type=%s name=%s" % (task_id, task.get("task_type"), task.get("save_path")))
        try:
            path = client.download(task, staging)
            verify_epub(path)
            target = import_to_shelf(path, shelf, state)
            state.setdefault("done_tasks", []).append(task_id)
            save_state(state_path, state)
            status, payload = client.complete(task_id, token, success=True)
            print("[云同步] 回执 HTTP %d success=%s" % (status, payload.get("success")))
            print("[导入图书] 已上架 %s / imported %s" % (os.path.basename(target), os.path.basename(target)))
        except Exception as exc:  # noqa: BLE001 - demo keeps going per task
            print("[云同步] 任务 %s 失败：%s" % (task_id, exc))
            client.complete(task_id, token, success=False, error_msg=str(exc))
    if not tasks:
        print("[云同步] 云端没有待导入的图书 / no pending books")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="星曈云同步 · 设备模拟器")
    parser.add_argument("--sdcard", default=os.path.join(here, "sdcard"),
                        help="模拟的 TF 卡根目录 / simulated SD root")
    parser.add_argument("--state", default="",
                        help="设备状态文件 / device state json")
    parser.add_argument("--once", action="store_true", help="只发一次绑定请求 / bind request only")
    parser.add_argument("--timeout", type=int, default=300, help="绑定等待秒数 / bind timeout seconds")
    parser.add_argument("--ascii", action="store_true", help="同时在终端打印 ASCII 二维码 / print ASCII QR too")
    parser.add_argument("--new-identity", action="store_true", help="换一套设备身份重新绑定 / rotate device identity")
    parser.add_argument("--firmware-name", default="", help="二维码里显示的固件名 / firmware_name shown in the QR")
    parser.add_argument("--brand", default="", help="品牌名 / brand string")
    parser.add_argument("--watch", action="store_true", help="已绑定时持续轮询任务 / keep polling tasks")
    parser.add_argument("--interval", type=int, default=10, help="轮询间隔秒 / poll interval seconds")
    args = parser.parse_args()

    state_path = args.state or os.path.join(args.sdcard, ".readpico", "cloud", "xteink_sync.json")
    os.makedirs(args.sdcard, exist_ok=True)
    state = load_state(state_path)
    if args.new_identity:
        state = {"identity": cloud.new_identity(), "access_token": "", "refresh_token": "",
                 "access_expires_at": 0, "imported": []}
    identity = state.setdefault("identity", cloud.new_identity())
    if args.firmware_name:
        identity["firmware_name"] = args.firmware_name
    if args.brand:
        identity["brand"] = args.brand
    save_state(state_path, state)

    show_import_screen()
    print("[设备] chip_id=%s" % identity["chip_id"])
    print("[设备] bluetooth_mac=%s wifi_mac=%s device_type=%s"
          % (identity["bluetooth_mac"], identity["wifi_mac"], identity["device_type"]))

    client = cloud.XteinkCloud(identity, log=print)

    if state.get("access_token"):
        print("[云同步] 已保存令牌，直接同步 / already bound, syncing")
        if not args.watch:
            sync_once(client, state["access_token"], args.sdcard, state, state_path)
            return
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                sync_once(client, state["access_token"], args.sdcard, state, state_path)
            except Exception as exc:  # noqa: BLE001 - keep watching
                print("[云同步] 同步出错：%s" % exc)
            time.sleep(args.interval)
        return

    print("[云同步] 正在申请绑定 / requesting bind...")
    status, payload = client.request_token()
    print("[云同步] 服务端返回 HTTP %d：%s" % (status, json.dumps(payload, ensure_ascii=False)))
    if status != 200:
        return

    interval = int(payload.get("poll_interval", 5) or 5)
    if payload.get("status") == "pending":
        timestamp = int(time.time())
        nonce = cloud.new_nonce()[:16]          # 二维码 nonce 用 8 字节 / 16 hex
        envelope = cloud.build_qr_envelope(identity, timestamp, nonce)
        print_qr(envelope, os.path.join(here, "qr-bind.png"), ascii_art=args.ascii)
        print("[云同步] 等待手机 App 确认绑定（每 %d 秒轮询一次，Ctrl+C 退出）" % interval)

    if args.once:
        return

    if not wait_bound(client, state_path, state, timeout=args.timeout):
        return
    sync_once(client, state["access_token"], args.sdcard, state, state_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[云同步] 已停止 / stopped")
