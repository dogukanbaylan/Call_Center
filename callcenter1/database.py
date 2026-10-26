import hmac
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.getenv("APPOINTMENTS_DB", Path(__file__).with_name("appointments.db")))
STATUSES = ("Onaylandı", "İptal", "Tamamlandı")
WORK_HOURS = [f"{hour:02d}:00" for hour in range(8, 18)]
_WEEKDAYS = {
    "pazartesi": 0,
    "sali": 1,
    "carsamba": 2,
    "persembe": 3,
    "cuma": 4,
    "cumartesi": 5,
    "pazar": 6,
}
_DAY_LABELS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _fold(text: str) -> str:
    table = str.maketrans(
        {
            "ç": "c",
            "ğ": "g",
            "ı": "i",
            "ö": "o",
            "ş": "s",
            "ü": "u",
            "Ç": "c",
            "Ğ": "g",
            "İ": "i",
            "I": "i",
            "Ö": "o",
            "Ş": "s",
            "Ü": "u",
        }
    )
    return " ".join(text.translate(table).casefold().split())


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                phone TEXT,
                address TEXT,
                email TEXT,
                appointment_date TEXT NOT NULL,
                appointment_time TEXT NOT NULL,
                request_note TEXT,
                status TEXT NOT NULL DEFAULT 'Onaylandı',
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(appointments)")}
        if "service_type" not in columns:
            connection.execute(
                "ALTER TABLE appointments ADD COLUMN service_type TEXT NOT NULL DEFAULT ''"
            )
        if "issue_details" not in columns:
            connection.execute(
                "ALTER TABLE appointments ADD COLUMN issue_details TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_slot
            ON appointments(appointment_date, appointment_time)
            WHERE status != 'İptal'
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assistants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                website_url TEXT NOT NULL DEFAULT '',
                custom_notes TEXT NOT NULL,
                sector_type TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(assistants)")}
        if "voice_id" not in columns:
            connection.execute(
                "ALTER TABLE assistants ADD COLUMN voice_id TEXT NOT NULL DEFAULT ''"
            )
        if "assistant_name" not in columns:
            connection.execute(
                "ALTER TABLE assistants ADD COLUMN assistant_name TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assistant_id INTEGER NOT NULL,
                room_name TEXT NOT NULL DEFAULT '',
                write_token TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'sürüyor'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_call_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL,
                segment_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(call_id, segment_id)
            )
            """
        )


_HOUR_WORDS = {
    "bir": 1,
    "iki": 2,
    "uc": 3,
    "dort": 4,
    "bes": 5,
    "alti": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
    "on bir": 11,
    "on iki": 12,
}


def speak_hour(slot: str) -> str:
    hour = int(slot.split(":")[0])
    if hour < 12:
        return f"saat {hour}"
    if hour == 12:
        return "öğlen 12"
    if hour == 17:
        return "akşam 5"
    return f"öğleden sonra {hour - 12}"


def _join_phrases(items: list[str]) -> str:
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " ve " + items[-1]


def normalize_time(value: str | None) -> str | None:
    if not value:
        return None
    text = _fold(str(value)).replace(".", " ")
    afternoon = "ogleden sonra" in text
    evening = "aksam" in text
    noon = "oglen" in text and not afternoon
    text = text.replace("ogleden sonra", " ").replace("saat", " ")
    text = " ".join(text.split())
    hour = None
    minute = 0
    for phrase, number in sorted(_HOUR_WORDS.items(), key=lambda item: -len(item[0])):
        if phrase in text:
            hour = number
            break
    digits = [int(part) for part in text.replace(":", " ").split() if part.isdigit()]
    if hour is None and digits:
        hour = digits[0]
        if len(digits) > 1:
            minute = digits[1]
    if hour is None:
        return None
    if noon:
        hour = 12
    elif afternoon and hour < 12:
        hour += 12
    elif evening and hour == 5:
        hour = 17
    elif evening and hour < 12:
        hour += 12
    if minute != 0 or f"{hour:02d}:00" not in WORK_HOURS:
        return None
    return f"{hour:02d}:00"


def resolve_day(day: str, today: date | None = None) -> date:
    today = today or date.today()
    text = str(day).strip()
    folded = _fold(text)
    if folded in {"bugun", "bugün"}:
        return today
    if folded == "yarin":
        return today + timedelta(days=1)
    if folded in {"obur gun", "oburgun"}:
        return today + timedelta(days=2)
    if folded in _WEEKDAYS:
        target = _WEEKDAYS[folded]
        delta = (target - today.weekday()) % 7
        return today + timedelta(days=delta)
    for separator in (".", "/", "-"):
        parts = text.split(separator)
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            if len(parts[0]) == 4:
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
    raise ValueError("Günü anlayamadım. Gün adını veya GG.AA.YYYY biçimini kullanın.")


def day_label(value: date) -> str:
    return _DAY_LABELS[value.weekday()]


def booked_times(appointment_date: str) -> set[str]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT appointment_time FROM appointments
            WHERE appointment_date = ? AND status != 'İptal'
            """,
            (appointment_date,),
        ).fetchall()
    return {row["appointment_time"] for row in rows}


