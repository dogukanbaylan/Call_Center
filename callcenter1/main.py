import asyncio
import io
import json
import os
import re
import sys
import tempfile
import time
import wave

import edge_tts
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from groq import Groq

from database import book_appointment, check_availability, init_db

load_dotenv()

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

# llama-3.1-8b-instant bu Groq hesabında yok; çalışan sohbet modeli.
CHAT_MODEL = "openai/gpt-oss-20b"
STT_MODEL = "whisper-large-v3-turbo"
TTS_VOICE = "tr-TR-EmelNeural"
SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 24000

SYSTEM_PROMPT = """ROLÜN
Sen Ahmet'sin. Ledunx Teknoloji sanal müşteri hizmetleri asistanısın. Web sitesi: https://ledunx.com/
İnsan olduğunu iddia etme. Türkçe, sakin ve kısa konuş. Her turda en fazla iki cümle kur ve yalnızca bir soru sor.
Adımları atlama. Müşterinin verdiği bilgiyi uydurma. Adresi tekrar okuyup onay isteme.

AKIŞ
Adım 1 açılış zaten söylendi. Bir daha KVKK metnini okuma.
Adım 2: Müşteri ismini söyleyince aynen şu kalıbı kullan. Erkek isimde Bey, kadın isimde Hanım de. Emin değilsen unvan ekleme:
"[İsim] Bey, size nasıl yardımcı olabilirim? Kurulum veya Arıza şeklinde iki ayrı seçeneğimiz bulunmaktadır."
Adım 3: Yalnızca Arıza veya Kurulum yoluna gir.
Arıza denince aynen sor: "Arıza talebinize karşılık randevu oluşturmam için arızanızın sebebini öğrenebilir miyim?"
Sebep gelince onu tekrar edip sor. Örnek: "Bilgisayarınızın ekranı açılmıyor arızası yaşıyorsunuz, doğru mu?"
Kurulum denince aynen sor: "Hangi ürün için kurulum istiyorsunuz?"
Ürün gelince onu tekrar edip sor. Örnek: "Fırın kurulumu talebiniz var, doğru mu?"
Müşteri evet, doğru veya aynen demeden sonraki adıma geçme. Hayır derse detayı yeniden iste.
Adım 4: Onaydan sonra aynen sor: "Talebiniz için size randevu oluşturuyorum. Bunun için adınızın ve soyadınızın tam halini alabilir miyim?"
Tam isimden sonra aynen sor: "Teşekkür ederim, size ulaşabileceğimiz telefon numaranızı alabilir miyim?"
Numarayı rakam rakam okuma. Baştaki sıfırı "Sıfır" diye söyle, kalanını 3-3-2-2 grupla. Örnek 0533 814 75 22 için aynen şöyle teyit et: "Sıfır beş yüz otuz üç, sekiz yüz on dört, yetmiş beş, yirmi iki numaranız doğru mudur?"
Müşteri evet veya doğru demeden adrese geçme. Hayır derse numarayı yeniden iste. Kayıtta numarayı 05338147522 biçiminde tut.
Numara onaylanınca aynen sor: "Teşekkürler. Adresinizin tam halini il, ilçe, mahalle, bina numarası ve daire numarası olacak şekilde alabilir miyim?"
Adım 5: Adres gelince "Adresiniz doğru mu?" diye sorma, adresi tekrar okuma. Aynen sor: "Onay maili gönderebilmemiz için e-posta adresinizi alabilir miyim?"
E-postada et, at, et işareti ve arroba işaretini @ kabul et. nokta kelimesini . kabul et. ahmet et gmail nokta com ifadesi ahmet@gmail.com olur.
E-postadan sonra aynen sor: "Hangi tarihe randevu oluşturmak istersiniz?"
Gün veya saat konuşulunca check_availability aracını çağır. Araçtan gelen cümleyi aynen söyle. Saat uydurma.
Saatler 08:00 ile 17:00 arasındadır, pazar kapalıdır. Gün tamamen doluysa o günün dolu olduğunu söyle ve aracın verdiği ilk boş günü ve saati öner.
Müşteri günü seçince boş saatleri söyle ve hangisini istediğini sor. Saati kabul edince book_appointment çağır.
service_type yalnızca Arıza veya Kurulum olsun. issue_details arıza sebebi veya kurulum ürünü olsun.
Araç kaydı onaylamadan randevu oluşturuldu deme.
Kayıt başarılıysa müşteriye aracın verdiği kapanış cümlesini aynen söyle, yeni soru ekleme.
Adım 6 kapanış cümlesi şudur:
"Randevunuz oluşturuldu. Randevu tarihinizde destek ekibimiz sizinle iletişime geçecektir. Bizi tercih ettiğiniz için teşekkür ederim. Başka bir talebiniz yoksa görüşmeyi sonlandırıyorum."
Müşteri bundan sonra yok, teşekkürler veya kapatabilirsiniz derse yalnızca şu cümleyi söyle:
"Rica ederim, iyi günler dilerim."
"""

