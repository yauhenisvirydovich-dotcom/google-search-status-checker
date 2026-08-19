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
    """
    Google иногда отдаёт ссылки в виде:
    <https://example.com>

    Для Telegram превращаем их в обычные ссылки:
    https://example.com
    """
    return (
        (text or "")
        .replace("<", "")
        .replace(">", "")
        .strip()
    )


def parse_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None


def get_initial_update(incident):
    """
    Возвращает самое первое сообщение Google
    по конкретному инциденту.

    Это именно то описание, которое видно
    на Google Search Status Dashboard.
    """
    updates = incident.get("updates", [])

    if not updates:
        return {}

    dated_updates = []

    for update in updates:
        date_value = (
            update.get("when")
            or update.get("created")
        )

        parsed = parse_datetime(date_value)

        if parsed is not None:
            dated_updates.append((parsed, update))

    if dated_updates:
        dated_updates.sort(key=lambda item: item[0])
        return dated_updates[0][1]

    # Запасной вариант.
    # Сейчас Google отдаёт updates от новых к старым.
    return updates[-1]


def get_initial_description(incident):
    initial_update = get_initial_update(incident)

    return clean_text(
        initial_update.get("text", "")
    )


def get_latest_description(incident):
    update = incident.get("most_recent_update", {})

    return clean_text(
        update.get("text", "")
    )


def incident_url(incident):
    incident_id = incident.get("id", "")

    if not incident_id:
        return "https://status.search.google.com/"

    return (
        "https://status.search.google.com/"
        f"incidents/{incident_id}"
    )


def new_incident_message(incident):
    """
    Сообщение о совершенно новом инциденте.
    """
    update = incident.get("most_recent_update", {})

    current_status = update.get(
        "status",
        "UNKNOWN",
    )

    title = incident.get(
        "external_desc",
        "Google Search update",
    )

    service = incident.get(
        "service_name",
        "Unknown",
    )

    initial_text = get_initial_description(incident)
    latest_text = get_latest_description(incident)

    message = (
        "🆕 Новый Google Search инцидент\n\n"
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: {status_name(current_status)}\n"
    )

    if initial_text:
        message += (
            "\n"
            "📝 Описание:\n"
            f"{initial_text}\n"
        )

    # Если последнее сообщение уже отличается
    # от первоначального — добавляем его отдельно.
    if (
        latest_text
        and latest_text != initial_text
    ):
        message += (
            "\n"
            "🔔 Последнее обновление:\n"
            f"{latest_text}\n"
        )

    message += (
        "\n"
        f"🔗 {incident_url(incident)}"
    )

    return message


def changed_incident_message(
    incident,
    old_status=None,
):
    """
    Отдельное новое Telegram-сообщение,
    когда Google изменил существующий инцидент.
    """
    update = incident.get("most_recent_update", {})

    current_status = update.get(
        "status",
        "UNKNOWN",
    )

    title = incident.get(
        "external_desc",
        "Google Search update",
    )

    service = incident.get(
        "service_name",
        "Unknown",
    )

    initial_text = get_initial_description(incident)
    latest_text = get_latest_description(incident)

    # Если реально изменился статус
    if (
        old_status
        and old_status != current_status
    ):
        message = (
            "🔄 Google Search Status изменился\n\n"
            f"{status_name(old_status)}"
            " → "
            f"{status_name(current_status)}\n\n"
        )

    # Если Google просто добавил новое сообщение,
    # но статус оставил прежним
    else:
        message = (
            "📝 Новый Google Search Status update\n\n"
        )

    message += (
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: {status_name(current_status)}\n"
    )

    if initial_text:
        message += (
            "\n"
            "📄 Описание инцидента:\n"
            f"{initial_text}\n"
        )

    if latest_text:
        if latest_text != initial_text:
            message += (
                "\n"
                "🔔 Последнее обновление:\n"
                f"{latest_text}\n"
            )

    message += (
        "\n"
        f"🔗 {incident_url(incident)}"
    )

    return message


def test_message(incident):
    """
    Сообщение при ручном запуске
    с TEST_MESSAGE=true.
    """
    update = incident.get("most_recent_update", {})

    current_status = update.get(
        "status",
        "UNKNOWN",
    )

    title = incident.get(
        "external_desc",
        "Google Search update",
    )

    service = incident.get(
        "service_name",
        "Unknown",
    )

    initial_text = get_initial_description(incident)
    latest_text = get_latest_description(incident)

    message = (
        "✅ Google Search Status Checker работает!\n\n"
        "Последнее событие Google:\n\n"
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: {status_name(current_status)}\n"
    )

    if initial_text:
        message += (
            "\n"
            "📝 Описание:\n"
            f"{initial_text}\n"
        )

    if (
        latest_text
        and latest_text != initial_text
    ):
        message += (
            "\n"
            "🔔 Последнее обновление:\n"
            f"{latest_text}\n"
        )

    message += (
        "\n"
        f"🔗 {incident_url(incident)}\n\n"
        "Автоматическая проверка включена."
    )

    return message


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}


def main():
    incidents = fetch_incidents()

    old_state = load_state()

    old_incidents = old_state.get(
        "incidents",
        {},
    )

    # Если state.json ещё пустой,
    # считаем это первым запуском.
    first_run = not bool(old_incidents)

    current_incidents = {}

    # Создаём текущее состояние Google
    for incident in incidents:
        incident_id = incident.get("id")

        if not incident_id:
            continue

        update = incident.get(
            "most_recent_update",
            {},
        )

        current_incidents[incident_id] = {
            "modified": incident.get(
                "modified",
                "",
            ),
            "status": update.get(
                "status",
                "UNKNOWN",
            ),
            "update_modified": update.get(
                "modified",
                "",
            ),
            "update_created": update.get(
                "created",
                "",
            ),
        }

    # На первом запуске уведомления не отправляем.
    #
    # Просто записываем текущее состояние,
    # чтобы бот не прислал сразу десятки
    # старых инцидентов.
    if not first_run:

        # Google обычно отдаёт новые события первыми.
        # reversed нужен, чтобы Telegram получил
        # события в хронологическом порядке:
        # сначала старые изменения, потом новые.
        for incident in reversed(incidents):
            incident_id = incident.get("id")

            if not incident_id:
                continue

            current = current_incidents[
                incident_id
            ]

            old = old_incidents.get(
                incident_id
            )

            # -----------------------------
            # СОВЕРШЕННО НОВЫЙ ИНЦИДЕНТ
            # -----------------------------
            if old is None:
                send_telegram(
                    new_incident_message(
                        incident
                    )
                )

                continue

            # -----------------------------
            # GOOGLE ИЗМЕНИЛ ИНЦИДЕНТ
            # -----------------------------
            #
            # modified меняется, когда Google
            # публикует новое обновление
            # или редактирует существующее.
            if (
                old.get("modified")
                != current.get("modified")
            ):
                send_telegram(
                    changed_incident_message(
                        incident,
                        old_status=old.get(
                            "status"
                        ),
                    )
                )

    now = datetime.now(timezone.utc)

    # Heartbeat нужен, чтобы public GitHub
    # repository не оставался без активности
    # больше 60 дней.
    old_heartbeat = parse_datetime(
        old_state.get("heartbeat")
    )

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

    # При ручном запуске GitHub Actions
    # с TEST_MESSAGE=true отправляем тест.
    if TEST_MESSAGE:
        if incidents:
            send_telegram(
                test_message(
                    incidents[0]
                )
            )
        else:
            send_telegram(
                "✅ Google Search Status Checker "
                "работает!"
            )


if __name__ == "__main__":
    main()
