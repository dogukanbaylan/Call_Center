import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    StopResponse,
    cli,
    function_tool,
    inference,
    room_io,
)

from config.sector_templates import SectorType
from config.voices import resolve_voice
from config.tenant_store import default_config, resolve_tenant
from database import book_appointment, check_availability, init_db
from main import GOODBYE_MESSAGE, is_farewell, normalize_spoken_email, phone_to_speech
from utils.prompt_builder import appointments_requested, generate_opening, generate_system_instruction
from utils.site_lookup import lookup_site

load_dotenv()
logger = logging.getLogger("ahmet.agent")

# Ses ve model LiveKit Cloud Inference üzerinden gider. Yerel Groq/ElevenLabs eklentisi yok.
STT_MODEL = os.getenv("LIVEKIT_STT", "deepgram/nova-3:tr")
LLM_MODEL = os.getenv("LIVEKIT_LLM", "google/gemini-3.5-flash")
TTS_MODEL = os.getenv("LIVEKIT_TTS", "cartesia/sonic-3").split(":", 1)[0]


def call_profile(metadata: str) -> dict:
    """Çağrı metadata'sından kiracıyı okur. Boşsa kayıtlı veya Ledunx paketini kullanır."""
    if metadata.strip():
        try:
            incoming = json.loads(metadata)
        except json.JSONDecodeError:
            logger.exception("Çağrı metadata'sı JSON değil")
            incoming = None
        if isinstance(incoming, dict) and incoming.get("company_name") and incoming.get("sector_type"):
            return incoming
        if isinstance(incoming, dict) and incoming.get("tenant_id"):
            return resolve_tenant(str(incoming["tenant_id"]))
    return resolve_tenant()


def instructions_for(profile: dict) -> tuple[str, str, str]:
    company = str(profile.get("company_name") or "")
    sector = str(profile.get("sector_type") or SectorType.TECHNICAL_SERVICE.value)
    website = str(profile.get("website_url") or "") or None
    notes = str(profile.get("custom_notes") or "") or None
    phone = str(profile.get("phone") or "") or None
    sip = str(profile.get("sip") or "") or None
    try:
        instruction = str(profile.get("system_instruction") or "").strip()
        opening = str(profile.get("opening") or "").strip()
        assistant_name = str(profile.get("assistant_name") or "") or None
        if not instruction:
            instruction = generate_system_instruction(
                company, sector, website, notes, phone, sip, assistant_name
            )
        if not opening:
            opening = generate_opening(company, sector, website, assistant_name)
        return instruction, opening, sector
    except Exception:
        logger.exception("Dinamik yönerge üretilemedi, varsayılan paket kullanılacak")
        fallback = default_config()
        return fallback["system_instruction"], fallback["opening"], fallback["sector_type"]