def free_slots(appointment_date: str) -> list[str]:
    taken = booked_times(appointment_date)
    return [slot for slot in WORK_HOURS if slot not in taken]


def _alternatives(slots: list[str], requested: str | None) -> list[str]:
    if not slots:
        return []
    if requested and requested in WORK_HOURS:
        later = [slot for slot in slots if slot > requested]
        earlier = [slot for slot in reversed(slots) if slot < requested]
        return (later + earlier)[:2]
    return slots[:2]


def open_day_names(today: date | None = None, skip: date | None = None) -> list[str]:
    today = today or date.today()
    names = []
    seen = set()
    for offset in range(21):
        current = today + timedelta(days=offset)
        if current == skip:
            seen.add(day_label(current))
            continue
        if current.weekday() == 6:
            continue
        label = day_label(current)
        if label in seen or not free_slots(current.isoformat()):
            continue
        seen.add(label)
        names.append(label)
        if len(names) == 6:
            break
    return names


def _hours_message(label: str, slots: list[str]) -> str:
    spoken = _join_phrases(speak_hour(slot) for slot in slots)
    return f"{label} günü {spoken} boş. Hangisini istersiniz?"


def first_open_slot(today: date | None = None, skip: date | None = None) -> tuple[date | None, str | None]:
    today = today or date.today()
    for offset in range(21):
        current = today + timedelta(days=offset)
        if current == skip or current.weekday() == 6:
            continue
        slots = free_slots(current.isoformat())
        if slots:
            return current, slots[0]
    return None, None


def _full_day_message(label: str, today: date, skip: date) -> str:
    current, slot = first_open_slot(today, skip=skip)
    if current is None or slot is None:
        return f"{label} günü tamamen dolu. Önümüzdeki günlerde de boş saat kalmadı."
    return (
        f"{label} günü tamamen dolu. "
        f"İlk boş randevu {day_label(current)} günü {speak_hour(slot)}."
    )


def service_label(value: str) -> str:
    folded = _fold(value or "")
    if "ariza" in folded:
        return "[ARIZA RANDEVUSU]"
    if "kurulum" in folded:
        return "[KURULUM RANDEVUSU]"
    return ""


def normalize_service(value: str) -> str:
    folded = _fold(value or "")
    if "ariza" in folded:
        return "Arıza"
    if "kurulum" in folded:
        return "Kurulum"
    return ""


