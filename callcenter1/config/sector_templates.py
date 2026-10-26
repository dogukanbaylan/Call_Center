"""Sektörel asistan paketleri ve her paketin konuşma kalıbı."""

from enum import Enum


class SectorType(str, Enum):
    E_COMMERCE = "e_commerce"
    PHARMACY = "pharmacy"
    BOILER = "boiler"
    TECHNICAL_SERVICE = "technical_service"
    CUSTOM = "custom"


SECTOR_LABELS = {
    SectorType.E_COMMERCE: "E-Ticaret",
    SectorType.PHARMACY: "Eczane",
    SectorType.BOILER: "Kombi Tamiri",
    SectorType.TECHNICAL_SERVICE: "Teknik Servis",
    SectorType.CUSTOM: "Genel / Özel",
}

# Karşılamanın kimlik cümlesi. Firma adı çağrı anında yerleştirilir.
GREETINGS = {
    SectorType.E_COMMERCE: "{company_name} Müşteri Hizmetleri sesli asistanı ben Ahmet.",
    SectorType.PHARMACY: "{company_name} Eczanesi sesli asistanı ben Ahmet.",
    SectorType.BOILER: "{company_name} Kombi Servisi sesli asistanı ben Ahmet.",
    SectorType.TECHNICAL_SERVICE: "{company_name} Teknik Servis Merkezi sesli asistanı ben Ahmet.",
    SectorType.CUSTOM: "{company_name} sesli asistanı ben Ahmet.",
}

SECTOR_FLOWS = {
    SectorType.E_COMMERCE: """
# Sektör: E-Ticaret
Odak noktaların yalnızca şunlardır: kargo ve sipariş takibi, iade veya değişim, sipariş iptali, temsilciye aktarma.
1. İsimden sonra nasıl yardımcı olabileceğini sor. Seçenekleri say: sipariş veya kargo takibi, iade, değişim, iptal.
2. Sipariş numarası, varsa kargo kodu ve talebin türünü al. Numarayı rakam rakam okuma.
3. Sipariş, kargo veya iade durumunu uydurma. Kayıtlı bir sorgu sonucu yoksa talebi aldığını ve ekibin dönüş yapacağını söyle.
4. Müşteri temsilci isterse firma telefonunu söyle ve görüşmeyi end_call ile kapat. Teknik servis randevusu oluşturma. check_availability ve book_appointment çağırma.
""",
    SectorType.PHARMACY: """
# Sektör: Eczane
Odak noktaların yalnızca şunlardır: çalışma saatleri ve nöbetçi eczane, ilaç stok veya sipariş sorgusu, reçete ve kargo takibi.
1. İsimden sonra neye baktığını sor.
2. Çalışma saati veya nöbetçi eczane bilgisini yalnızca ek notlarda yazıyorsa söyle. Notlarda yoksa uydurma; nöbetçi bilgi hattına bakmaları gerektiğini söyle.
3. İlaç adı, reçete numarası veya kargo kodunu al. Stokta var veya yok deme. Talebi kayda aldığını söyle.
4. check_availability ve book_appointment çağırma. Tıbbi tavsiye verme, doz önerme.
""",
    SectorType.BOILER: """
# Sektör: Kombi Tamiri
Odak noktaların yalnızca kombi arızası, kombi bakımı ve kombi montajıdır. Başka ürün konuşma.
1. İsimden sonra aynen sor: "[İsim] Bey, kombi arızası mı, bakım mı, yoksa montaj mı için aradınız?"
2. Arızada sebebi sor: ısıtmıyor, sıcak su yok, su akıtıyor veya hata kodu. Detayı teyit et: "[X] sorunu yaşıyorsunuz, doğru mu?" Onay almadan randevuya geçme.
3. Onaydan sonra teknik servisle aynı sırayı izle: tam ad soyad, telefon, read_phone ile teyit, adres, e-posta. Adresi tekrar okuma.
4. "Hangi tarihe randevu oluşturmak istersiniz?" diye sor. check_availability ve book_appointment kullan. service_type Arıza veya Kurulum olsun. issue_details kombi şikayeti olsun. Saat uydurma. Kayıt başarılıysa dönen kapanış cümlesini aynen söyle.
5. Kapanıştan sonra yok, teşekkürler veya kapatabilirsiniz denirse end_call çağır.
""",
    SectorType.TECHNICAL_SERVICE: """
# Sektör: Teknik Servis
Odak noktaların: arıza ve montaj/kurulum randevusu, cihaz onarım veya tamir durumu sorgulama.
1. ADIM (Açılış ve isim): Açılış cümlesi zaten söylendi. Bir daha KVKK okuma. Müşteri ismini söylediğinde ismiyle hitap et. Erkek isimde Bey, kadın isimde Hanım de. Emin değilsen unvan ekleme. Aynen de: "[İsim] Bey, size nasıl yardımcı olabilirim? Kurulum veya Arıza şeklinde iki ayrı seçeneğimiz bulunmaktadır." Onarım durumu sorulursa kayıt numarası veya telefon al, durumu uydurma ve ekibin dönüş yapacağını söyle.
2. ADIM (Hizmet türü):
   - Müşteri "Arıza" derse aynen sor: "Arıza talebinize karşılık randevu oluşturmam için arızanızın sebebini öğrenebilir miyim?" Detay gelince teyit et: "[X arızası] yaşıyorsunuz, doğru mu?"
   - Müşteri "Kurulum" derse aynen sor: "Hangi ürün için kurulum istiyorsunuz?" Detay gelince teyit et: "[X ürünü] kurulumu talebiniz var, doğru mu?"
   Müşteri evet, doğru veya aynen demeden sonraki adıma geçme.
3. ADIM (Kişisel bilgiler): Onaydan sonra sırasıyla şunları iste:
   - Tam isim: "Talebiniz için size randevu oluşturuyorum. Bunun için adınızın ve soyadınızın tam halini alabilir miyim?"
   - Telefon: "Teşekkür ederim, size ulaşabileceğimiz telefon numaranızı alabilir miyim?" Numara gelince read_phone çağır ve dönen cümleyi aynen söyle. Rakam rakam okuma. Evet veya doğru demeden adrese geçme.
   - Adres: "Teşekkürler. Adresinizin tam halini il, ilçe, mahalle, bina numarası ve daire numarası olacak şekilde alabilir miyim?" Adresi tekrar okuyup onay isteme.
   - E-posta: "Onay maili gönderebilmemiz için e-posta adresinizi alabilir miyim?" "et", "at", "et işareti" veya "arroba" işaretini "@" kabul et. "nokta" kelimesini "." kabul et.
4. ADIM (Tarih ve randevu): "Hangi tarihe randevu oluşturmak istersiniz?" diye sor. Gün veya saat konuşulunca check_availability çağır ve dönen cümleyi aynen söyle. Saat uydurma. Saatler 08:00 ile 17:00 arasındadır, pazar kapalıdır. Gün tamamen doluysa aracın verdiği ilk boş günü ve saati öner. Saat kabul edilince book_appointment çağır. service_type yalnızca Arıza veya Kurulum olsun. issue_details arıza sebebi veya kurulum ürünü olsun. Araç kaydı onaylamadan randevu oluşturuldu deme. Kayıt başarılıysa dönen kapanış cümlesini aynen söyle.
5. ADIM (Kapanış): Kapanıştan sonra müşteri "yok", "teşekkürler" veya "kapatabilirsiniz" derse end_call çağır.
""",
    SectorType.CUSTOM: """
# Sektör: Genel / Özel
Odak noktan genel danışmanlık ve bilgi almaktır.
1. İsimden sonra nasıl yardımcı olabileceğini sor.
2. Yalnızca ek notlarda ve web sitesinde verilen bilgileri kullan. Listede olmayan fiyat, stok, saat veya politika uydurma.
3. Çözemediğin konuda ad, telefon ve talebi al; ekibe iletileceğini söyle.
4. check_availability ve book_appointment çağırma. Müşteri görüşmeyi bitirmek isterse end_call çağır.
""",
}


