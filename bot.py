import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")

os.makedirs(STORAGE_DIR, exist_ok=True)

BOT_TOKEN = os.environ.get("MARKAZ_BOT_TOKEN", "").strip()
BOT_PASSWORD = os.environ.get("MARKAZ_BOT_PASSWORD", "").strip()
DIRECTOR_CHAT_ID = os.environ.get("MARKAZ_DIRECTOR_CHAT_ID", "").strip()
API_KEY = os.environ.get("MARKAZ_API_KEY", "").strip()

PORT = int(os.environ.get("PORT", "8080"))

AUTHORIZED_FILE = os.path.join(STORAGE_DIR, "authorized.json")
EVENTS_FILE = os.path.join(STORAGE_DIR, "events.jsonl")
OFFSET_FILE = os.path.join(STORAGE_DIR, "sent_offset.txt")

app = Flask(__name__)

file_lock = threading.Lock()


def ensure_config():
    missing = []

    if not BOT_TOKEN:
        missing.append("MARKAZ_BOT_TOKEN")

    if not BOT_PASSWORD:
        missing.append("MARKAZ_BOT_PASSWORD")

    if not DIRECTOR_CHAT_ID:
        missing.append("MARKAZ_DIRECTOR_CHAT_ID")

    if not API_KEY:
        missing.append("MARKAZ_API_KEY")

    if missing:
        print("MISSING ENV:", ", ".join(missing))


def telegram(method, data=None):
    if not BOT_TOKEN:
        return {}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        r = requests.post(
            url,
            data=data or {},
            timeout=35
        )

        try:
            return r.json()
        except Exception:
            return {}

    except Exception as e:
        print("TELEGRAM ERROR:", type(e).__name__, str(e))
        return {}


def send_message(chat_id, text):
    text = str(text or "")

    telegram(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text[:4096]
        }
    )


def load_authorized():
    if not os.path.exists(AUTHORIZED_FILE):
        return {}

    try:
        with open(
            AUTHORIZED_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def save_authorized(data):
    with file_lock:
        with open(
            AUTHORIZED_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )


def is_authorized(chat_id):
    data = load_authorized()
    return bool(data.get(str(chat_id)))


def set_authorized(chat_id, value):
    data = load_authorized()
    chat_id = str(chat_id)

    if value:
        data[chat_id] = True
    else:
        data.pop(chat_id, None)

    save_authorized(data)


def event_title(event_type):
    titles = {
        "login": "تسجيل دخول",
        "add_student": "إضافة طالب",
        "edit_student": "تعديل بيانات طالب",
        "delete_student": "حذف طالب",
        "attendance": "حفظ الحضور والتقرير",
        "student_log": "حفظ سجل الطالب",
        "student_daily_report": "تقرير المتابعة اليومية",
        "logout": "تسجيل خروج"
    }

    return titles.get(event_type, event_type)


def format_event(event):
    lines = [
        "مركز عمران",
        f"الحدث: {event_title(str(event.get('type', 'event')))}",
        f"الوقت: {event.get('time', '')}"
    ]

    data = event.get("data", {})

    if isinstance(data, dict):
        for key, value in data.items():

            if value is None or value == "":
                continue

            if isinstance(value, (dict, list)):
                try:
                    value = json.dumps(
                        value,
                        ensure_ascii=False
                    )
                except Exception:
                    value = str(value)

            lines.append(f"{key}: {value}")

    return "\n".join(lines)[:4096]


def read_events():
    if not os.path.exists(EVENTS_FILE):
        return []

    with file_lock:
        try:
            with open(
                EVENTS_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                return f.readlines()
        except Exception:
            return []


def get_offset():
    if not os.path.exists(OFFSET_FILE):
        return 0

    try:
        with open(
            OFFSET_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return max(0, int(f.read().strip() or "0"))
    except Exception:
        return 0


def set_offset(value):
    with file_lock:
        with open(
            OFFSET_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(str(value))


def send_pending_events():
    lines = read_events()

    if not lines:
        return

    authorized = load_authorized()

    targets = [
        str(chat_id)
        for chat_id, enabled in authorized.items()
        if enabled
    ]

    if not targets:
        return

    offset = get_offset()

    if offset >= len(lines):
        return

    for index in range(offset, len(lines)):

        try:
            event = json.loads(lines[index])
        except Exception:
            set_offset(index + 1)
            continue

        text = format_event(event)

        success = True

        for chat_id in targets:
            result = telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text
                }
            )

            if not result.get("ok"):
                success = False
                break

        if not success:
            break

        set_offset(index + 1)


def worker_loop():
    print("EVENT WORKER STARTED")

    while True:
        try:
            send_pending_events()
        except Exception as e:
            print(
                "WORKER ERROR:",
                type(e).__name__,
                str(e)
            )

        time.sleep(5)


def telegram_polling():
    print("TELEGRAM POLLING STARTED")

    offset = None

    while True:

        try:
            params = {
                "timeout": 30
            }

            if offset is not None:
                params["offset"] = offset

            result = telegram(
                "getUpdates",
                params
            )

            if not result.get("ok"):
                time.sleep(5)
                continue

            updates = result.get("result", [])

            for update in updates:

                update_id = update.get("update_id")

                if update_id is not None:
                    offset = int(update_id) + 1

                handle_update(update)

        except Exception as e:
            print(
                "POLLING ERROR:",
                type(e).__name__,
                str(e)
            )

            time.sleep(5)


def handle_update(update):

    message = update.get("message")

    if not isinstance(message, dict):
        return

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))

    if not chat_id:
        return

    text = str(
        message.get("text", "")
    ).strip()

    if text == "/start":

        set_authorized(chat_id, False)

        send_message(
            chat_id,
            "مرحبًا بك في بوت مركز عمران.\n\n"
            "أدخل كلمة المرور للمتابعة."
        )

        return

    if text == "/logout":

        set_authorized(chat_id, False)

        send_message(
            chat_id,
            "تم تسجيل الخروج.\n"
            "أرسل /start للدخول مرة أخرى."
        )

        return

    if not is_authorized(chat_id):

        if text == BOT_PASSWORD:

            if DIRECTOR_CHAT_ID and chat_id != DIRECTOR_CHAT_ID:

                send_message(
                    chat_id,
                    "هذه المحادثة غير مخولة باستخدام البوت."
                )

                return

            set_authorized(chat_id, True)

            send_message(
                chat_id,
                "تم التحقق بنجاح.\n"
                "سيتم إرسال تقارير مركز عمران إلى هذه المحادثة."
            )

        else:

            send_message(
                chat_id,
                "كلمة المرور غير صحيحة.\n"
                "أدخل كلمة المرور للمتابعة."
            )

        return

    if text == "/status":

        send_message(
            chat_id,
            "البوت يعمل بشكل طبيعي."
        )