def check_availability(day: str, time: str | None = None) -> dict:
    today = date.today()
    if not str(day).strip():
        others = open_day_names(today)
        return {
            "ok": False,
            "free_days": others,
            "message": (
                f"Boş randevusu olan günler: {_join_phrases(others)}. Hangisini istersiniz?"
                if others
                else "Önümüzdeki günlerde boş saat kalmadı."
            ),
        }
    appointment_day = resolve_day(day, today)
    label = day_label(appointment_day)
    iso_date = appointment_day.isoformat()
    if appointment_day.weekday() == 6:
        others = open_day_names(today, skip=appointment_day)
        return {
            "ok": False,
            "date": iso_date,
            "weekday": label,
            "message": (
                "Pazar günü randevu vermiyoruz. "
                f"Boş randevusu olan günler: {_join_phrases(others)}. Hangisini istersiniz?"
            ),
        }
    slots = free_slots(iso_date)
    requested = normalize_time(time) if time else None
    if not slots:
        return {
            "ok": False,
            "date": iso_date,
            "weekday": label,
            "free_slots": [],
            "message": _full_day_message(label, today, appointment_day),
        }
    if time and requested is None:
        return {
            "ok": False,
            "date": iso_date,
            "weekday": label,
            "free_slots": slots,
            "message": (
                f"Randevular saat 8 ile akşam 5 arasında, birer saat arayla verilir. {_hours_message(label, slots)}"
            ),
        }
    if requested and requested not in slots:
        return {
            "ok": False,
            "date": iso_date,
            "weekday": label,
            "requested_time": requested,
            "free_slots": slots,
            "message": f"Üzgünüm, {label} {speak_hour(requested)} dolu. {_hours_message(label, slots)}",
        }
    if requested:
        message = f"{label} günü {speak_hour(requested)} uygun."
    else:
        message = _hours_message(label, slots)
    return {
        "ok": True,
        "date": iso_date,
        "weekday": label,
        "requested_time": requested,
        "free_slots": slots,
        "message": message,
    }


BOOKED_MESSAGE = (
    "Randevunuz oluşturuldu. Randevu tarihinizde destek ekibimiz sizinle iletişime geçecektir. "
    "Bizi tercih ettiğiniz için teşekkür ederim. Başka bir talebiniz yoksa görüşmeyi sonlandırıyorum."
)


def book_appointment(
    customer_name: str,
    phone: str,
    address: str,
    email: str,
    appointment_date: str,
    appointment_time: str,
    request_note: str = "",
    service_type: str = "",
    issue_details: str = "",
) -> dict:
    from mailer import send_appointment_email

    service = normalize_service(service_type)
    issue = str(issue_details or request_note or "").strip()
    phone_text = str(phone or "").strip()
    missing = []
    if not str(customer_name).strip():
        missing.append("ad soyad")
    if sum(character.isdigit() for character in phone_text) < 10:
        missing.append("telefon")
    if not str(address).strip():
        missing.append("adres")
    if "@" not in str(email) or "." not in str(email).split("@")[-1]:
        missing.append("e-posta")
    if not service:
        missing.append("hizmet türü")
    if not issue:
        missing.append("talep detayı")
    if missing:
        return {
            "ok": False,
            "message": "Kayıt için eksik bilgi: " + ", ".join(missing) + ".",
        }

    try:
        appointment_day = resolve_day(appointment_date)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    slot = normalize_time(appointment_time)
    if slot is None:
        return {
            "ok": False,
            "message": "Saat 8 ile akşam 5 arasında ve tam saat başı olmalı.",
        }

    availability = check_availability(appointment_day.isoformat(), slot)
    if not availability["ok"]:
        return availability

    created_at = datetime.now().isoformat(timespec="minutes")
    try:
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    customer_name, phone, address, email, appointment_date,
                    appointment_time, request_note, status, created_at,
                    service_type, issue_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Onaylandı', ?, ?, ?)
                """,
                (
                    customer_name.strip(),
                    phone_text,
                    address.strip(),
                    email.strip(),
                    appointment_day.isoformat(),
                    slot,
                    issue,
                    created_at,
                    service,
                    issue,
                ),
            )
            appointment_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return check_availability(appointment_day.isoformat(), slot)

    email_sent = send_appointment_email(
        to_email=email.strip(),
        name=customer_name.strip(),
        appointment_date=appointment_day.strftime("%d.%m.%Y"),
        appointment_time=slot,
        details=issue,
        service_type=service,
    )
    return {
        "ok": True,
        "id": appointment_id,
        "date": appointment_day.isoformat(),
        "time": slot,
        "email_sent": email_sent,
        "service_type": service,
        "message": BOOKED_MESSAGE,
    }


def slot_bookings(appointment_date: str) -> dict[str, dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM appointments
            WHERE appointment_date = ? AND status != 'İptal'
            """,
            (appointment_date,),
        ).fetchall()
    return {row["appointment_time"]: dict(row) for row in rows}