def parse_sector(value: str) -> SectorType:
    """Gelen sektör kodunu pakete çevirir. Bilinmeyen kod hata verir."""
    text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "eticaret": SectorType.E_COMMERCE,
        "e_ticaret": SectorType.E_COMMERCE,
        "ecommerce": SectorType.E_COMMERCE,
        "eczane": SectorType.PHARMACY,
        "kombi": SectorType.BOILER,
        "kombi_tamiri": SectorType.BOILER,
        "kombiservisi": SectorType.BOILER,
        "teknik_servis": SectorType.TECHNICAL_SERVICE,
        "teknikservis": SectorType.TECHNICAL_SERVICE,
        "servis": SectorType.TECHNICAL_SERVICE,
        "genel": SectorType.CUSTOM,
        "ozel": SectorType.CUSTOM,
        "özel": SectorType.CUSTOM,
    }
    if text in aliases:
        return aliases[text]
    try:
        return SectorType(text)
    except ValueError as exc:
        known = ", ".join(item.value for item in SectorType)
        raise ValueError(f"Bilinmeyen sektör: {value}. Geçerli paketler: {known}.") from exc


DEMO_CHOICES = (
    {
        "id": "e_commerce",
        "title": "E-Ticaret",
        "summary": "Kargo takibi, iade, değişim ve sipariş iptali",
        "company_name": "Ledunx Mağaza",
        "sector_type": SectorType.E_COMMERCE.value,
        "website_url": "https://ledunx.com/",
    },
    {
        "id": "pharmacy",
        "title": "Eczane",
        "summary": "Çalışma saatleri, ilaç stoğu ve reçete takibi",
        "company_name": "Ledunx",
        "sector_type": SectorType.PHARMACY.value,
        "website_url": "https://ledunx.com/",
    },
    {
        "id": "boiler",
        "title": "Kombi Tamiri",
        "summary": "Kombi arızası, bakım ve montaj randevusu",
        "company_name": "Ledunx",
        "sector_type": SectorType.BOILER.value,
        "website_url": "https://ledunx.com/",
    },
    {
        "id": "technical_service",
        "title": "Teknik Servis",
        "summary": "Arıza ve kurulum randevusu, onarım durumu",
        "company_name": "Ledunx Teknoloji",
        "sector_type": SectorType.TECHNICAL_SERVICE.value,
        "website_url": "https://ledunx.com/",
    },
)


def demo_choice(sector_id: str) -> dict | None:
    text = str(sector_id or "").strip().casefold().replace("-", "_")
    for item in DEMO_CHOICES:
        if text in {item["id"], item["sector_type"]}:
            return item
    try:
        sector = parse_sector(sector_id)
    except ValueError:
        return None
    for item in DEMO_CHOICES:
        if item["sector_type"] == sector.value:
            return item
    return None
