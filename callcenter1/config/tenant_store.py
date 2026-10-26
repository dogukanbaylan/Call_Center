"""Kiracı paket ayarını SQLite içinde saklar. Randevu tablosuna dokunmaz."""

import re
import sqlite3
from datetime import datetime, timezone

from database import connect

from config.sector_templates import SectorType, parse_sector
from utils.prompt_builder import generate_opening, generate_system_instruction


def init_tenant_table() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_configs (
                tenant_id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                sector_type TEXT NOT NULL,
                website_url TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                sip TEXT NOT NULL DEFAULT '',
                custom_notes TEXT NOT NULL DEFAULT '',
                system_instruction TEXT NOT NULL,
                opening TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _slug(value: str) -> str:
    text = str(value or "").casefold()
    text = text.translate(str.maketrans("çğıöşü", "cgiosu"))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:48] or "tenant"


def default_config() -> dict:
    """Kayıt yokken mevcut Ledunx teknik servis hattı kullanılır."""
    payload = {
        "tenant_id": "ledunx",
        "company_name": "Ledunx Teknoloji",
        "sector_type": SectorType.TECHNICAL_SERVICE.value,
        "website_url": "https://ledunx.com/",
        "phone": "",
        "sip": "",
        "custom_notes": (
            "Ledunx Teknoloji teknik servis hattıdır. Müşterilere arıza ve kurulum randevusunda yardımcı olur."
        ),
    }
    payload["opening"] = generate_opening(
        payload["company_name"], payload["sector_type"], payload["website_url"]
    )
    payload["system_instruction"] = generate_system_instruction(
        payload["company_name"],
        payload["sector_type"],
        payload["website_url"],
        payload["custom_notes"],
    )
    return payload


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "tenant_id": row["tenant_id"],
        "company_name": row["company_name"],
        "sector_type": row["sector_type"],
        "website_url": row["website_url"],
        "phone": row["phone"],
        "sip": row["sip"],
        "custom_notes": row["custom_notes"],
        "system_instruction": row["system_instruction"],
        "opening": row["opening"],
        "updated_at": row["updated_at"],
    }


def save_tenant(payload: dict) -> dict:
    """Gelen kiracı ayarını doğrular, yönergeyi üretir ve kaydeder."""
    try:
        company_name = str(payload.get("company_name") or "").strip()
        sector = parse_sector(str(payload.get("sector_type") or ""))
        website_url = str(payload.get("website_url") or "").strip()
        phone = str(payload.get("phone") or payload.get("telefon") or "").strip()
        sip = str(payload.get("sip") or payload.get("sip_uri") or "").strip()
        custom_notes = str(payload.get("custom_notes") or payload.get("notes") or "").strip()
        tenant_id = _slug(str(payload.get("tenant_id") or company_name))
        instruction = generate_system_instruction(
            company_name,
            sector,
            website_url or None,
            custom_notes or None,
            phone or None,
            sip or None,
        )
        opening = generate_opening(company_name, sector, website_url or None)
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        init_tenant_table()
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO tenant_configs (
                    tenant_id, company_name, sector_type, website_url, phone, sip,
                    custom_notes, system_instruction, opening, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    company_name = excluded.company_name,
                    sector_type = excluded.sector_type,
                    website_url = excluded.website_url,
                    phone = excluded.phone,
                    sip = excluded.sip,
                    custom_notes = excluded.custom_notes,
                    system_instruction = excluded.system_instruction,
                    opening = excluded.opening,
                    updated_at = excluded.updated_at
                """,
                (
                    tenant_id,
                    company_name,
                    sector.value,
                    website_url,
                    phone,
                    sip,
                    custom_notes,
                    instruction,
                    opening,
                    updated_at,
                ),
            )
        return get_tenant(tenant_id) or {}
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Kiracı ayarı kaydedilemedi: {exc}") from exc


def get_tenant(tenant_id: str) -> dict | None:
    init_tenant_table()
    key = _slug(tenant_id)
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM tenant_configs WHERE tenant_id = ?",
            (key,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_active_tenant() -> dict | None:
    """En son güncellenen kiracı, arama sayfasının varsayılan hattıdır."""
    init_tenant_table()
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM tenant_configs ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return _row_to_dict(row) if row else None


def resolve_tenant(tenant_id: str = "") -> dict:
    try:
        if tenant_id.strip():
            found = get_tenant(tenant_id)
            if found:
                return found
        active = get_active_tenant()
        if active:
            return active
    except Exception:
        return default_config()
    return default_config()
