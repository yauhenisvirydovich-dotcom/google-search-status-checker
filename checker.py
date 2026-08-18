import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


STATUS_URL = "https://status.search.google.com/incidents.json"
STATE_FILE = Path("state.json")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()

TEST_MESSAGE = os.getenv("TEST_MESSAGE", "").lower() == "true"


STATUS_NAMES = {
    "AVAILABLE": "🟢 Available",
    "SERVICE_INFORMATION": "🔵 Information",
    "SERVICE_DISRUPTION": "🟠 Disruption",
    "SERVICE_OUTAGE": "🔴 Outage",
}


def status_name(status):
    return STATUS_NAMES.get(status, status or "Unknown")


def fetch_incidents():
    request = urllib.request.Request(
        STATUS_URL,
        headers={
            "User-Agent": "google-search-status-checker/1.0"
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)

    if not isinstance(data, list):
        raise RuntimeError("Google returned unexpected JSON")

    return data


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = urllib.parse.urlencode(
        {
            "chat_id": CHAT_ID,
            "text": message[:4000],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def clean_text(text):
    return (
        (text or "")
        .replace("<", "")
        .replace(">", "")
        .strip()
    )


def incident_message(incident, old_status=None):
    update = incident.get("most_recent_update", {})
    current_status = update.get("status", "UNKNOWN")

    if old_status and old_status != current_status:
        header = (
            "🔄 Google Search Status изменился\n\n"
            f"{status_name(old_status)} → "
            f"{status_name(current_status)}"
        )
    else:
        header = "📝 Новый Google Search Status update"

    title = incident.get("external_desc", "Google Search update")
    service = incident.get("service_name", "Unknown")
    text = clean_text(update.get("text", ""))

    incident_id = incident.get("id", "")
    incident_url = (
        f"https://status.search.google.com/incidents/{incident_id}"
    )

    return (
        f"{header}\n\n"
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: {status_name(current_status)}\n\n"
        f"{text}\n\n"
        f"{incident_url}"
    )


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


def parse_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None


def main():
    incidents = fetch_incidents()
    old_state = load_state()

    old_incidents = old_state.get("incidents", {})
    first_run = not bool(old_incidents)

    current_incidents = {}

    for incident in incidents:
        incident_id = incident.get("id")

        if not incident_id:
            continue

        update = incident.get("most_recent_update", {})

        current_incidents[incident_id] = {
            "modified": incident.get("modified", ""),
            "status": update.get("status", "UNKNOWN"),
        }

    # На первом запуске просто создаём baseline,
    # чтобы не получить десятки уведомлений
    if not first_run:
        # reversed = сначала более старые изменения,
        # затем более новые
        for incident in reversed(incidents):
            incident_id = incident.get("id")

            if not incident_id:
                continue

            current = current_incidents[incident_id]
            old = old_incidents.get(incident_id)

            # Совершенно новый инцидент
            if old is None:
                message = (
                    "🆕 Новый Google Search инцидент\n\n"
                    + incident_message(incident)
                )
                send_telegram(message)
                continue

            # Google изменил существующий инцидент
            if old.get("modified") != current.get("modified"):
                send_telegram(
                    incident_message(
                        incident,
                        old_status=old.get("status"),
                    )
                )

    now = datetime.now(timezone.utc)

    # Heartbeat нужен, чтобы public GitHub repository
    # не оставался без активности больше 60 дней.
    old_heartbeat = parse_datetime(old_state.get("heartbeat"))

    if (
        old_heartbeat is None
        or (now - old_heartbeat).days >= 30
    ):
        heartbeat = now.isoformat()
    else:
        heartbeat = old_state["heartbeat"]

    new_state = {
        "heartbeat": heartbeat,
        "incidents": current_incidents,
    }

    STATE_FILE.write_text(
        json.dumps(
            new_state,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # При ручном запуске отправляем тестовое сообщение
    if TEST_MESSAGE:
        if incidents:
            latest = incidents[0]
            update = latest.get("most_recent_update", {})

            send_telegram(
                "✅ Google Search Status Checker работает!\n\n"
                f"Последнее событие Google:\n"
                f"{latest.get('external_desc', 'Unknown')}\n"
                f"Сервис: {latest.get('service_name', 'Unknown')}\n"
                f"Статус: "
                f"{status_name(update.get('status'))}\n\n"
                "Автоматическая проверка включена."
            )
        else:
            send_telegram(
                "✅ Google Search Status Checker работает!"
            )


if __name__ == "__main__":
    main()
