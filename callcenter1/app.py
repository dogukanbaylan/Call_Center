import asyncio
import base64
import json
import os
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template_string, request, session, url_for
from groq import Groq
from livekit.api import AccessToken, VideoGrants
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration

from config.sector_templates import SECTOR_LABELS
from config.voices import DEFAULT_VOICE, VOICES, resolve_voice, voice_label
from config.tenant_store import resolve_tenant, save_tenant
from utils.prompt_builder import (
    clean_assistant_name,
    generate_opening,
    generate_system_instruction,
    infer_sector,
)
from database import (
    STATUSES,
    calendar_view,
    finish_assistant_call,
    get_assistant,
    get_assistant_call,
    init_db,
    list_appointments,
    list_assistant_calls,
    list_assistants,
    save_assistant,
    save_call_lines,
    start_assistant_call,
    update_status,
)
from main import (
    EXIT_COMMANDS,
    GOODBYE_MESSAGE,
    OPENING_MESSAGE,
    SYSTEM_PROMPT,
    is_farewell,
    normalize_spoken_email,
    reply_as_ahmet,
    require_env,
    synthesize_browser,
    transcribe,
)

app = Flask(__name__)
app.secret_key = "ledunx-local-call"
CONVERSATIONS: dict[str, list[dict]] = {}
GREETING_AUDIO = ""
GREETING_MIME = "audio/wav"
LIVEKIT_AGENT_NAME = "customer-support-997"

PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ledunx Teknoloji randevuları</title>
  <style>
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #f4f7f8; color: #1c2834; font-family: "Segoe UI", sans-serif; }
    header { background: #000; color: #fff; padding: 28px 32px; }
    header p { margin: 6px 0 0; color: #c5d4df; }
    main { padding: 28px 32px 48px; }
    .stats { display: flex; gap: 12px; margin-bottom: 18px; }
    .stat { background: #fff; border: 1px solid #d5e0e7; padding: 14px 16px; min-width: 140px; }
    .stat span { display: block; color: #5d6d7a; font-size: 13px; }
    .stat strong { font-size: 24px; }
    .panel { background: #fff; border: 1px solid #d5e0e7; overflow: auto; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px 14px; text-align: left; vertical-align: top; border-bottom: 1px solid #e6eef2; }
    th { font-size: 13px; color: #5d6d7a; font-weight: 600; background: #f8fbfc; }
    form { display: flex; gap: 8px; align-items: center; }
    select, button { font: inherit; }
    select { padding: 6px 8px; border: 1px solid #c5d2db; background: #fff; }
    button { border: 0; background: #123044; color: #fff; padding: 7px 10px; cursor: pointer; }
    .empty { padding: 28px; color: #5d6d7a; }
    .note { max-width: 240px; }
    .legend { display: flex; gap: 16px; margin: 0 0 14px; color: #5d6d7a; font-size: 14px; }
    .swatch { display: inline-block; width: 14px; height: 14px; margin-right: 6px; vertical-align: -2px; }
    .swatch.free { background: #1f9d55; }
    .swatch.busy { background: #d64545; }
    .day { display: grid; grid-template-columns: 130px 1fr; gap: 12px; align-items: center; margin-bottom: 12px; min-width: 720px; }
    .day-name strong { display: block; }
    .day-name span { color: #5d6d7a; font-size: 13px; }
    .hours { display: grid; grid-template-columns: repeat(10, 46px); gap: 6px; }
    .slot { height: 34px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 13px; }
    .slot.free { background: #1f9d55; }
    .slot.busy { background: #d64545; }
    .day-busy { background: #fdecea; outline: 1px solid #e7b2b2; }
    .day-busy .day-name strong { color: #8a2a22; }
    .busy-date { background: #f8d0d0; color: #6e1d18; font-weight: 650; }
    .kind { display: inline-block; background: #d64545; color: #fff; padding: 3px 6px; font-size: 12px; }
    .kind.kurulum { background: #123044; }
    .banner { margin-bottom: 14px; padding: 10px 12px; background: #e7f2ea; color: #1d5c32; }
    .error { background: #fdecea; color: #8a2a22; }
  </style>
</head>
<body>
  <header>
    <h1>Ledunx Teknoloji</h1>
    <p>Randevu yönetimi · <a href="/admin" style="color:#c5d4df;">Asistanlar</a></p>
  </header>
  <main>
    {% if notice == "saved" %}<div class="banner">Randevu durumu güncellendi.</div>{% endif %}
    {% if notice == "failed" %}<div class="banner error">Durum güncellenemedi. Aynı saatte başka bir kayıt olabilir.</div>{% endif %}
    <section class="stats">
      <div class="stat"><span>Toplam</span><strong>{{ items|length }}</strong></div>
      <div class="stat"><span>Onaylandı</span><strong>{{ approved }}</strong></div>
      <div class="stat"><span>Bugün</span><strong>{{ today_count }}</strong></div>
    </section>
    <p class="legend"><span><i class="swatch free"></i>Boş</span><span><i class="swatch busy"></i>Dolu</span></p>
    <section class="panel" style="padding:16px;margin-bottom:18px;overflow-x:auto;">
      {% for day in calendar %}
      <div class="day{% if day.booked %} day-busy{% endif %}">
        <div class="day-name"><strong>{{ day.label }}{% if day.booked %} · DOLU{% endif %}</strong><span>{{ day.date_label }}</span></div>
        <div class="hours">
          {% for slot in day.slots %}
          <span class="slot {{ 'busy' if slot.booked else 'free' }}" title="{{ (slot.service ~ ' ' ~ slot.name) if slot.booked else 'Boş' }}">{{ slot.label }}</span>
          {% endfor %}
        </div>
      </div>
      {% endfor %}
    </section>
    <section class="panel">
      {% if items %}
      <table>
        <thead>
          <tr>
            <th>Ad soyad</th>
            <th>Telefon</th>
            <th>Adres</th>
            <th>E-posta</th>
            <th>Hizmet türü</th>
            <th>Talep detayı</th>
            <th>Randevu tarihi ve saati</th>
            <th>Durum</th>
          </tr>
        </thead>
        <tbody>
          {% for item in items %}
          <tr>
            <td>{{ item.customer_name }}</td>
            <td>{{ item.phone }}</td>
            <td>{{ item.address }}</td>
            <td>{{ item.email }}</td>
            <td>{% if item.service_label %}<span class="kind {{ 'kurulum' if 'KURULUM' in item.service_label else '' }}">{{ item.service_label }}</span>{% else %}—{% endif %}</td>
            <td class="note">{{ item.issue_details }}</td>
            <td class="{{ 'busy-date' if item.is_booked else '' }}">{{ item.weekday }}<br>{{ item.date_label }} {{ item.appointment_time }}</td>
            <td>
              <form method="post" action="{{ url_for('update_appointment', appointment_id=item.id) }}">
                <select name="status">
                  {% for status in statuses %}
                  <option value="{{ status }}" {% if item.status == status %}selected{% endif %}>{{ status }}</option>
                  {% endfor %}
                </select>
                <button type="submit">Kaydet</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="empty">Henüz randevu yok.</div>
      {% endif %}
    </section>
  </main>
  <script>
    setInterval(() => {
      const field = document.activeElement;
      if (field && (field.tagName === "SELECT" || field.tagName === "BUTTON")) return;
      location.reload();
    }, 8000);
  </script>
</body>
</html>"""


PUBLIC_CSS = """
  @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap");
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body { margin: 0; min-height: 100vh; background: #050505; color: #f5f5f5; font-family: Manrope, "Segoe UI", sans-serif; }
  body::before { content: ""; position: fixed; inset: 0; pointer-events: none; background: radial-gradient(900px 420px at 80% -10%, rgba(255,255,255,.08), transparent 55%); }
  a { color: inherit; }
  .nav { position: sticky; top: 0; z-index: 5; display: flex; justify-content: space-between; align-items: center; padding: 18px 7vw; background: rgba(0,0,0,.72); border-bottom: 1px solid rgba(255,255,255,.08); backdrop-filter: blur(16px); }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; letter-spacing: .16em; text-decoration: none; }
  .brand i { width: 10px; height: 10px; border-radius: 50%; background: #fff; box-shadow: 0 0 0 6px rgba(255,255,255,.08); animation: pulse 2.4s ease infinite; }
  .nav nav { display: flex; gap: 22px; }
  .nav nav a { color: #bdbdbd; text-decoration: none; font-size: 14px; transition: color .2s ease; }
  .nav nav a:hover { color: #fff; }
  .wrap { width: min(1080px, calc(100% - 40px)); margin: 0 auto; padding: 64px 0 80px; position: relative; }
  .kicker { margin: 0 0 12px; color: #9a9a9a; letter-spacing: .18em; font-size: 12px; text-transform: uppercase; }
  h1 { margin: 0; font-size: clamp(36px, 5vw, 64px); line-height: 1.02; letter-spacing: -.04em; }
  .lead { max-width: 560px; color: #a3a3a3; font-size: 17px; line-height: 1.6; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 16px; margin-top: 36px; }
  a.card, .panel { background: #0c0c0c; border: 1px solid rgba(255,255,255,.1); border-radius: 22px; }
  a.card { display: flex; flex-direction: column; gap: 10px; min-height: 190px; padding: 22px; text-decoration: none; animation: rise .7s ease both; transition: transform .28s ease, border-color .28s ease, background .28s ease; }
  a.card:nth-child(2) { animation-delay: .08s; }
  a.card:nth-child(3) { animation-delay: .16s; }
  a.card:nth-child(4) { animation-delay: .24s; }
  a.card:hover { transform: translateY(-6px); border-color: #fff; background: #111; }
  a.card em { color: #737373; font-style: normal; font-size: 12px; letter-spacing: .14em; }
  a.card strong { font-size: 26px; letter-spacing: -.03em; }
  a.card span { color: #a3a3a3; line-height: 1.5; }
  .steps { display: flex; gap: 8px; margin: 28px 0; }
  .steps b { padding: 8px 12px; border-radius: 999px; border: 1px solid rgba(255,255,255,.12); color: #8d8d8d; font-size: 13px; font-weight: 600; }
  .steps b.on { color: #000; background: #fff; border-color: #fff; }
  form.panel { padding: 28px; display: grid; gap: 18px; animation: rise .6s ease both; }
  label { display: grid; gap: 8px; font-size: 14px; color: #d4d4d4; }
  input, textarea { width: 100%; border: 1px solid rgba(255,255,255,.14); background: #050505; color: #fff; border-radius: 14px; padding: 14px 16px; font: inherit; transition: border-color .2s ease, box-shadow .2s ease; }
  textarea { min-height: 140px; resize: vertical; }
  input:focus, textarea:focus { outline: none; border-color: #fff; box-shadow: 0 0 0 4px rgba(255,255,255,.08); }
  input::placeholder, textarea::placeholder { color: rgba(255,255,255,.38); }
  button, .button { border: 0; border-radius: 999px; background: #fff; color: #000; padding: 14px 18px; font: inherit; font-weight: 700; cursor: pointer; transition: transform .2s ease, opacity .2s ease; }
  button:hover, .button:hover { transform: translateY(-2px); }
  button:disabled { opacity: .55; cursor: wait; transform: none; }
  button.end { background: #fff; color: #000; }
  .error { color: #ffb4b4; min-height: 1.2em; margin: 0; }
  .stage { display: grid; grid-template-columns: 1.1fr .9fr; gap: 28px; align-items: center; }
  .phone { background: #0c0c0c; border: 1px solid rgba(255,255,255,.1); border-radius: 28px; padding: 28px; animation: rise .6s ease both; }
  .who { display: flex; gap: 14px; align-items: center; }
  .mark { width: 56px; height: 56px; border-radius: 18px; display: grid; place-items: center; background: #fff; color: #000; font-weight: 800; }
  .status { margin: 6px 0 0; color: #a3a3a3; }
  .log { margin: 22px 0; min-height: 220px; max-height: 320px; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
  .line { padding: 12px 14px; border-radius: 16px; background: #161616; animation: rise .35s ease both; }
  .line.ahmet, .line.agent { background: #101010; border: 1px solid rgba(255,255,255,.08); }
  .who-line { display: block; font-size: 12px; color: #8d8d8d; margin-bottom: 4px; }
  @keyframes rise { from { opacity: 0; transform: translateY(18px); } to { opacity: 1; transform: none; } }
  @keyframes pulse { 50% { box-shadow: 0 0 0 10px rgba(255,255,255,0); } }
  .voices { border: 0; margin: 0; padding: 0; display: grid; gap: 10px; }
  .voices legend { font-size: 14px; color: #d4d4d4; }
  .voice-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .voice { display: flex; gap: 12px; align-items: center; margin: 0; padding: 14px 16px; border-radius: 16px; border: 1px solid rgba(255,255,255,.14); background: #050505; cursor: pointer; }
  .voice:has(input:checked) { border-color: #fff; background: #141414; }
  .voice input { width: 18px; height: 18px; margin: 0; accent-color: #fff; }
  .voice strong { display: block; font-size: 15px; }
  .voice small { color: #a3a3a3; }
  @media (max-width: 800px) { .stage, .voice-grid { grid-template-columns: 1fr; } .nav { padding: 16px 20px; } }
"""


CHOICE_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sanal asistan</title>
  <style>""" + PUBLIC_CSS + """</style>
</head>
<body>
  <header class="nav">
    <a class="brand" href="/"><i></i> ASİSTAN</a>
    <nav><a href="/admin">Üyelik</a></nav>
  </header>
  <main class="wrap">
    <p class="kicker">Şirket tanımı</p>
    <h1>Sanal asistanınızı kurun.</h1>
    <p class="lead">Asistanın adını siz koyun. Şirket adını, varsa sitenizi ve ne yapacağını yazın. Yazdığınız ada ve metne göre konuşur.</p>
    <form class="panel" method="post" action="/">
      <label>Şirket adı
        <input name="company_name" required maxlength="120" value="{{ form.company_name }}" placeholder="Şirketinizin adı">
      </label>
      <label>Sanal asistanın adı
        <input name="assistant_name" required maxlength="40" value="{{ form.assistant_name }}" placeholder="Örneğin Elif">
      </label>
      <label>Web sitesi
        <input name="website_url" maxlength="300" value="{{ form.website_url }}" placeholder="Varsa https://sirketiniz.com">
      </label>
      <label>Sanal asistan hakkında
        <textarea name="custom_notes" required maxlength="4000" placeholder="Sanal asistanım arıza talebi alacak.">{{ form.custom_notes }}</textarea>
      </label>
      <fieldset class="voices">
        <legend>Ses</legend>
        <p class="status" id="voice-status">Bir sese tıklayınca kısa bir deneme duyarsınız.</p>
        <div class="voice-grid">
          {% for voice in voices %}
          <label class="voice">
            <input type="radio" name="voice_id" value="{{ voice.id }}" {% if form.voice_id == voice.id %}checked{% endif %}>
            <span><strong>{{ voice.name }}</strong><small>{{ voice.label }}</small></span>
          </label>
          {% endfor %}
        </div>
      </fieldset>
      <p class="error">{{ error }}</p>
      <button type="submit">Sanal asistanı oluştur</button>
    </form>
  </main>
  <script>
    const notes = document.querySelector("textarea[name=custom_notes]");
    const notesHint = notes ? notes.placeholder : "";
    if (notes) {
      notes.addEventListener("focus", () => { notes.placeholder = ""; });
      notes.addEventListener("blur", () => {
        if (!notes.value.trim()) notes.placeholder = notesHint;
      });
    }
    const player = new Audio();
    const voiceStatus = document.querySelector("#voice-status");
    document.querySelectorAll(".voice").forEach((label) => {
      label.addEventListener("click", () => {
        const input = label.querySelector("input");
        if (!input) return;
        voiceStatus.textContent = "Ses hazırlanıyor";
        player.src = "/api/voices/preview/" + encodeURIComponent(input.value);
        player.play().then(() => {
          voiceStatus.textContent = "Ben sanal asistanınız.";
        }).catch(() => {
          voiceStatus.textContent = "Bu ses şu an dinlenemedi.";
        });
      });
    });
  </script>
</body>
</html>"""


CALL_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ profile.company_name }} arama</title>
  <script src="https://cdn.jsdelivr.net/npm/livekit-client@2.15.7/dist/livekit-client.umd.min.js"></script>
  <style>""" + PUBLIC_CSS + """</style>
</head>
<body>
  <header class="nav">
    <a class="brand" href="/"><i></i> {{ profile.assistant_name }}</a>
    <nav><a href="/">Başla</a><a href="/admin">Üyelik</a></nav>
  </header>
  <main class="wrap">
    <div class="stage">
      <div>
        <p class="kicker">{{ profile.focus }}</p>
        <h1>{{ profile.company_name }}</h1>
        <p class="lead">{{ profile.assistant_name }} bu şirket adına, seçtiğiniz sesle konuşacak. Ses: {{ profile.voice_label }}. Arama kendiliğinden başlamaz.</p>
      </div>
      <section class="phone">
      <div class="who">
        <div class="mark">{{ profile.assistant_name[:1] }}</div>
        <div>
          <h2 style="margin:0;font-size:22px;">{{ profile.assistant_name }}</h2>
          <p class="status" id="status">Arama bekleniyor</p>
        </div>
      </div>
      <div class="log" id="log"></div>
      <p class="error" id="error"></p>
      <div id="remote-audio" hidden></div>
      <button id="call" type="button">Aramayı başlat</button>
      </section>
    </div>
  </main>
  <script>
    const statusEl = document.querySelector("#status");
    const logEl = document.querySelector("#log");
    const errorEl = document.querySelector("#error");
    const callBtn = document.querySelector("#call");
    const audioBox = document.querySelector("#remote-audio");
    const profile = {{ profile|tojson }};
    const assistantName = profile.assistant_name || "Asistan";
    const lines = new Map();
    const pending = new Map();
    let room = null;
    let active = false;
    let closing = false;
    let callId = 0;
    let writeToken = "";
    let flushTimer = null;

    function setStatus(text) { statusEl.textContent = text; }
    function showError(text) { errorEl.textContent = text || ""; }
    function upsertLine(id, who, text) {
      let line = lines.get(id);
      if (!line) {
        line = document.createElement("div");
        line.className = "line" + (who === assistantName ? " agent" : "");
        const whoEl = document.createElement("span");
        whoEl.className = "who-line";
        whoEl.textContent = who;
        const body = document.createElement("span");
        line.append(whoEl, body);
        logEl.append(line);
        lines.set(id, line);
      }
      line.lastChild.textContent = text;
      logEl.scrollTop = logEl.scrollHeight;
      if (callId) {
        pending.set(id, { segment_id: id, speaker: who === assistantName ? assistantName : "Müşteri", body: text });
        if (!flushTimer) flushTimer = setTimeout(flushLines, 700);
      }
    }
    async function flushLines() {
      flushTimer = null;
      if (!callId || pending.size === 0) return;
      const batch = Array.from(pending.values());
      pending.clear();
      try {
        await fetch("/api/calls/" + callId + "/lines", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: writeToken, lines: batch }),
        });
      } catch (error) {}
    }
    async function closeRecord() {
      if (flushTimer) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      await flushLines();
      if (!callId) return;
      const current = callId;
      const token = writeToken;
      callId = 0;
      writeToken = "";
      try {
        await fetch("/api/calls/" + current + "/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: token }),
        });
      } catch (error) {}
    }
    async function finish(ended) {
      if (closing) return;
      closing = true;
      active = false;
      const current = room;
      room = null;
      lines.clear();
      if (current) {
        try { await current.disconnect(); } catch (error) {}
      }
      audioBox.replaceChildren();
      await closeRecord();
      callBtn.disabled = false;
      callBtn.className = "";
      callBtn.textContent = "Aramayı başlat";
      setStatus(ended ? "Görüşme sonlandı" : "Arama bekleniyor");
      closing = false;
    }
    callBtn.addEventListener("click", async () => {
      showError("");
      if (active) return finish(false);
      callBtn.disabled = true;
      setStatus(assistantName + " bağlanıyor");
      try {
        const response = await fetch("/api/livekit/join", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(profile),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Arama başlamadı");
        callId = data.call_id || 0;
        writeToken = data.write_token || "";
        const LK = window.LivekitClient;
        if (!LK) throw new Error("Arama bileşeni yüklenemedi. Sayfayı yenileyin.");
        room = new LK.Room();
        room.on(LK.RoomEvent.TrackSubscribed, (track) => {
          if (track.kind !== LK.Track.Kind.Audio) return;
          const el = track.attach();
          el.autoplay = true;
          audioBox.append(el);
          setStatus(assistantName + " konuşuyor");
        });
        room.on(LK.RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((el) => el.remove());
        });
        if (LK.RoomEvent.TranscriptionReceived) {
          room.on(LK.RoomEvent.TranscriptionReceived, (segments, participant) => {
            const who = participant && participant.isLocal ? "Siz" : assistantName;
            for (const segment of segments) {
              if (!segment.text) continue;
              upsertLine(segment.id || who + ":" + segment.text, who, segment.text);
            }
          });
        }
        room.on(LK.RoomEvent.Disconnected, () => finish(true));
        room.on(LK.RoomEvent.ParticipantConnected, () => setStatus(assistantName + " hatta"));
        logEl.replaceChildren();
        lines.clear();
        await room.connect(data.url, data.token);
        await room.startAudio();
        await room.localParticipant.setMicrophoneEnabled(true);
        active = true;
        callBtn.disabled = false;
        callBtn.className = "end";
        callBtn.textContent = "Aramayı kapat";
        setStatus("Görüşme sürüyor");
      } catch (error) {
        const denied = error && (error.name === "NotAllowedError" || error.name === "NotFoundError");
        showError(denied ? "Mikrofon izni gerekli." : (error.message || "Arama başlamadı"));
        await finish(false);
      }
    });
  </script>
</body>
</html>"""


def _draft_from_form(company: str, website: str, notes: str, voice_id: str, assistant_name: str) -> dict:
    sector = infer_sector(notes)
    chosen = resolve_voice(voice_id)
    return {
        "sector_id": sector.value,
        "sector_type": sector.value,
        "title": SECTOR_LABELS[sector],
        "focus": f"Metne göre · {SECTOR_LABELS[sector]}",
        "company_name": company,
        "website_url": website,
        "custom_notes": notes,
        "assistant_name": assistant_name,
        "voice_id": chosen,
        "voice_label": voice_label(chosen),
    }


@app.route("/", methods=["GET", "POST"])
def home():
    form = {
        "company_name": "",
        "assistant_name": "",
        "website_url": "",
        "custom_notes": "",
        "voice_id": DEFAULT_VOICE,
    }
    error = ""
    if request.method == "POST":
        form["company_name"] = request.form.get("company_name", "").strip()
        form["assistant_name"] = clean_assistant_name(request.form.get("assistant_name", ""))
        form["website_url"] = request.form.get("website_url", "").strip()
        form["custom_notes"] = request.form.get("custom_notes", "").strip()
        form["voice_id"] = resolve_voice(request.form.get("voice_id", ""))
        if len(form["company_name"]) < 2:
            error = "Şirket adını yazın."
        elif len(form["assistant_name"]) < 2:
            error = "Sanal asistanın adını yazın."
        elif form["website_url"] and " " in form["website_url"]:
            error = "Web sitesi adresinde boşluk olmasın."
        elif len(form["custom_notes"]) < 8:
            error = "Şirketin ne yaptığını ve hangi konularda yardımcı olacağınızı yazın."
        else:
            if form["website_url"] and not form["website_url"].startswith(("http://", "https://")):
                form["website_url"] = "https://" + form["website_url"]
            draft = _draft_from_form(
                form["company_name"],
                form["website_url"],
                form["custom_notes"],
                form["voice_id"],
                form["assistant_name"],
            )
            saved = save_assistant(
                draft["company_name"],
                draft["website_url"],
                draft["custom_notes"],
                draft["sector_type"],
                draft["voice_id"],
                draft["assistant_name"],
            )
            draft["assistant_id"] = saved["id"]
            session["assistant"] = draft
            return redirect(url_for("call_page"))
    return render_template_string(CHOICE_PAGE, form=form, error=error, voices=VOICES)


@app.route("/kurulum/<sector_id>")
def setup(sector_id: str):
    del sector_id
    return redirect(url_for("home"))


@app.route("/arama")
@app.route("/arama/<sector_id>")
def call_page(sector_id: str = ""):
    del sector_id
    draft = session.get("assistant") or {}
    if not draft.get("company_name") or not draft.get("custom_notes") or not draft.get("assistant_name"):
        return redirect(url_for("home"))
    draft["assistant_name"] = clean_assistant_name(draft.get("assistant_name")) or "Asistan"
    draft["voice_id"] = resolve_voice(draft.get("voice_id"))
    draft["voice_label"] = voice_label(draft["voice_id"])
    return render_template_string(CALL_PAGE, profile=draft)


def _profile_from_fields(
    company: str,
    website: str,
    notes: str,
    sector_type: str = "",
    voice_id: str = "",
    assistant_name: str = "",
) -> dict:
    del sector_type
    sector = infer_sector(notes).value
    chosen = resolve_voice(voice_id)
    name = clean_assistant_name(assistant_name) or "Asistan"
    return {
        "tenant_id": sector,
        "company_name": company,
        "assistant_name": name,
        "sector_type": sector,
        "website_url": website,
        "voice_id": chosen,
        "phone": "",
        "sip": "",
        "custom_notes": notes,
        "opening": generate_opening(company, sector, website or None, name),
        "system_instruction": generate_system_instruction(
            company,
            sector,
            website or None,
            notes or None,
            assistant_name=name,
        ),
    }


def _dispatch_metadata(tenant_id: str = "") -> str:
    profile = resolve_tenant(tenant_id)
    payload = {
        "tenant_id": profile.get("tenant_id", ""),
        "company_name": profile.get("company_name", ""),
        "assistant_name": profile.get("assistant_name", ""),
        "sector_type": profile.get("sector_type", ""),
        "website_url": profile.get("website_url", ""),
        "phone": profile.get("phone", ""),
        "sip": profile.get("sip", ""),
        "custom_notes": profile.get("custom_notes", ""),
        "system_instruction": profile.get("system_instruction", ""),
        "opening": profile.get("opening", ""),
    }
    return json.dumps(payload, ensure_ascii=False)


@app.post("/api/agent/config")
def agent_config():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON gövde gerekli."}), 400
    try:
        saved = save_tenant(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Ayar kaydedilemedi: {exc}"}), 500
    return jsonify(
        {
            "ok": True,
            "tenant_id": saved.get("tenant_id"),
            "company_name": saved.get("company_name"),
            "sector_type": saved.get("sector_type"),
            "website_url": saved.get("website_url"),
            "phone": saved.get("phone"),
            "sip": saved.get("sip"),
            "opening": saved.get("opening"),
            "system_instruction": saved.get("system_instruction"),
        }
    )


PREVIEW_LINE = "Ben sanal asistanınız."
_PREVIEW_CACHE: dict[str, bytes] = {}
_PREVIEW_LOCK = threading.Lock()
_ALLOWED_VOICES = {item["id"] for item in VOICES}


def _preview_file(voice_id: str) -> Path:
    return Path(__file__).with_name("voices") / "preview" / f"{voice_id}.wav"


async def _speak_preview(voice_id: str) -> bytes:
    from livekit.agents import inference
    from livekit.agents.utils import http_context

    model = os.getenv("LIVEKIT_TTS", "cartesia/sonic-3").split(":", 1)[0]
    async with http_context.open():
        speaker = inference.TTS(model=model, voice=voice_id, language="tr")
        try:
            frame = await speaker.synthesize(PREVIEW_LINE).collect()
            return frame.to_wav_bytes()
        finally:
            await speaker.aclose()


def _preview_audio(voice_id: str) -> bytes:
    cached = _PREVIEW_CACHE.get(voice_id)
    if cached:
        return cached
    path = _preview_file(voice_id)
    if path.exists() and path.stat().st_size > 1000:
        audio = path.read_bytes()
        _PREVIEW_CACHE[voice_id] = audio
        return audio
    audio = asyncio.run(_speak_preview(voice_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    _PREVIEW_CACHE[voice_id] = audio
    return audio


@app.get("/api/voices/preview/<voice_id>")
def voice_preview(voice_id: str):
    if voice_id not in _ALLOWED_VOICES:
        return jsonify({"error": "Bu ses yok."}), 404
    try:
        with _PREVIEW_LOCK:
            audio = _preview_audio(voice_id)
    except Exception:
        app.logger.exception("Ses denemesi hazırlanamadı")
        return jsonify({"error": "Ses denemesi hazırlanamadı."}), 502
    return Response(audio, mimetype="audio/wav")


@app.post("/api/livekit/join")
def livekit_join():
    url = os.getenv("LIVEKIT_URL", "").strip()
    if not url:
        return jsonify({"error": "LiveKit adresi tanımlı değil."}), 500
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}
    sector_type = str(body.get("sector_type") or "")
    company = str(body.get("company_name") or "").strip()
    website = str(body.get("website_url") or "").strip()
    notes = str(body.get("custom_notes") or "").strip()
    assistant_name = clean_assistant_name(str(body.get("assistant_name") or ""))
    voice_id = resolve_voice(str(body.get("voice_id") or ""))
    tenant_id = str(body.get("tenant_id") or "")
    assistant_id = body.get("assistant_id")
    room_name = "cagri-" + uuid.uuid4().hex[:12]
    identity = "musteri-" + uuid.uuid4().hex[:8]
    try:
        if company and notes:
            profile = _profile_from_fields(
                company, website, notes, voice_id=voice_id, assistant_name=assistant_name
            )
            assistant_id = save_assistant(
                company,
                website,
                notes,
                profile["sector_type"],
                profile["voice_id"],
                profile["assistant_name"],
            )["id"]
            profile["assistant_id"] = assistant_id
            metadata = json.dumps(profile, ensure_ascii=False)
        elif tenant_id:
            metadata = _dispatch_metadata(tenant_id)
            assistant_id = 0
        else:
            return jsonify({"error": "Önce şirket bilgilerini doldurun."}), 400
        call_record = (
            start_assistant_call(int(assistant_id), room_name) if assistant_id else None
        )
        token = (
            AccessToken()
            .with_identity(identity)
            .with_name("Müşteri")
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_room_config(
                RoomConfiguration(
                    agents=[
                        RoomAgentDispatch(
                            agent_name=LIVEKIT_AGENT_NAME,
                            metadata=metadata,
                        )
                    ]
                )
            )
            .to_jwt()
        )
    except Exception as exc:
        return jsonify({"error": f"Arama başlatılamadı: {exc}"}), 502
    payload = {"url": url, "token": token, "room": room_name}
    if call_record:
        payload["call_id"] = call_record["id"]
        payload["write_token"] = call_record["write_token"]
    return jsonify(payload)


def _client() -> Groq:
    return Groq(api_key=require_env("GROQ_API_KEY"))


def _begin_call() -> list[dict]:
    call_id = uuid.uuid4().hex
    session["call_id"] = call_id
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OPENING_MESSAGE},
    ]
    CONVERSATIONS[call_id] = messages
    return messages


@app.post("/api/call/start")
def call_start():
    init_db()
    _begin_call()
    audio = GREETING_AUDIO
    mime = GREETING_MIME
    warning = ""
    if not audio:
        try:
            raw, mime = synthesize_browser(OPENING_MESSAGE)
            audio = base64.b64encode(raw).decode("ascii")
        except Exception as exc:
            warning = f"Açılış sesi üretilemedi: {exc}"
            print(warning)
    return jsonify({"reply": OPENING_MESSAGE, "audio": audio, "mime": mime, "ended": False, "error": warning})


@app.post("/api/call/turn")
def call_turn():
    call_id = session.get("call_id")
    messages = CONVERSATIONS.get(call_id) if call_id else None
    if messages is None:
        return jsonify({"error": "Arama başlamadı."}), 409
    upload = request.files.get("audio")
    if upload is None:
        return jsonify({"error": "Ses kaydı gelmedi."}), 400
    filename = upload.filename or "speech.webm"
    if "." not in filename:
        filename = "speech.webm"
    try:
        user_text = normalize_spoken_email(transcribe(_client(), upload.read(), filename))
    except Exception as exc:
        return jsonify({"error": f"Ses metne çevrilemedi: {exc}"}), 502
    if not user_text:
        return jsonify({"user": "", "reply": "", "audio": "", "ended": False, "retry": True})

    previous = ""
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            previous = message["content"]
            break
    ended = user_text.lower().strip(" .,!?") in EXIT_COMMANDS or is_farewell(user_text, previous)
    if ended:
        reply = GOODBYE_MESSAGE
    else:
        history_start = len(messages)
        messages.append({"role": "user", "content": user_text})
        try:
            reply = reply_as_ahmet(_client(), messages)
        except Exception as exc:
            del messages[history_start:]
            return jsonify({"error": f"Yanıt alınamadı: {exc}"}), 502
        if not reply:
            reply = "Sizi tam anlayamadım. Sorununuzu bir kez daha söyler misiniz?"
        messages.append({"role": "assistant", "content": reply})

    if ended:
        CONVERSATIONS.pop(call_id, None)
        session.pop("call_id", None)
    try:
        raw, mime = synthesize_browser(reply)
        audio = base64.b64encode(raw).decode("ascii")
    except Exception as exc:
        return jsonify({"user": user_text, "reply": reply, "audio": "", "mime": "audio/mpeg", "ended": ended, "retry": False, "error": str(exc)})
    return jsonify({"user": user_text, "reply": reply, "audio": audio, "mime": mime, "ended": ended, "retry": False})


@app.post("/api/calls/<int:call_id>/lines")
def save_lines(call_id: int):
    body = request.get_json(silent=True) or {}
    token = str(body.get("token") or "")
    lines = body.get("lines")
    if not isinstance(lines, list):
        return jsonify({"error": "Konuşma satırı gerekli."}), 400
    if not save_call_lines(call_id, token, lines):
        return jsonify({"error": "Konuşma kaydı bulunamadı."}), 403
    return jsonify({"ok": True})


@app.post("/api/calls/<int:call_id>/end")
def end_saved_call(call_id: int):
    body = request.get_json(silent=True) or {}
    if not finish_assistant_call(call_id, str(body.get("token") or "")):
        return jsonify({"error": "Konuşma kaydı bulunamadı."}), 403
    return jsonify({"ok": True})


MEMBER_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Üyelik paneli</title>
  <style>""" + PUBLIC_CSS + """</style>
</head>
<body>
  <header class="nav">
    <a class="brand" href="/admin"><i></i> ÜYELİK</a>
    <nav>
      <a href="/admin">Asistanlar</a>
      <a href="/admin/randevular">Randevular</a>
      <a href="/">Başla</a>
    </nav>
  </header>
  <main class="wrap">
    <p class="kicker">Ücretli üyelik</p>
    <h1>Sanal asistanlarınız</h1>
    <p class="lead">Oluşturduğunuz her asistan burada durur. Birine girdiğinizde müşterileriyle yaptığı her görüşme ayrı sayfada açılır.</p>
    {% if assistants %}
    <div class="grid">
      {% for item in assistants %}
      <a class="card" href="{{ url_for('assistant_calls', assistant_id=item.id) }}">
        <em>{{ item.sector_label }}</em>
        <strong>{{ item.assistant_name }}</strong>
        <span>{{ item.company_name }}</span>
        <span>{{ item.notes_preview }}</span>
        <span>{{ item.voice_label }} · {{ item.call_count }} görüşme</span>
      </a>
      {% endfor %}
    </div>
    {% else %}
    <p class="lead">Henüz asistan yok. Ana sayfadan şirket bilgisini yazıp bir asistan oluşturun.</p>
    {% endif %}
  </main>
</body>
</html>"""


CALLS_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ assistant.assistant_name }} görüşmeleri</title>
  <style>""" + PUBLIC_CSS + """</style>
</head>
<body>
  <header class="nav">
    <a class="brand" href="/admin"><i></i> ÜYELİK</a>
    <nav>
      <a href="/admin">Asistanlar</a>
      <a href="/admin/randevular">Randevular</a>
      <a href="/">Başla</a>
    </nav>
  </header>
  <main class="wrap">
    <p class="kicker"><a href="/admin">Asistanlar</a> · {{ assistant.sector_label }}</p>
    <h1>{{ assistant.assistant_name }}</h1>
    <p class="lead">{{ assistant.company_name }}</p>
    <p class="lead">{{ assistant.custom_notes }}</p>
    {% if calls %}
    <div class="grid">
      {% for item in calls %}
      <a class="card" href="{{ url_for('call_detail', assistant_id=assistant.id, call_id=item.id) }}">
        <em>Görüşme {{ item.id }}</em>
        <strong>{{ item.status_label }}</strong>
        <span>{{ item.preview or "Henüz konuşma metni yok." }}</span>
        <span>{{ item.line_count }} mesaj · {{ item.started_label }}</span>
      </a>
      {% endfor %}
    </div>
    {% else %}
    <p class="lead">Bu asistanın henüz müşteri görüşmesi yok. Arama sayfasından bir görüşme başlatınca burada ayrı bir sayfa olarak görünür.</p>
    {% endif %}
  </main>
</body>
</html>"""


DETAIL_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Görüşme {{ call.id }}</title>
  <style>""" + PUBLIC_CSS + """
  .script { max-width: 720px; margin-top: 28px; }
  </style>
</head>
<body>
  <header class="nav">
    <a class="brand" href="/admin"><i></i> ÜYELİK</a>
    <nav>
      <a href="/admin">Asistanlar</a>
      <a href="{{ url_for('assistant_calls', assistant_id=assistant.id) }}">Görüşmeler</a>
      <a href="/">Başla</a>
    </nav>
  </header>
  <main class="wrap">
    <p class="kicker">{{ assistant.company_name }} · {{ call.started_label }}</p>
    <h1>Görüşme {{ call.id }}</h1>
    <p class="lead">{{ call.status_label }}. {{ assistant.assistant_name }} ile müşterinin bu görüşmedeki konuşması.</p>
    <section class="log script">
      {% if call.lines %}
        {% for line in call.lines %}
        <div class="line{{ ' agent' if line.speaker != 'Müşteri' else '' }}">
          <span class="who-line">{{ line.speaker }}</span>
          <span>{{ line.body }}</span>
        </div>
        {% endfor %}
      {% else %}
        <p class="lead">Bu görüşmede kayda geçen bir cümle yok.</p>
      {% endif %}
    </section>
  </main>
</body>
</html>"""


def _assistant_card(item: dict) -> dict:
    notes = item.get("custom_notes") or ""
    sector = item.get("sector_type") or ""
    try:
        label = SECTOR_LABELS[infer_sector(notes)]
    except Exception:
        label = sector or "Asistan"
    item["assistant_name"] = clean_assistant_name(item.get("assistant_name")) or "Asistan"
    item["sector_label"] = label
    item["voice_label"] = voice_label(item.get("voice_id"))
    item["notes_preview"] = notes if len(notes) <= 160 else notes[:157] + "..."
    return item


@app.route("/admin")
def admin():
    assistants = [_assistant_card(item) for item in list_assistants()]
    return render_template_string(MEMBER_PAGE, assistants=assistants)


@app.route("/admin/asistan/<int:assistant_id>")
def assistant_calls(assistant_id: int):
    assistant = get_assistant(assistant_id)
    if not assistant:
        return redirect(url_for("admin"))
    return render_template_string(
        CALLS_PAGE,
        assistant=_assistant_card(assistant),
        calls=list_assistant_calls(assistant_id),
    )


@app.route("/admin/asistan/<int:assistant_id>/gorusme/<int:call_id>")
def call_detail(assistant_id: int, call_id: int):
    assistant = get_assistant(assistant_id)
    call = get_assistant_call(assistant_id, call_id)
    if not assistant or not call:
        return redirect(url_for("admin"))
    return render_template_string(
        DETAIL_PAGE,
        assistant=_assistant_card(assistant),
        call=call,
    )


@app.route("/admin/randevular")
def appointments_admin():
    init_db()
    items = list_appointments()
    from datetime import date

    today = date.today().isoformat()
    return render_template_string(
        PAGE,
        items=items,
        calendar=calendar_view(),
        statuses=STATUSES,
        approved=sum(item["status"] == "Onaylandı" for item in items),
        today_count=sum(item["appointment_date"] == today for item in items),
        notice=request.args.get("notice"),
    )


@app.post("/admin/appointments/<int:appointment_id>/status")
def update_appointment(appointment_id: int):
    saved = update_status(appointment_id, request.form.get("status", ""))
    return redirect(url_for("appointments_admin", notice="saved" if saved else "failed"))


def warm_greeting() -> None:
    global GREETING_AUDIO, GREETING_MIME
    raw, GREETING_MIME = synthesize_browser(OPENING_MESSAGE, limit=None)
    GREETING_AUDIO = base64.b64encode(raw).decode("ascii")


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
