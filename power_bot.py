"""
Світлобот на розетці Tapo.
Перевіряє через хмару TP-Link, чи розетка онлайн, і пише в Telegram,
коли світло зникає або з'являється.

Налаштування — через секрети GitHub (змінні середовища):
  TPLINK_EMAIL, TPLINK_PASSWORD  — акаунт TP-Link (краще окремий, з доступом до розетки)
  TELEGRAM_TOKEN                 — токен бота від @BotFather
  TELEGRAM_CHAT_ID               — id каналу (напр. @my_channel або -100...)
  DEVICE_NAME                    — назва розетки в Tapo (за замовчуванням "bathroom A")
"""
import base64
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

CLOUD = "https://wap.tplinkcloud.com"
STATE_FILE = "state.json"
TZ = ZoneInfo("Europe/Kyiv")

EMAIL = os.environ["TPLINK_EMAIL"]
PASSWORD = os.environ["TPLINK_PASSWORD"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
DEVICE_NAME = os.environ.get("DEVICE_NAME", "bathroom A").strip().lower()


def post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def login():
    r = post(CLOUD, {
        "method": "login",
        "params": {
            "appType": "Tapo_Ios",
            "cloudUserName": EMAIL,
            "cloudPassword": PASSWORD,
            "terminalUUID": str(uuid.uuid5(uuid.NAMESPACE_DNS, EMAIL)),
        },
    })
    if r.get("error_code") != 0:
        sys.exit(f"Помилка входу в TP-Link: {r}")
    return r["result"]["token"]


def device_list(token):
    r = post(f"{CLOUD}?token={token}", {"method": "getDeviceList"})
    if r.get("error_code") != 0:
        sys.exit(f"Помилка отримання списку пристроїв: {r}")
    return r["result"]["deviceList"]


def name_of(d):
    alias = d.get("alias") or d.get("deviceName") or ""
    try:  # у Tapo назва часто закодована base64
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
    devices = device_list(token)

    print("Пристрої в акаунті:")
    for d in devices:
        print(f"  - {name_of(d)!r}  status={d.get('status')}  model={d.get('deviceModel')}")

    dev = next((d for d in devices if name_of(d).strip().lower() == DEVICE_NAME), None)
    if dev is None:
        sys.exit(f"Розетку {DEVICE_NAME!r} не знайдено. Перевірте DEVICE_NAME.")

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