class Ahmet(Agent):
    def __init__(
        self,
        instructions: str,
        opening: str,
        sector: str,
        website: str = "",
        notes: str = "",
    ) -> None:
        super().__init__(instructions=instructions)
        self.opening = opening
        self.sector = sector
        self.website = website
        self.notes = notes

    async def on_enter(self) -> None:
        await self.session.say(self.opening, allow_interruptions=True)

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = new_message.text_content or ""
        rewritten = normalize_spoken_email(text)
        if rewritten != text:
            new_message.content = [rewritten]
            text = rewritten
        previous = ""
        for message in reversed(turn_ctx.messages()):
            if message.role == "assistant" and message.text_content:
                previous = message.text_content
                break
        if is_farewell(text, previous):
            await self.session.say(GOODBYE_MESSAGE, allow_interruptions=False)
            self.session.shutdown(drain=True)
            raise StopResponse()

    def _service_only(self) -> str | None:
        if appointments_requested(self.notes):
            return None
        return "Şirket tanımında randevu yok. Randevu açma. Tanımdaki konuya dön."

    @function_tool()
    async def site_bilgisi(self, context: RunContext, konu: str) -> str:
        """Şirketin web sitesinden müşterinin sorduğu ürün veya konunun metnini getirir.

        Ürün, fiyat, özellik, stok, kargo, iade veya kampanya sorulunca cevap vermeden önce çağır.
        Dönen metni bir veya iki cümleyle özetle. Metinde olmayan bilgiyi söyleme.

        Args:
            konu: Müşterinin söylediği ürün veya konu. Kısa tut, örneğin 'deri ceket'.
        """
        del context
        try:
            return await asyncio.to_thread(lookup_site, self.website, konu)
        except Exception:
            logger.exception("Site okunamadı")
            return "Siteye şu an ulaşılamadı. Ürün, fiyat veya stok uydurma."

    @function_tool()
    async def read_phone(self, context: RunContext, phone: str) -> str:
        """Telefonu gruplanmış Türkçe okuyuşa çevirir. Dönen cümleyi aynen söyle.

        Args:
            phone: Rakamlarla telefon, örneğin 05338147522.
        """
        del context
        spoken = phone_to_speech(phone)
        if not spoken:
            return "Numarayı anlayamadım. Tekrar söyler misiniz?"
        return f"{spoken} numaranız doğru mudur?"

    @function_tool()
    async def check_availability(self, context: RunContext, day: str, time: str = "") -> str:
        """İstenen günün ve varsa saatin boş olup olmadığını SQLite randevu takviminden okur.

        Args:
            day: Gün adı, bugün, yarın veya tarih.
            time: İstenen saat. Yalnızca gün soruluyorsa boş bırak.
        """
        del context
        blocked = self._service_only()
        if blocked:
            return blocked
        if not str(day).strip():
            return "Gün belirtilmedi. Müşteriye hangi günü istediğini sor."
        result = await asyncio.to_thread(check_availability, day, time or None)
        return str(result.get("message") or "Uygunluk okunamadı.")

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext,
        customer_name: str,
        phone: str,
        address: str,
        email: str,
        appointment_date: str,
        appointment_time: str,
        service_type: str,
        issue_details: str,
    ) -> str:
        """Randevuyu appointments.db dosyasına yazar ve onay e-postasını gönderir.

        Args:
            customer_name: Ad ve soyad.
            phone: Telefon, rakamlarla.
            address: İl, ilçe, mahalle, bina ve daire.
            email: E-posta, ahmet@gmail.com biçiminde.
            appointment_date: Gün adı veya tarih.
            appointment_time: Saat.
            service_type: Arıza veya Kurulum.
            issue_details: Arıza sebebi veya kurulum ürünü.
        """
        del context
        blocked = self._service_only()
        if blocked:
            return blocked
        result = await asyncio.to_thread(
            book_appointment,
            customer_name,
            phone,
            address,
            email,
            appointment_date,
            appointment_time,
            issue_details,
            service_type,
            issue_details,
        )
        return str(result.get("message") or "Kayıt tamamlanamadı.")

    @function_tool()
    async def end_call(self, context: RunContext) -> None:
        """Müşteri veda edince görüşmeyi kapatır."""
        del context
        await self.session.say(GOODBYE_MESSAGE, allow_interruptions=False)
        self.session.shutdown(drain=True)
        raise StopResponse()


server = AgentServer()


@server.rtc_session(agent_name="customer-support-997")
async def entrypoint(ctx: JobContext) -> None:
    init_db()
    try:
        metadata = ctx.job.metadata or ""
    except Exception:
        logger.exception("Çağrı metadata'sı okunamadı")
        metadata = ""
    profile = call_profile(metadata)
    instruction, opening, sector = instructions_for(profile)
    session = AgentSession(
        stt=STT_MODEL,
        llm=LLM_MODEL,
        tts=inference.TTS(
            model=TTS_MODEL,
            voice=resolve_voice(str(profile.get("voice_id") or "")),
            language="tr",
        ),
    )
    await session.start(
        agent=Ahmet(
            instruction,
            opening,
            sector,
            website=str(profile.get("website_url") or ""),
            notes=str(profile.get("custom_notes") or ""),
        ),
        room=ctx.room,
        room_options=room_io.RoomOptions(delete_room_on_close=True),
    )


if __name__ == "__main__":
    cli.run_app(server)
