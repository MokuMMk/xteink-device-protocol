"""
SPDX-FileCopyrightText: 2026 mindreset
SPDX-License-Identifier: Apache-2.0

中文：星曈云同步的设备端协议客户端（PC 模拟设备用）。签名算法从 CrossLink
      pixel-pico 固件逆向得到，并已用真接口验证：
      sign = hex(HMAC-SHA256(key=GUID, msg="appid.chip_id.bluetooth_mac.wifi_mac.timestamp.nonce"))
English: Device-side client for the Xteink cloud, as reverse-engineered from the
      CrossLink pixel-pico firmware and verified against the live endpoint.

冻结 / Frozen:
- 只连 https://api-prod.xteink.cn；身份放 HTTP 头，不放进 body。
- 签名只由本文件的 sign() 生成，禁止在别处手拼。
- Only the official HTTPS endpoint; identity lives in headers; sign() is the
  single place that builds the signature.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

APPID = "cl-20260508"
SECRET = "3d3f2e6d-9745-79eb-ad19-9b33e5b4a424"  # 固件常量 / firmware constant
BASE = "https://api-prod.xteink.cn"
TOKEN_PATH = "/api/v1/third-party-device/token"
PULL_PATH = "/api/v1/third-party-device/tasks/pull?limit=20"

USER_AGENT = "kiikoread-device-sim/0.1"


def new_nonce():
    """固件用 16 字节随机数转 32 位小写 hex。/ 16 random bytes as 32 lowercase hex."""
    return secrets.token_hex(16)


def sign(timestamp, nonce, identity):
    """签名原文：appid.chip_id.bluetooth_mac.wifi_mac.timestamp.nonce。"""
    message = "%s.%s.%s.%s.%d.%s" % (
        APPID,
        identity["chip_id"],
        identity["bluetooth_mac"],
        identity["wifi_mac"],
        timestamp,
        nonce,
    )
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def new_identity():
    """生成一套本地设备身份（模拟板卡）。/ Build a local device identity."""
    chip = secrets.token_hex(8).upper()
    def mac():
        return ":".join("%02X" % b for b in [0x02] + [secrets.randbelow(256) for _ in range(5)])
    return {
        "chip_id": chip,
        "bluetooth_mac": mac(),
        "wifi_mac": mac(),
        "device_type": "pico",
        "brand": "kiikoread",
        "version": "0.1",
        "firmware_name": FIRMWARE_NAME,
    }


class XteinkCloud:
    """只做协议，不画界面。/ Protocol only; the simulator owns the UI."""

    def __init__(self, identity, log=None, timeout=20):
        self.identity = identity
        self.log = log or (lambda *a: None)
        self.timeout = timeout

    # ---- 底层 / transport ----------------------------------------------------
    def _headers(self, bearer=None):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Chip-Id": self.identity["chip_id"],
            "X-Wifi-Mac": self.identity["wifi_mac"],
            "X-Bluetooth-Mac": self.identity["bluetooth_mac"],
        }
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        return headers

    def _call(self, method, path, body=None, bearer=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers=self._headers(bearer))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"error": raw}
            return err.code, payload
        except urllib.error.URLError as err:
            raise RuntimeError("network error: %s" % err.reason)

    # ---- 绑定 / bind -------------------------------------------------------
    def request_token(self):
        """申请/轮询绑定；pending 时返回服务端的轮询间隔。/ Token request or poll."""
        timestamp = int(time.time())
        nonce = new_nonce()
        body = {
            "appid": APPID,
            "timestamp": timestamp,
            "nonce": nonce,
            "sign": sign(timestamp, nonce, self.identity),
        }
        status, payload = self._call("POST", TOKEN_PATH, body)
        return status, payload

    # ---- 任务 / tasks ------------------------------------------------------
    def pull(self, token):
        status, payload = self._call("GET", PULL_PATH, bearer=token)
        if status == 401:
            raise RuntimeError("token rejected (401)")
        tasks = payload.get("tasks") or []
        return int(payload.get("pending", len(tasks))), tasks

    @staticmethod
    def encode_url(url):
        """中文文件名/空格要按路径段编码；已有 %xx 不能再编码一次。"""
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path,
                                        parts.query, parts.fragment))

    @staticmethod
    def local_name(task, url):
        """优先用 save_path 的文件名，保证书架里是原始中文名。"""
        for candidate in (task.get("save_path") or "",
                          urllib.parse.unquote(urllib.parse.urlsplit(url).path)):
            name = os.path.basename(candidate.replace("\\", "/"))
            if name:
                return name
        return "book.epub"

    def download(self, task, dest_dir):
        url = task.get("file_url") or ""
        if not url.startswith("https://"):
            raise RuntimeError("untrusted file_url: %r" % url)
        name = self.local_name(task, url)
        os.makedirs(dest_dir, exist_ok=True)
        target = os.path.join(dest_dir, name)
        req = urllib.request.Request(self.encode_url(url), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(target + ".part", "wb") as out:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(target + ".part", target)
        return target

    def redo(self, task_ids, token):
        """把失败任务重新入队（固件有 batch-redo / batch-cancel）。"""
        status, payload = self._call("POST", "/api/v1/third-party-device/tasks/batch-redo",
                                     {"task_ids": list(task_ids)}, bearer=token)
        return status, payload

    def complete(self, task_id, token, success=True, error_msg="", retry_count=0):
        body = {"success": success, "completed": success,
                "error_msg": error_msg, "retry_count": retry_count}
        status, payload = self._call("POST", "/api/v1/third-party-device/tasks/%s/complete" % task_id,
                                     body, bearer=token)
        return status, payload


def find_token(payload):
    """从任意嵌套结构里找 access_token/refresh_token。/ Find tokens anywhere."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("access_token", "refresh_token", "expires_in", "refresh_expires_in"):
                    found.setdefault(key, value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(payload)
    return found


# ---- 二维码信封 / QR envelope ------------------------------------------------
# 固件 0x420ce758：key = SHA256(GUID)，AES-256-GCM(12B IV)，tag 16B；
# payload/iv/tag 用 base64url（无 padding），sign = hex(HMAC-SHA256(GUID, appid.payload.iv.tag))。
# Firmware 0x420ce758: key = SHA256(GUID), AES-256-GCM with a 12-byte IV and a
# 16-byte tag; payload/iv/tag are unpadded base64url; sign is lowercase hex HMAC
# over "appid.payload.iv.tag".
FIRMWARE_NAME = "CrossPoint-ESP32-4.2.2T"   # 仅作为缺省/参考值 / default reference only


def _b64u(raw):
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def build_qr_envelope(identity, timestamp, nonce, firmware_name=None):
    """生成 App 可扫码的二维码信封 JSON。/ Build the scannable bind envelope."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    payload = json.dumps({
        "chip_id": identity["chip_id"],
        "bluetooth_mac": identity["bluetooth_mac"],
        "wifi_mac": identity["wifi_mac"],
        "device_type": identity["device_type"],
        "brand": identity["brand"],
        "version": identity["version"],
        "timestamp": timestamp,
        "nonce": nonce,
    }, separators=(",", ":"))
    key = hashlib.sha256(SECRET.encode()).digest()
    iv = secrets.token_bytes(12)
    sealed = AESGCM(key).encrypt(iv, payload.encode(), None)   # ciphertext + 16B tag
    ciphertext, tag = sealed[:-16], sealed[-16:]
    p, i, t = _b64u(ciphertext), _b64u(iv), _b64u(tag)
    message = "%s.%s.%s.%s" % (APPID, p, i, t)
    signature = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    return json.dumps({
        "appid": APPID,
        "firmware_name": firmware_name or identity.get("firmware_name") or FIRMWARE_NAME,
        "payload": p,
        "iv": i,
        "tag": t,
        "sign": signature,
    }, separators=(",", ":"))