OPENING_MESSAGE = (
    "İyi günler, müşteri hizmetlerine hoş geldiniz. Konuşmamız KVKK kapsamında "
    "kayıt altına alınmaktadır, ilgili mevzuatı web sitemizden öğrenebilirsiniz. "
    "Ben sanal asistanınız Ahmet. Size isminizle hitap edebilmem için isminizi öğrenebilir miyim?"
)

EXIT_COMMANDS = {"çık", "cik", "kapat", "quit", "exit", "görüşmeyi bitir", "gorusmeyi bitir"}
GOODBYE_MESSAGE = "Rica ederim, iyi günler dilerim."
_STRONG_FAREWELLS = (
    "başka isteğim yok",
    "baska istegim yok",
    "başka bir isteğim yok",
    "başka bir şey yok",
    "baska bir sey yok",
    "kapatabilirsiniz",
    "kapatabilirsin",
    "iyi günler",
    "iyi gunler",
    "hoşça kal",
    "hosca kal",
    "görüşürüz",
    "gorusuruz",
    "iyi akşamlar",
    "iyi aksamlar",
)
_SOFT_FAREWELLS = (
    "teşekkürler",
    "tesekkurler",
    "teşekkür ederim",
    "tesekkur ederim",
    "sağ ol",
    "sag ol",
)
_FAREWELL_FILLER = {"çok", "cok", "size", "sana", "ya", "abi", "hanım", "bey", "efendim"}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "İstenen gün ve varsa saatin boş olup olmadığını appointments veritabanından kontrol eder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "description": "Gün adı, bugün, yarın veya YYYY-AA-GG tarihi.",
                    },
                    "time": {
                        "type": "string",
                        "description": "İstenen saat, örneğin 14:00. Gün boşluklarını soruyorsa boş bırak.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Onaylanan randevuyu kaydeder ve müşteriye onay e-postası gönderir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "address": {"type": "string"},
                    "email": {"type": "string"},
                    "appointment_date": {"type": "string"},
                    "appointment_time": {"type": "string"},
                    "request_note": {"type": "string"},
                    "service_type": {
                        "type": "string",
                        "description": "Arıza veya Kurulum.",
                    },
                    "issue_details": {
                        "type": "string",
                        "description": "Arıza sebebi veya kurulum ürünü.",
                    },
                },
                "required": [],
            },
        },
    },
]


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(f"{name} bulunamadı. .env dosyasını kontrol edin.", file=sys.stderr)
        sys.exit(1)
    return value


def samples_to_wav(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue()


def record_utterance() -> np.ndarray | None:
    frame_ms = 30
    frame_size = int(SAMPLE_RATE * frame_ms / 1000)
    max_frames = int(25_000 / frame_ms)
    start_timeout_frames = int(8_000 / frame_ms)
    silence_frames_needed = int(1_200 / frame_ms)

    print("Sizi dinliyorum. Konuşabilirsiniz.")
    speech_frames: list[np.ndarray] = []
    heard_speech = False
    silent_run = 0
    waited = 0

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=frame_size,
    ) as stream:
        noise_levels = []
        for _ in range(8):
            data, _ = stream.read(frame_size)
            noise_levels.append(float(np.abs(data).mean()))
        noise_floor = float(np.median(noise_levels))
        threshold = max(noise_floor * 4, noise_floor + 250)

        while len(speech_frames) < max_frames:
            data, _ = stream.read(frame_size)
            level = float(np.abs(data).mean())
            if level >= threshold:
                heard_speech = True
                silent_run = 0
                speech_frames.append(data.copy())
            elif heard_speech:
                speech_frames.append(data.copy())
                silent_run += 1
                if silent_run >= silence_frames_needed:
                    break
            else:
                waited += 1
                if waited >= start_timeout_frames:
                    return None

    if not speech_frames:
        return None
    return np.concatenate(speech_frames).reshape(-1)


