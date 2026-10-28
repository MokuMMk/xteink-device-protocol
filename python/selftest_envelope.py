"""
SPDX-FileCopyrightText: 2026 mindreset
SPDX-License-Identifier: Apache-2.0

中文：自检二维码信封：生成 → 解密 payload → 复算 sign。
English: Self-test for the QR envelope: build → decrypt payload → re-check sign.
"""
import base64
import hashlib
import hmac
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import xteink_client as cloud
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IDENTITY = {
    "chip_id": "24C78FCB43E88B44",
    "bluetooth_mac": "02:2D:6F:19:BF:EC",
    "wifi_mac": "02:81:49:0E:17:53",
    "device_type": "pico",
    "brand": "kiikoread",
    "version": "0.1",
}


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * ((4 - len(text) % 4) % 4))


def main():
    try:
        secret = cloud.require_secret()
    except RuntimeError as error:
        raise SystemExit("提示：%s" % error)
    envelope = json.loads(cloud.build_qr_envelope(IDENTITY, 1793183000, "0011223344556677"))
    key = hashlib.sha256(secret.encode()).digest()
    plain = AESGCM(key).decrypt(b64d(envelope["iv"]),
                                b64d(envelope["payload"]) + b64d(envelope["tag"]), None)
    print("payload:", plain.decode())

    message = "%s.%s.%s.%s" % (envelope["appid"], envelope["payload"],
                               envelope["iv"], envelope["tag"])
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    print("sign match:", expected == envelope["sign"])
    print("fields:", sorted(envelope))
    missing = {"appid", "firmware_name", "payload", "iv", "tag", "sign"} - set(envelope)
    print("missing:", missing or "none")


if __name__ == "__main__":
    main()
