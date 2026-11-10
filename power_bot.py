"""Світлобот: стежить через хмару Tapo, чи розетка онлайн, і пише в Telegram."""
import base64
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from tplinkcloud.client import TPLinkApi

# Має збігатися з TERM_ID у mfa_setup.py
TERM_ID = "3f6b2c1e-8a4d-4f7b-9c2e-1d5a7e9b0c42"
STATE_FILE = "state.json"
TZ = ZoneInfo("Europe/Kyiv")

EMAIL = os.environ["TPLINK_EMAIL"]
PASSWORD = os.environ["TPLINK_PASSWORD"]
REFRESH = os.environ.get("TAPO_REFRESH_TOKEN", "").strip()
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
DEVICE_NAME = os.environ.get("DEVICE_NAME", "bathroom A").strip().lower()

DEVICE_TYPES = ["SMART.TAPOPLUG", "SMART.TAPOBULB", "SMART.TAPOHUB",
                "SMART.TAPOSWITCH", "SMART.IPCAMERA"]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


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


def get_token(api):
    """Спершу токен оновлення, потім пароль (як прив'язаний пристрій)."""
    api.host = api._get_regional_url(EMAIL)
    if REFRESH:
        try:
            res = api.refresh_login(REFRESH) or {}
            if res.get("token"):
                print("[auth] вхід через токен оновлення: ok")
                return res["token"]
        except Exception as e:
            print(f"[auth] токен оновлення не спрацював: {type(e).__name__}")
    body = {"appType": api._app_type, "appVersion": api._app_ver,
            "cloudUserName": EMAIL, "cloudPassword": PASSWORD,
            "platform": "Android", "refreshTokenNeeded": True,
            "supportBindAccount": False, "terminalUUID": TERM_ID,
            "terminalName": "power-bot", "terminalMeta": "power-bot"}
    r = api._request_post_v2(api.host, "/api/v2/account/login", body)
    res = r.result or {}
    if res.get("token"):
        print("[auth] вхід паролем: ok")
        return res["token"]
    print(f"[auth] вхід не вдався: error_code={r.error_code} "
          f"mfa={'так' if res.get('MFAProcessId') else 'ні'} "
          f"remainAttempts={res.get('remainAttempts')}")
    return None


def get_status(api, token):
    r = api._request_post_v2(api.host, "/api/v2/common/getDeviceListByPage",
                             {"deviceTypeList": DEVICE_TYPES, "index": 0, "limit": 50},
                             token)
    devs = (r.result or {}).get("deviceList") or []
    print(f"[list] error_code={r.error_code} msg={r.msg!r} пристроїв={len(devs)}")
    for d in devs:
        print(f"   {name_of(d)!r} status={d.get('status')}")
    return devs


def main():
    print("=== версія 5 ===")
    state = load_state()
    if state.get("auth_blocked"):
        sys.exit("Бот на паузі: TP-Link просив код. Повторіть mfa_setup.py "
                 "і видаліть state.json.")

    api = TPLinkApi(cloud_type="tapo", term_id=TERM_ID)
    token = get_token(api)
    if not token:
        state["auth_blocked"] = True
        save_state(state)
        sys.exit("Вхід не вдався. Бот поставлено на паузу, щоб не блокувати акаунт.")

    devs = get_status(api, token)
    dev = next((d for d in devs if name_of(d).strip().lower() == DEVICE_NAME), None)
    if dev is None:
        sys.exit(f"Розетку {DEVICE_NAME!r} не знайдено.")

    online = dev.get("status") == 1
    now = dt.datetime.now(TZ)

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

    save_state(state)


if __name__ == "__main__":
    main()