def transcribe(client: Groq, audio_bytes: bytes, filename: str = "speech.wav") -> str:
    result = client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model=STT_MODEL,
        language="tr",
        response_format="json",
    )
    return (result.text or "").strip()


_ONES = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_DIGIT_WORDS = {
    "sıfır": "0",
    "sifir": "0",
    "bir": "1",
    "iki": "2",
    "üç": "3",
    "uc": "3",
    "dört": "4",
    "dort": "4",
    "beş": "5",
    "bes": "5",
    "altı": "6",
    "alti": "6",
    "yedi": "7",
    "sekiz": "8",
    "dokuz": "9",
}
_DIGIT_WORD_PATTERN = "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True))
_SPOKEN_PHONE = re.compile(
    rf"(?i)(?<!\w)(?:{_DIGIT_WORD_PATTERN})(?:[\s,;]+(?:{_DIGIT_WORD_PATTERN})){{9,12}}(?!\w)"
)
_PHONE_DIGITS = re.compile(r"(?<!\d)(?:\+?\s*90[\s.\-]*)?(?:\d[\s.\-]*){9,13}\d(?!\d)")
_EMAIL_SPEECH = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9._%+\-]*)\s+"
    r"(?:et\s+işareti|et\s+isareti|arroba|et|at)\s+"
    r"("
    r"[a-z0-9][a-z0-9\-]*(?:\s+(?:nokta|dot)\s+[a-z0-9\-]+)+(?:\s+[a-z]{2,})?"
    r"|[a-z0-9][a-z0-9\-]*\s+(?:com|net|org)(?:\s+tr)?"
    r"|[a-z0-9][a-z0-9.\-]+\.[a-z]{2,}"
    r")"
)


def _fold_tr(word: str) -> str:
    return word.replace("İ", "i").replace("I", "ı").lower()


def number_to_turkish(value: int) -> str:
    if value < 10:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        if ones == 0:
            return _TENS[tens]
        return f"{_TENS[tens]} {_ONES[ones]}"
    hundreds, rest = divmod(value, 100)
    hundred_part = "yüz" if hundreds == 1 else f"{_ONES[hundreds]} yüz"
    if rest == 0:
        return hundred_part
    return f"{hundred_part} {number_to_turkish(rest)}"


def _group_to_speech(group: str) -> str:
    zeros = len(group) - len(group.lstrip("0"))
    rest = group[zeros:]
    prefix = " ".join(["sıfır"] * zeros)
    if not rest:
        return prefix
    spoken = number_to_turkish(int(rest))
    return f"{prefix} {spoken}".strip()


def phone_to_speech(digits: str) -> str:
    digits = re.sub(r"\D", "", digits)
    if digits.startswith("90") and len(digits) >= 12:
        digits = "0" + digits[2:]
    leading_zero = digits.startswith("0") and len(digits) == 11
    if leading_zero:
        digits = digits[1:]
    if len(digits) == 10:
        groups = [digits[0:3], digits[3:6], digits[6:8], digits[8:10]]
    elif len(digits) == 7:
        groups = [digits[0:3], digits[3:5], digits[5:7]]
    else:
        groups = []
        index = 0
        while index < len(digits):
            remaining = len(digits) - index
            if remaining == 4:
                groups.extend([digits[index : index + 2], digits[index + 2 : index + 4]])
                break
            size = 3 if remaining > 2 else remaining
            groups.append(digits[index : index + size])
            index += size
    spoken = ", ".join(_group_to_speech(group) for group in groups)
    if leading_zero:
        return f"Sıfır {spoken}"
    return spoken[:1].upper() + spoken[1:]


