"""Şirket tanımına göre Ahmet'in sistem yönergesini üretir."""

from config.sector_templates import SectorType, parse_sector


def _clean(value: str | None, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _fold(text: str) -> str:
    table = str.maketrans("çğıöşüı", "cgiosui")
    return " ".join(str(text or "").translate(table).casefold().split())


def infer_sector(description: str) -> SectorType:
    """Şirket metnindeki kelimelerden hangi paketin kurallarının kullanılacağını seçer."""
    text = _fold(description)
    if "kombi" in text:
        return SectorType.BOILER
    if any(word in text for word in ("eczane", "ilac", "recete", "nobetci")):
        return SectorType.PHARMACY
    if any(word in text for word in ("e ticaret", "e-ticaret", "eticaret", "kargo", "iade", "siparis", "degisim")):
        return SectorType.E_COMMERCE
    if any(word in text for word in ("teknik servis", "ariza", "kurulum", "tamir", "montaj", "randevu")):
        return SectorType.TECHNICAL_SERVICE
    return SectorType.CUSTOM


def clean_assistant_name(value: str | None) -> str:
    """Asistan adını tek satırda ve kısa tutar."""
    return _clean(value, 40)


def generate_opening(
    company_name: str,
    sector_type: SectorType | str | None = None,
    website_url: str | None = None,
    assistant_name: str | None = None,
) -> str:
    """Karşılama cümlesi yazılan şirket adını ve asistan adını kullanır."""
    del sector_type
    company = _clean(company_name, 120) or "Firmamız"
    name = clean_assistant_name(assistant_name) or "Ahmet"
    notice = "İlgili mevzuatı web sitemizden öğrenebilirsiniz."
    if not _clean(website_url, 300):
        notice = "İlgili mevzuatı firmamızdan öğrenebilirsiniz."
    return (
        f"İyi günler, {company} müşteri hizmetleri sesli asistanı ben {name}. "
        "Konuşmamız KVKK kapsamında kayıt altına alınmaktadır, "
        f"{notice} "
        "Size isminizle hitap edebilmem için isminizi öğrenebilir miyim?"
    )


def appointments_requested(description: str | None) -> bool:
    """Randevu araçları yalnızca tanımda servis randevusu geçiyorsa açılır."""
    text = _fold(description or "")
    return any(
        word in text
        for word in ("randevu", "ariza", "kurulum", "kombi", "montaj", "tamir", "teknik servis")
    )


def generate_system_instruction(
    company_name: str,
    sector_type: SectorType | str,
    website_url: str | None = None,
    custom_notes: str | None = None,
    phone: str | None = None,
    sip: str | None = None,
    assistant_name: str | None = None,
) -> str:
    """Konuşmayı hazır sektör metnine değil, girilen şirket tanımına bağlar."""
    try:
        if not isinstance(sector_type, SectorType):
            parse_sector(sector_type)
        company = _clean(company_name, 120)
        if not company:
            raise ValueError("Firma adı boş olamaz.")
        site = _clean(website_url, 300)
        notes = _clean(custom_notes, 4000)
        if not notes:
            raise ValueError("Şirket tanımı boş olamaz.")
        phone_text = _clean(phone, 40) or "belirtilmedi"
        sip_text = _clean(sip, 120) or "belirtilmedi"
        name = clean_assistant_name(assistant_name) or "Ahmet"
        opening = generate_opening(company, None, website_url, name)
        if site:
            site_rule = (
                f"Web sitesi: {site}\n"
                "Müşteri bir ürün, fiyat, özellik, stok, kargo, iade, kampanya veya sitede "
                "olabilecek herhangi bir şey sorarsa cevap vermeden önce site_bilgisi aracını çağır. "
                "Sorguyu müşterinin söylediği ürün veya konu adıyla ver. Aracın döndürdüğü metni "
                "bir veya iki cümleyle özetle. Metinde olmayan özellik, fiyat veya stok söyleme. "
                "Araç bir şey bulamazsa bulamadığını söyle."
            )
        else:
            site_rule = (
                "Web sitesi verilmedi. site_bilgisi çağırma. Ürün, fiyat ve stok uydurma. "
                "Yalnızca şirket tanımındaki bilgiyi kullan."
            )
        if appointments_requested(notes):
            booking = (
                "Tanım randevu veya servis içeriyor. Gün ya da saat konuşulunca check_availability çağır, "
                "kabul edilince book_appointment çağır. Saat uydurma. Tanımda yazmayan hizmet için randevu açma."
            )
        else:
            booking = "Randevu oluşturma. check_availability ve book_appointment çağırma."
        return (
            f"Sen {company} firmasının kibar ve akıcı Türkçe konuşan sesli asistanı {name}'sin. "
            f"Adın {name}. Başka bir ad söyleme. "
            "İnsan olduğunu iddia etme. Her turda en fazla iki cümle kur ve yalnızca bir soru sor.\n\n"
            "Hazır bir sektör senaryosu yok. Ne soracağını ve ne cevaplayacağını aşağıdaki şirket tanımı belirler. "
            "Tanımda yazmayan konuda hizmet verme, politika uydurma.\n"
            f"Şirket tanımı:\n{notes}\n\n"
            f"{site_rule}\n"
            f"{booking}\n"
            f"Açılış cümlesi zaten söylendi. Onu tekrarlama: \"{opening}\"\n"
            "İsim alındıktan sonra tanımda yazılan konulardan nasıl yardımcı olabileceğini sor. "
            "Hazır menü okuma.\n"
            f"Firma telefonu: {phone_text}\n"
            f"SIP hattı: {sip_text}\n"
            "Telefon ve rakamları tek tek okuma. Doğal Türkçe gruplamayla oku.\n"
            "Müşteri kapanıştan sonra yok, teşekkürler veya kapatabilirsiniz derse end_call çağır."
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Sistem yönergesi üretilemedi: {exc}") from exc
