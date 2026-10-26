"""Asistan oluşturulurken seçilebilen Cartesia sesleri."""

VOICES = (
    {
        "id": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        "name": "Jacqueline",
        "label": "Kadın, genç ve net",
    },
    {
        "id": "f31cc6a7-c1e8-4764-980c-60a361443dd1",
        "name": "Robyn",
        "label": "Kadın, sakin ve olgun",
    },
    {
        "id": "5c5ad5e7-1020-476b-8b91-fdcbe9cc313c",
        "name": "Daniela",
        "label": "Kadın, yumuşak",
    },
    {
        "id": "a167e0f3-df7e-4d52-a9c3-f949145efdab",
        "name": "Blake",
        "label": "Erkek, enerjik",
    },
)

DEFAULT_VOICE = VOICES[0]["id"]
_BY_ID = {item["id"]: item for item in VOICES}


def resolve_voice(value: str | None) -> str:
    """Listede olmayan bir ses kimliğini varsayılan sese çevirir."""
    text = str(value or "").strip()
    return text if text in _BY_ID else DEFAULT_VOICE


def voice_label(value: str | None) -> str:
    item = _BY_ID.get(resolve_voice(value))
    if not item:
        return "Jacqueline"
    return f"{item['name']} · {item['label']}"
