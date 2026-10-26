import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from dotenv import load_dotenv

load_dotenv()


def send_appointment_email(
    to_email: str,
    name: str,
    appointment_date: str,
    appointment_time: str,
    details: str,
    service_type: str = "",
) -> bool:
    server = os.getenv("SMTP_SERVER", "").strip()
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    sender = os.getenv("SENDER_EMAIL", "").strip()
    password = os.getenv("SENDER_PASSWORD", "").strip()
    if not all([server, sender, password, to_email]):
        print("Onay e-postası atlandı: SMTP ayarları eksik.")
        return False

    kind = "Arıza" if "arıza" in service_type.casefold() else "Kurulum" if service_type else "Randevu"
    safe_name = escape(name)
    safe_date = escape(appointment_date)
    safe_time = escape(appointment_time)
    safe_details = escape(details)
    safe_kind = escape(kind)
    text = (
        f"Sayın {name},\n\n"
        "Ledunx Teknoloji randevunuz oluşturuldu.\n"
        f"Hizmet: {kind}\n"
        f"Tarih: {appointment_date}\n"
        f"Saat: {appointment_time}\n"
        f"Detay: {details}\n\n"
        "Randevu tarihinizde destek ekibimiz sizinle iletişime geçecektir.\n\n"
        "Ledunx Teknoloji\n"
        "https://ledunx.com/\n"
    )
    html = f"""<!DOCTYPE html>
<html lang="tr">
<body style="margin:0;background:#f3f6f8;font-family:Segoe UI,Arial,sans-serif;color:#1c2834;">
  <div style="max-width:560px;margin:32px auto;background:#ffffff;border:1px solid #d5e0e7;">
    <div style="background:#123044;color:#ffffff;padding:22px 28px;">
      <div style="font-size:12px;letter-spacing:0.14em;">LEDUNX TEKNOLOJİ</div>
      <h1 style="margin:8px 0 0;font-size:22px;font-weight:600;">Randevunuz oluşturuldu</h1>
    </div>
    <div style="padding:28px;">
      <p style="margin:0 0 16px;">Sayın {safe_name},</p>
      <p style="margin:0 0 20px;">Teknik servis randevunuz kayda alındı. Bilgiler aşağıdadır.</p>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:8px 0;color:#5d6d7a;">Firma</td><td style="padding:8px 0;">Ledunx Teknoloji</td></tr>
        <tr><td style="padding:8px 0;color:#5d6d7a;">Hizmet</td><td style="padding:8px 0;">{safe_kind}</td></tr>
        <tr><td style="padding:8px 0;color:#5d6d7a;">Tarih</td><td style="padding:8px 0;">{safe_date}</td></tr>
        <tr><td style="padding:8px 0;color:#5d6d7a;">Saat</td><td style="padding:8px 0;">{safe_time}</td></tr>
        <tr><td style="padding:8px 0;color:#5d6d7a;">Detay</td><td style="padding:8px 0;">{safe_details}</td></tr>
      </table>
      <p style="margin:24px 0 0;color:#5d6d7a;">https://ledunx.com/</p>
    </div>
  </div>
</body>
</html>"""

    message = EmailMessage()
    message["Subject"] = "Ledunx Teknoloji randevu onayı"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(server, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(sender, password)
            smtp.send_message(message)
    except Exception as exc:
        print(f"Onay e-postası gönderilemedi: {exc}")
        return False
    return True