def calendar_view(days: int = 12, today: date | None = None) -> list[dict]:
    today = today or date.today()
    cursor = today
    result = []
    while len(result) < days:
        if cursor.weekday() != 6:
            bookings = slot_bookings(cursor.isoformat())
            result.append(
                {
                    "date": cursor.isoformat(),
                    "label": day_label(cursor),
                    "date_label": cursor.strftime("%d.%m"),
                    "booked": bool(bookings),
                    "slots": [
                        {
                            "time": slot,
                            "label": str(int(slot[:2])),
                            "booked": slot in bookings,
                            "name": bookings[slot]["customer_name"] if slot in bookings else "",
                            "service": service_label(bookings[slot].get("service_type", "")) if slot in bookings else "",
                        }
                        for slot in WORK_HOURS
                    ],
                }
            )
        cursor += timedelta(days=1)
        if cursor > today + timedelta(days=40):
            break
    return result


def list_appointments() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM appointments
            ORDER BY appointment_date, appointment_time, id
            """
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["weekday"] = day_label(date.fromisoformat(item["appointment_date"]))
            item["date_label"] = date.fromisoformat(item["appointment_date"]).strftime("%d.%m.%Y")
        except ValueError:
            item["weekday"] = ""
            item["date_label"] = item["appointment_date"]
        item["service_type"] = item.get("service_type") or ""
        item["issue_details"] = item.get("issue_details") or item.get("request_note") or ""
        item["service_label"] = service_label(item["service_type"])
        item["is_booked"] = item["status"] != "İptal"
        items.append(item)
    return items


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def format_stamp(value: str) -> str:
    months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"{moment.day} {months[moment.month - 1]} {moment.year}, {moment.hour:02d}:{moment.minute:02d}"


def save_assistant(
    company: str,
    website: str,
    notes: str,
    sector: str,
    voice: str = "",
    assistant_name: str = "",
) -> dict:
    """Aynı şirket ve aynı tanım varsa kaydı günceller, yoksa yeni asistan açar."""
    now = _stamp()
    init_db()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM assistants
            WHERE company_name = ? AND custom_notes = ? AND assistant_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (company, notes, assistant_name),
        ).fetchone()
        if row:
            connection.execute(
                """
                UPDATE assistants
                SET website_url = ?, sector_type = ?, voice_id = ?, assistant_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (website, sector, voice, assistant_name, now, row["id"]),
            )
            assistant_id = row["id"]
        else:
            cursor = connection.execute(
                """
                INSERT INTO assistants (
                    company_name, website_url, custom_notes, sector_type, voice_id,
                    assistant_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company, website, notes, sector, voice, assistant_name, now, now),
            )
            assistant_id = cursor.lastrowid
    found = get_assistant(int(assistant_id))
    if not found:
        raise RuntimeError("Asistan kaydı okunamadı.")
    return found


def list_assistants() -> list[dict]:
    init_db()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT a.*,
                (SELECT COUNT(*) FROM assistant_calls c WHERE c.assistant_id = a.id) AS call_count
            FROM assistants a
            ORDER BY a.updated_at DESC, a.id DESC
            """
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["updated_label"] = format_stamp(item["updated_at"])
        items.append(item)
    return items


def get_assistant(assistant_id: int) -> dict | None:
    init_db()
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM assistants WHERE id = ?",
            (assistant_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["updated_label"] = format_stamp(item["updated_at"])
    return item


def start_assistant_call(assistant_id: int, room_name: str) -> dict:
    now = _stamp()
    token = uuid.uuid4().hex
    init_db()
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO assistant_calls (assistant_id, room_name, write_token, started_at, status)
            VALUES (?, ?, ?, ?, 'sürüyor')
            """,
            (assistant_id, room_name, token, now),
        )
        call_id = int(cursor.lastrowid)
    return {"id": call_id, "write_token": token, "started_at": now}


def _call_owner(connection: sqlite3.Connection, call_id: int, token: str) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT id, write_token, assistant_id FROM assistant_calls WHERE id = ?",
        (call_id,),
    ).fetchone()
    if row is None or not hmac.compare_digest(str(row["write_token"]), token):
        return None
    return row


def save_call_lines(call_id: int, token: str, lines: list[dict]) -> bool:
    init_db()
    with connect() as connection:
        if _call_owner(connection, call_id, token) is None:
            return False
        now = _stamp()
        for line in lines[:40]:
            segment = " ".join(str(line.get("segment_id") or "").split())[:120]
            speaker = " ".join(str(line.get("speaker") or "").split())[:40]
            body = " ".join(str(line.get("body") or "").split())[:4000]
            if not segment or not speaker or len(speaker) > 40 or not body:
                continue
            connection.execute(
                """
                INSERT INTO assistant_call_lines (call_id, segment_id, speaker, body, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(call_id, segment_id) DO UPDATE SET
                    speaker = excluded.speaker,
                    body = excluded.body
                """,
                (call_id, segment, speaker, body, now),
            )
    return True


def finish_assistant_call(call_id: int, token: str) -> bool:
    init_db()
    with connect() as connection:
        if _call_owner(connection, call_id, token) is None:
            return False
        connection.execute(
            """
            UPDATE assistant_calls
            SET status = 'tamamlandı', ended_at = COALESCE(ended_at, ?)
            WHERE id = ?
            """,
            (_stamp(), call_id),
        )
    return True


def list_assistant_calls(assistant_id: int) -> list[dict]:
    init_db()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.assistant_id, c.started_at, c.ended_at, c.status,
                (SELECT COUNT(*) FROM assistant_call_lines l WHERE l.call_id = c.id) AS line_count,
                (
                    SELECT l.body FROM assistant_call_lines l
                    WHERE l.call_id = c.id AND l.speaker = 'Müşteri'
                    ORDER BY l.id LIMIT 1
                ) AS preview
            FROM assistant_calls c
            WHERE c.assistant_id = ?
            ORDER BY c.id DESC
            """,
            (assistant_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["started_label"] = format_stamp(item["started_at"])
        item["status_label"] = "Tamamlandı" if item["status"] == "tamamlandı" else "Sürüyor"
        item["preview"] = item["preview"] or ""
        items.append(item)
    return items


def get_assistant_call(assistant_id: int, call_id: int) -> dict | None:
    init_db()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, assistant_id, started_at, ended_at, status
            FROM assistant_calls
            WHERE id = ? AND assistant_id = ?
            """,
            (call_id, assistant_id),
        ).fetchone()
        if row is None:
            return None
        lines = connection.execute(
            """
            SELECT speaker, body, created_at
            FROM assistant_call_lines
            WHERE call_id = ?
            ORDER BY id
            """,
            (call_id,),
        ).fetchall()
    item = dict(row)
    item["started_label"] = format_stamp(item["started_at"])
    item["status_label"] = "Tamamlandı" if item["status"] == "tamamlandı" else "Sürüyor"
    item["lines"] = [dict(line) for line in lines]
    return item


def update_status(appointment_id: int, status: str) -> bool:
    if status not in STATUSES:
        return False
    try:
        with connect() as connection:
            cursor = connection.execute(
                "UPDATE appointments SET status = ? WHERE id = ?",
                (status, appointment_id),
            )
            return cursor.rowcount == 1
    except sqlite3.IntegrityError:
        return False