def prepare_speech(text: str) -> str:
    def replace_spoken(match: re.Match) -> str:
        words = re.findall(r"[^\W\d_]+", match.group(0), flags=re.UNICODE)
        digits = "".join(_DIGIT_WORDS[_fold_tr(word)] for word in words)
        if len(digits) < 10:
            return match.group(0)
        return phone_to_speech(digits)

    def replace_digits(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if not 10 <= len(digits) <= 13:
            return match.group(0)
        return phone_to_speech(digits)

    text = _SPOKEN_PHONE.sub(replace_spoken, text)
    return _PHONE_DIGITS.sub(replace_digits, text)


def normalize_spoken_email(text: str) -> str:
    def replace_email(match: re.Match) -> str:
        local = re.sub(r"\s+", "", match.group(1))
        domain = match.group(2)
        domain = re.sub(r"(?i)\s*(?:nokta|dot)\s*", ".", domain)
        domain = re.sub(r"(?i)\s+(com|net|org)\s+tr\b", r".\1.tr", domain)
        domain = re.sub(r"(?i)\s+(com|net|org)\b", r".\1", domain)
        domain = re.sub(r"\s+", "", domain)
        return f"{local}@{domain}"

    return _EMAIL_SPEECH.sub(replace_email, text)


def speech_excerpt(text: str, limit: int = 140) -> str:
    spoken = prepare_speech(text).strip()
    if len(spoken) <= limit:
        return spoken
    cut = spoken[:limit]
    for mark in (".", "!", "?"):
        position = cut.rfind(mark)
        if position > 40:
            return cut[: position + 1]
    return cut.rsplit(" ", 1)[0]


def synthesize_browser(text: str, limit: int | None = 360) -> tuple[bytes, str]:
    spoken = prepare_speech(text).strip()
    if limit is not None and len(spoken) > limit:
        spoken = speech_excerpt(spoken, limit)
    if not spoken:
        return b"", "audio/mpeg"

    async def _run() -> bytes:
        communicate = edge_tts.Communicate(spoken, TTS_VOICE)
        chunks: list[bytes] = []
        async for part in communicate.stream():
            if part["type"] == "audio":
                chunks.append(part["data"])
        return b"".join(chunks)

    return asyncio.run(_run()), "audio/mpeg"


def speak(text: str) -> None:
    audio, _mime = synthesize_browser(text, limit=None)
    if not audio:
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as handle:
        handle.write(audio)
        path = handle.name
    try:
        import pygame

        pygame.mixer.init()
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        pygame.mixer.quit()
    finally:
        os.remove(path)


def is_farewell(text: str, previous: str = "") -> bool:
    compact = re.sub(r"[^\w\s@]", " ", text.casefold(), flags=re.UNICODE)
    compact = re.sub(r"\s+", " ", compact).strip()
    if "@" in text or re.search(r"\d", text):
        return False
    closing = previous.casefold()
    if compact in {"yok", "hayır", "hayir", "yok teşekkürler", "yok tesekkurler"}:
        return "sonlandır" in closing
    if any(phrase in compact for phrase in _STRONG_FAREWELLS) and len(compact) <= 80:
        return True
    if not any(phrase in compact for phrase in _SOFT_FAREWELLS):
        return False
    remainder = compact
    for phrase in _SOFT_FAREWELLS:
        remainder = remainder.replace(phrase, " ")
    words = [word for word in remainder.split() if word not in _FAREWELL_FILLER]
    return not words


def run_tool(name: str, arguments: str) -> dict:
    try:
        payload = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "message": "Araç parametreleri okunamadı."}
    if name == "check_availability":
        day = str(payload.get("day") or "").strip()
        if not day:
            return {
                "ok": False,
                "message": "Gün belirtilmedi. Müşteriye hangi günü istediğini sor.",
            }
        try:
            return check_availability(day, payload.get("time"))
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
    if name == "book_appointment":
        return book_appointment(
            customer_name=payload.get("customer_name", ""),
            phone=payload.get("phone", ""),
            address=payload.get("address", ""),
            email=payload.get("email", ""),
            appointment_date=payload.get("appointment_date", ""),
            appointment_time=payload.get("appointment_time", ""),
            request_note=payload.get("request_note", ""),
            service_type=payload.get("service_type", ""),
            issue_details=payload.get("issue_details", ""),
        )
    return {"ok": False, "message": "Bilinmeyen araç."}


