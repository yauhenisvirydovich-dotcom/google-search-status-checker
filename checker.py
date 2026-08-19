import json
import os
import re
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
    return STATUS_NAMES.get(
        status,
        status or "Unknown",
    )


def fetch_incidents():
    request = urllib.request.Request(
        STATUS_URL,
        headers={
            "User-Agent": "google-search-status-checker/1.0"
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        data = json.load(response)

    if not isinstance(data, list):
        raise RuntimeError(
            "Google returned unexpected JSON"
        )

    return data


def send_telegram(message):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

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

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram error: {result}"
        )


def clean_text(text):
    """
    Очищает текст Google перед отправкой в Telegram.

    Удаляет:
    - ссылки <https://example.com>
    - обычные https://example.com
    - обычные http://example.com
    - markdown-ссылки [текст](https://example.com)

    При этом текст markdown-ссылки сохраняется.
    """

    text = text or ""

    # Markdown-ссылки:
    # [Spam updates](https://example.com)
    # превращаются в:
    # Spam updates
    text = re.sub(
        r"\[([^\]]+)\]\(https?://[^)]+\)",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )

    # Ссылки в угловых скобках:
    # <https://example.com>
    text = re.sub(
        r"<https?://[^>]+>",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Обычные URL:
    # https://example.com
    # http://example.com
    text = re.sub(
        r"https?://[^\s<>]+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Удаляем оставшиеся угловые скобки
    text = (
        text
        .replace("<", "")
        .replace(">", "")
    )

    # Убираем лишние пробелы
    text = re.sub(
        r"[ \t]{2,}",
        " ",
        text,
    )

    # Убираем пробел перед знаками препинания.
    #
    # Например:
    # "spam update , which"
    # ->
    # "spam update, which"
    text = re.sub(
        r"\s+([,.;:!?])",
        r"\1",
        text,
    )

    # Не больше одной пустой строки подряд
    text = re.sub(
        r"\n[ \t]*\n[ \t]*\n+",
        "\n\n",
        text,
    )

    # Убираем пробелы в начале и конце строк
    lines = [
        line.strip()
        for line in text.splitlines()
    ]

    text = "\n".join(lines)

    return text.strip()


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
    Возвращает самое первое обновление
    конкретного Google Search инцидента.
    """

    updates = incident.get("updates", [])

    if not updates:
        return {}

    dated_updates = []

    for update in updates:
        date_value = (
            update.get("when")
            or update.get("created")
            or update.get("modified")
        )

        parsed = parse_datetime(
            date_value
        )

        if parsed is not None:
            dated_updates.append(
                (
                    parsed,
                    update,
                )
            )

    # Если даты удалось определить,
    # выбираем самое раннее сообщение.
    if dated_updates:
        dated_updates.sort(
            key=lambda item: item[0]
        )

        return dated_updates[0][1]

    # Запасной вариант.
    # Google обычно отдаёт updates
    # от новых к старым.
    return updates[-1]


def get_initial_description(incident):
    """
    Первоначальное описание инцидента.
    Все ссылки из текста удаляются.
    """

    initial_update = get_initial_update(
        incident
    )

    return clean_text(
        initial_update.get(
            "text",
            "",
        )
    )


def get_latest_description(incident):
    """
    Самое последнее сообщение Google.
    Все ссылки из текста удаляются.
    """

    update = incident.get(
        "most_recent_update",
        {},
    )

    return clean_text(
        update.get(
            "text",
            "",
        )
    )


def incident_url(incident):
    """
    Ссылка на сам Google Search Status incident.

    Эта ссылка НЕ удаляется,
    поскольку добавляется нами отдельно
    и не проходит через clean_text().
    """

    incident_id = incident.get(
        "id",
        "",
    )

    if not incident_id:
        return (
            "https://status.search.google.com/"
        )

    return (
        "https://status.search.google.com/"
        f"incidents/{incident_id}"
    )


def new_incident_message(incident):
    """
    Сообщение о совершенно новом инциденте.
    """

    update = incident.get(
        "most_recent_update",
        {},
    )

    current_status = update.get(
        "status",
        "UNKNOWN",
    )

    title = clean_text(
        incident.get(
            "external_desc",
            "Google Search update",
        )
    )

    service = clean_text(
        incident.get(
            "service_name",
            "Unknown",
        )
    )

    initial_text = get_initial_description(
        incident
    )

    latest_text = get_latest_description(
        incident
    )

    message = (
        "🆕 Новый Google Search инцидент\n\n"
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: "
        f"{status_name(current_status)}\n"
    )

    if initial_text:
        message += (
            "\n"
            "📝 Описание:\n"
            f"{initial_text}\n"
        )

    # Если последнее сообщение уже отличается
    # от первоначального — показываем отдельно.
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
    Новое отдельное Telegram-сообщение,
    когда Google изменил существующий инцидент.

    Старое сообщение Telegram не редактируется.
    """

    update = incident.get(
        "most_recent_update",
        {},
    )

    current_status = update.get(
        "status",
        "UNKNOWN",
    )

    title = clean_text(
        incident.get(
            "external_desc",
            "Google Search update",
        )
    )

    service = clean_text(
        incident.get(
            "service_name",
            "Unknown",
        )
    )

    initial_text = get_initial_description(
        incident
    )

    latest_text = get_latest_description(
        incident
    )

    # Если изменился именно статус
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

    # Если Google добавил новый комментарий,
    # но сам статус остался прежним
    else:
        message = (
            "📝 Новый Google Search Status update\n\n"
        )

    message += (
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: "
        f"{status_name(current_status)}\n"
    )

    if initial_text:
        message += (
            "\n"
            "📄 Описание инцидента:\n"
            f"{initial_text}\n"
        )

    # Не повторяем один и тот же текст дважды.
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


def test_message(incident):
    """
    Тестовое сообщение при ручном запуске
    с TEST_MESSAGE=true.
    """

    update = incident.get(
        "most_recent_update",
        {},
    )

    current_status = update.get(
        "status",
        "UNKNOWN",
    )

    title = clean_text(
        incident.get(
            "external_desc",
            "Google Search update",
        )
    )

    service = clean_text(
        incident.get(
            "service_name",
            "Unknown",
        )
    )

    initial_text = get_initial_description(
        incident
    )

    latest_text = get_latest_description(
        incident
    )

    message = (
        "✅ Google Search Status Checker работает!\n\n"
        "Последнее событие Google:\n\n"
        f"📌 {title}\n"
        f"Сервис: {service}\n"
        f"Статус: "
        f"{status_name(current_status)}\n"
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

    # На самом первом запуске создаём baseline,
    # но не отправляем старые события.
    first_run = not bool(
        old_incidents
    )

    current_incidents = {}

    # ---------------------------------
    # СОБИРАЕМ ТЕКУЩЕЕ СОСТОЯНИЕ
    # ---------------------------------

    for incident in incidents:
        incident_id = incident.get(
            "id"
        )

        if not incident_id:
            continue

        update = incident.get(
            "most_recent_update",
            {},
        )

        current_incidents[
            incident_id
        ] = {
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

    # ---------------------------------
    # ПРОВЕРЯЕМ ИЗМЕНЕНИЯ
    # ---------------------------------

    if not first_run:

        # reversed нужен для того,
        # чтобы уведомления приходили
        # в хронологическом порядке.
        for incident in reversed(
            incidents
        ):
            incident_id = incident.get(
                "id"
            )

            if not incident_id:
                continue

            current = current_incidents[
                incident_id
            ]

            old = old_incidents.get(
                incident_id
            )

            # -------------------------
            # НОВЫЙ ИНЦИДЕНТ
            # -------------------------

            if old is None:
                send_telegram(
                    new_incident_message(
                        incident
                    )
                )

                continue

            # -------------------------
            # ИНЦИДЕНТ ИЗМЕНИЛСЯ
            # -------------------------
            #
            # Google меняет modified,
            # когда публикует новое обновление
            # или меняет существующее событие.

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

    # ---------------------------------
    # HEARTBEAT
    # ---------------------------------

    now = datetime.now(
        timezone.utc
    )

    # Heartbeat нужен, чтобы public GitHub
    # repository не оставался без активности
    # больше 60 дней.
    old_heartbeat = parse_datetime(
        old_state.get(
            "heartbeat"
        )
    )

    if (
        old_heartbeat is None
        or (now - old_heartbeat).days >= 30
    ):
        heartbeat = now.isoformat()
    else:
        heartbeat = old_state[
            "heartbeat"
        ]

    # ---------------------------------
    # СОХРАНЯЕМ STATE
    # ---------------------------------

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

    # ---------------------------------
    # TEST_MESSAGE
    # ---------------------------------

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
