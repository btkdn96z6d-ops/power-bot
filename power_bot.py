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

from tplinkcloud.certs import get_ca_cert_path
from tplinkcloud.client import TPLinkApi

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
HIDE = {"deviceMac", "deviceId", "hwId", "fwId", "oemId", "appServerUrl", "alias",
        "accountApiUrl", "fwVer", "lastBindTime", "deviceHwVer"}

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


def tapo_login():
    """Вхід у нову хмару Tapo (з підписом запитів, як у застосунку)."""
    try:
        api = TPLinkApi(cloud_type="tapo",
                        term_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "power-bot2-" + EMAIL)))
        res = api.login(EMAIL, PASSWORD) or {}
        token = res.get("token")
        print(f"[tapo login] host={api.host} token={'ok' if token else None} "
              f"keys={list(res.keys())}")
        return api, token
    except Exception as e:
        print(f"[tapo login] помилка: {type(e).__name__}: {e}")
        return None, None


def regional_status(api, token, host):
    """Список пристроїв з регіонального сервера Tapo: {deviceId: status}."""
    try:
        r = api._request_post_v2(host, "/api/v2/common/getDeviceListByPage",
                                 {"deviceTypeList": DEVICE_TYPES, "index": 0, "limit": 50},
                                 token)
    except Exception as e:
        print(f"[regional list] помилка: {type(e).__name__}: {e}")
        return {}
    devs = (r.result or {}).get("deviceList") or []
    print(f"[regional list] error_code={r.error_code} msg={r.msg!r} пристроїв={len(devs)}")
    for d in devs:
        print(f"    {name_of(d)!r} status={d.get('status')}")
    return {d.get("deviceId"): d.get("status") for d in devs}


def passthrough(api, token, host, device_id):
    try:
        r = api._request_post_v2(host, "/api/v2/common/passthrough",
                                 {"deviceId": device_id,
                                  "requestData": json.dumps({"method": "get_device_info"})},
                                 token)
        return r.error_code, r.msg
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


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
    print("=== версія 4 ===")
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
    host = dev.get("appServerUrlV2")
    api, token2 = tapo_login()
    print(f"[debug] api={'ok' if api else None} token2={'ok' if token2 else None} host={host}")

    if api and token2 and host:
        statuses = regional_status(api, token2, host)
        for d in devices:
            code, msg = passthrough(api, token2, host, d.get("deviceId"))
            print(f"[passthrough] {name_of(d)!r} error_code={code} msg={msg!r}")
        if statuses.get(dev.get("deviceId")) is not None:
            online = statuses[dev.get("deviceId")] == 1
            print(f"Статус з регіонального сервера: {'онлайн' if online else 'офлайн'}")
        else:
            code, _ = passthrough(api, token2, host, dev.get("deviceId"))
            if code is not None:
                online = code == 0
                print(f"Статус через passthrough: {'онлайн' if online else 'офлайн'}")

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
