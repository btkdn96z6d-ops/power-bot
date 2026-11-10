"""Світлобот: стежить через хмару Tapo, чи розетка онлайн, і пише в Telegram."""
import base64
import datetime as dt
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from tplinkcloud.certs import get_ca_cert_path  # сертифікат TP-Link

CLOUD = "https://n-wap-gw.tplinkcloud.com"
STATE_FILE = "state.json"
TZ = ZoneInfo("Europe/Kyiv")

EMAIL = os.environ["TPLINK_EMAIL"]
PASSWORD = os.environ["TPLINK_PASSWORD"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
DEVICE_NAME = os.environ.get("DEVICE_NAME", "bathroom A").strip().lower()

DEVICE_TYPES = ["SMART.TAPOPLUG", "SMART.TAPOBULB", "SMART.TAPOHUB",
                "SMART.TAPOSWITCH", "SMART.IPCAMERA"]
HIDE = {"deviceMac", "deviceId", "hwId", "fwId", "oemId", "appServerUrl", "alias"}

CTX = ssl.create_default_context()
CTX.load_verify_locations(get_ca_cert_path())


def post(payload, token=None):
    url = CLOUD + (f"?token={token}" if token else "")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.load(r)


def login():
    r = post({"method": "login", "params": {
        "appType": "Tapo_Android",
        "cloudUserName": EMAIL,
        "cloudPassword": PASSWORD,
        "terminalUUID": str(uuid.uuid5(uuid.NAMESPACE_DNS, "power-bot-" + EMAIL)),
    }})
    if r.get("error_code") != 0:
        sys.exit(f"Помилка входу: error_code={r.get('error_code')} msg={r.get('msg')}")
    return r["result"]["token"]


def get_devices(token):
    r = post({"method": "getDeviceListByPage", "params": {
        "deviceTypeList": DEVICE_TYPES, "index": 0, "limit": 50}}, token)
    print(f"[getDeviceListByPage] error_code={r.get('error_code')} msg={r.get('msg')!r}")
    return (r.get("result") or {}).get("deviceList") or []


def name_of(d):
    alias = d.get("alias") or d.get("deviceName") or ""
    try:
        decoded = base64.b64decode(alias, validate=True).decode("utf-8")
        if decoded.isprintable():
            return decoded
    except Exception:
        pass
    return alias


def send(text):
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=30
    )


def fmt_duration(seconds):
    m = int(seconds // 60)
    h, m = divmod(m, 60)
    return f"{h} год {m} хв" if h else f"{m} хв"


def main():
    token = login()
    devices = get_devices(token)

    print("Пристрої в хмарі Tapo:")
    for d in devices:
        info = {k: v for k, v in d.items() if k not in HIDE}
        print(f"  - {name_of(d)!r}  {info}")

    dev = next((d for d in devices if name_of(d).strip().lower() == DEVICE_NAME), None)
    if dev is None:
        sys.exit(f"Розетку {DEVICE_NAME!r} не знайдено.")

    online = dev.get("status") == 1
    now = dt.datetime.now(TZ)

    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)

    prev = state.get("online")
    if prev is None:
        print(f"Перший запуск: світло {'є' if online else 'немає'}.")
        state = {"online": online, "since": now.isoformat()}
    elif prev != online:
        since = dt.datetime.fromisoformat(state["since"])
        dur = fmt_duration((now - since).total_seconds())
        t = now.strftime("%H:%M")
        if online:
            send(f"🟢 Світло з'явилося ({t})\nБез світла було: {dur}")
        else:
            send(f"🔴 Світла немає ({t})\nСвітло було: {dur}")
        state = {"online": online, "since": now.isoformat()}
        print("Статус змінився, повідомлення надіслано.")
    else:
        print(f"Без змін: світло {'є' if online else 'немає'}.")

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)
