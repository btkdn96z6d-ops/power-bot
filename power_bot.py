"""Світлобот: стежить через хмару Tapo, чи розетка онлайн, і пише в Telegram."""
import base64
import hashlib
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from tplinkcloud.client import TPLinkApi

STATE_FILE = "state.json"
TZ = ZoneInfo("Europe/Kyiv")

EMAIL = os.environ["TPLINK_EMAIL"]
PASSWORD = os.environ["TPLINK_PASSWORD"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
DEVICE_NAME = os.environ.get("DEVICE_NAME", "bathroom A").strip().lower()


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


DEVICE_TYPES = ["SMART.TAPOPLUG", "SMART.TAPOBULB", "SMART.TAPOHUB",
                "SMART.IPCAMERA", "SMART.TAPOSWITCH", "SMART.KASAPLUG"]


def extract(result):
    if not isinstance(result, dict):
        return []
    for key in ("deviceList", "deviceInfoList", "list"):
        if isinstance(result.get(key), list):
            return result[key]
    return []


def get_devices(api, token):
    attempts = [
        ("v1 getDeviceList", lambda: api._request_post_v1(
            {"method": "getDeviceList"}, token)),
        ("v1 getDeviceListByPage", lambda: api._request_post_v1(
            {"method": "getDeviceListByPage",
             "params": {"deviceTypeList": DEVICE_TYPES, "index": 0, "limit": 50}},
            token)),
        ("v2 getDeviceListByPage", lambda: api._request_post_v2(
            api.host, "/api/v2/common/getDeviceListByPage",
            {"deviceTypeList": DEVICE_TYPES, "index": 0, "limit": 50}, token)),
        ("v2 getDeviceList", lambda: api._request_post_v2(
            api.host, "/api/v2/common/getDeviceList", {}, token)),
    ]
    for label, call in attempts:
        try:
            r = call()
        except Exception as e:
            print(f"[{label}] помилка запиту: {type(e).__name__}")
            continue
        devs = extract(r.result)
        print(f"[{label}] error_code={r.error_code} msg={r.msg!r} пристроїв={len(devs)}")
        if r.successful and devs:
            return devs
    return []


def main():
    api = TPLinkApi(cloud_type="tapo", term_id="power-bot-" + hashlib.md5(EMAIL.encode()).hexdigest()[:12])
    try:
        result = api.login(EMAIL, PASSWORD)
    except Exception as e:
        sys.exit(f"Помилка входу в хмару Tapo: {type(e).__name__}: {e}")
    token = result.get("token")
    devices = get_devices(api, token)

    print("Пристрої в хмарі Tapo:")
    for d in devices:
        print(f"  - {name_of(d)!r}  status={d.get('status')}  model={d.get('deviceModel')}")

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


if __name__ == "__main__":
    main()