_SCHEDULE_HINTS = (
    "randevu",
    "saat",
    "bugün",
    "bugun",
    "yarın",
    "yarin",
    "pazartesi",
    "salı",
    "sali",
    "çarşamba",
    "carsamba",
    "perşembe",
    "persembe",
    "cuma",
    "cumartesi",
    "pazar",
    "öğleden",
    "ogleden",
    "akşam",
    "aksam",
    "sabah",
    "müsait",
    "musait",
    "dolu",
    "boş",
    "bos",
)


def schedule_turn(messages: list[dict]) -> bool:
    user = ""
    assistant = ""
    for message in reversed(messages):
        role = message.get("role")
        if role == "user" and not user:
            user = message.get("content") or ""
        elif role == "assistant" and not assistant and not message.get("tool_calls"):
            assistant = message.get("content") or ""
        if user and assistant:
            break
    folded = user.casefold()
    if any(hint in folded for hint in _SCHEDULE_HINTS):
        return True
    return bool(
        re.search(r"\d", user)
        and any(word in assistant.casefold() for word in ("saat", "gün", "gun", "randevu", "hangisini"))
    )


def reply_as_ahmet(client: Groq, messages: list[dict]) -> str:
    use_tools = schedule_turn(messages)
    for _ in range(2 if use_tools else 1):
        try:
            kwargs = {
                "model": CHAT_MODEL,
                "messages": messages,
                "temperature": 0.4,
            }
            if use_tools:
                kwargs["tools"] = TOOLS
                kwargs["tool_choice"] = "auto"
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "tool" not in str(exc).lower():
                raise
            fallback = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=0.4,
            )
            return (fallback.choices[0].message.content or "").strip()
        message = response.choices[0].message
        tool_calls = message.tool_calls or []
        if not tool_calls:
            text = (message.content or "").strip()
            if text:
                return text
            plain = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=0.4,
            )
            return (plain.choices[0].message.content or "").strip()
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            result = run_tool(call.function.name, call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    return "Kaydı şu an tamamlayamadım. Bilgileri bir kez daha paylaşır mısınız?"


def main() -> None:
    groq_api_key = require_env("GROQ_API_KEY")
    init_db()
    client = Groq(api_key=groq_api_key)

    try:
        microphone = sd.query_devices(kind="input")
    except Exception as exc:
        print(f"Mikrofon bulunamadı: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Mikrofon: {microphone['name']}")
    print("Çıkmak için 'çık' deyin veya Ctrl+C kullanın.\n")

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OPENING_MESSAGE},
    ]
    print(f"Ahmet: {OPENING_MESSAGE}\n")
    try:
        speak(OPENING_MESSAGE)
    except Exception as exc:
        print(f"Açılış sesi çalınamadı: {exc}\n")

    while True:
        try:
            samples = record_utterance()
        except KeyboardInterrupt:
            print("\nGörüşmek üzere.")
            break

        if samples is None or samples.size < SAMPLE_RATE * 0.4:
            print("Sizi duyamadım. Tekrar konuşabilirsiniz.\n")
            continue

        try:
            user_text = transcribe(client, samples_to_wav(samples))
        except Exception as exc:
            print(f"Ses metne çevrilemedi: {exc}\n")
            continue

        if not user_text:
            print("Sizi duyamadım. Tekrar konuşabilirsiniz.\n")
            continue

        user_text = normalize_spoken_email(user_text)
        print(f"Siz: {user_text}")
        if user_text.lower().strip(" .,!?") in EXIT_COMMANDS:
            print("Görüşmek üzere.")
            break
        if is_farewell(user_text):
            print(f"Ahmet: {GOODBYE_MESSAGE}\n")
            try:
                speak(GOODBYE_MESSAGE)
            except Exception as exc:
                print(f"Seslendirme başarısız: {exc}\n")
            print("Görüşme sonlandı.")
            break

        history_start = len(messages)
        messages.append({"role": "user", "content": user_text})
        try:
            reply = reply_as_ahmet(client, messages)
        except Exception as exc:
            del messages[history_start:]
            print(f"Yanıt alınamadı: {exc}\n")
            continue

        if not reply:
            del messages[history_start:]
            print("Ahmet boş bir yanıt döndürdü.\n")
            continue

        messages.append({"role": "assistant", "content": reply})
        print(f"Ahmet: {reply}\n")
        try:
            speak(reply)
        except Exception as exc:
            print(f"Seslendirme başarısız: {exc}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGörüşmek üzere.")