@app.post("/api.php")
@app.post("/event")
def receive_event():

    supplied_key = request.headers.get(
        "X-Markaz-Api-Key",
        ""
    ).strip()

    if not supplied_key:
        supplied_key = str(
            request.form.get("api_key", "")
        ).strip()

    if not API_KEY or supplied_key != API_KEY:
        return jsonify({
            "ok": False,
            "error": "unauthorized"
        }), 401

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):
        data = request.form.to_dict()

    event_type = str(
        data.get(
            "type",
            data.get("event", "event")
        )
    ).strip()

    event_data = data.get(
        "data",
        {}
    )

    if not isinstance(event_data, dict):
        event_data = {}

    allowed = {
        "login",
        "add_student",
        "edit_student",
        "delete_student",
        "attendance",
        "student_log",
        "student_daily_report",
        "logout"
    }

    if event_type not in allowed:
        return jsonify({
            "ok": False,
            "error": "invalid_event"
        }), 400

    event = {
        "time": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "type": event_type,
        "data": event_data
    }

    try:
        with file_lock:
            with open(
                EVENTS_FILE,
                "a",
                encoding="utf-8"
            ) as f:

                f.write(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":")
                    ) + "\n"
                )

        return jsonify({
            "ok": True
        })

    except Exception as e:

        print(
            "API STORAGE ERROR:",
            type(e).__name__,
            str(e)
        )

        return jsonify({
            "ok": False,
            "error": "storage_failed"
        }), 500


@app.get("/")
def home():
    return "Markaz Omran Telegram Bot is running."


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "Markaz Omran Telegram Bot"
    })


def start_services():

    ensure_config()

    threading.Thread(
        target=worker_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=telegram_polling,
        daemon=True
    ).start()


if __name__ == "__main__":

    ensure_config()

    start_services()

    print(
        f"API SERVER STARTING ON PORT {PORT}"
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
