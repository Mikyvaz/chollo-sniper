# -*- coding: utf-8 -*-
"""
Boti Relister & Command Center v4.0 (El Bot Definitivo de Discord).
Incluye:
- Botones interactivos persistentes en cada alerta de #avisos_chollos (¡Comprado! -> Reventa, Regateo 1-Clic, Grados CeX, Descartar).
- /kits     : Centro de Reventa con IA Copywriter (tono Particular Meticuloso, 0 truncamientos JSON) y optimizador EXIF de fotos.
- /tasar    : Tasador en vivo CeX España (Algolia) + Auditoría IA de precio de reventa en 48h y compra máxima recomendada.
- /radares  : Panel de control en vivo para activar/pausar nichos y editar precios/filtros de config.json desde Discord.
- /cartera  : Contabilidad de arbitraje (Stock comprado, Registrar Venta, Capital invertido, Beneficio Neto Real y ROI %).
- /estado   : Telemetría en tiempo real de los 6 mercados (Wallapop, Vinted, Milanuncios, CashConverters, TodoColección, eBay).
- /guia y /ayuda
"""

import os
import io
import re
import json
import glob
import time
import asyncio
from datetime import datetime
from PIL import Image, ImageOps, ImageEnhance

import discord
from discord import app_commands
from discord.ext import commands
from curl_cffi import requests as cffi_requests

from core_engine import (
    DB_PATH, CONFIG_PATH, KITS_DIR,
    init_db, load_config, fetch_cex_live,
    download_item_images, enhance_and_clean_image,
    build_kit_folder_name, save_resale_kit
)

# ==================== CONFIGURACIÓN Y VARIABLES ====================
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "gemini-flash-latest").strip()
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1564559739960959036"))
RELISTER_CHANNEL_ID = int(os.environ.get("RELISTER_CHANNEL_ID", "1565139139949764708"))
SELLER_CITY = os.environ.get("SELLER_CITY", "Sevilla").strip()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

active_sessions = {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ==================== UTILIDADES DE KITS Y FOTOS ====================

def get_kit_info_by_folder(folder_path):
    if not folder_path or not os.path.exists(folder_path):
        return None
    bname = os.path.basename(folder_path)
    txt_path = os.path.join(folder_path, "ANUNCIO_LISTO.txt")
    meta_path = os.path.join(folder_path, "meta.json")
    preview_title = bname.replace("_", " ")
    est_price = ""
    tip_text = ""
    base_content = ""
    clean_photos = sorted(glob.glob(os.path.join(folder_path, "foto_*_limpia.jpg")))

    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
                preview_title = meta.get("seo_title") or meta.get("identified_model") or preview_title
                if meta.get("optimal_price"):
                    est_price = f"{float(meta['optimal_price']):.0f}€"
                tip_text = meta.get("value_add_tip", "")
        except Exception:
            pass

    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as tf:
                base_content = tf.read()
                if not est_price:
                    m_p = re.search(r"Wallapop:\s*(\d+)\s*€", base_content) or re.search(r"(\d+)\s*€", base_content)
                    if m_p:
                        est_price = f"{m_p.group(1)}€"
                m_t = re.search(r"--- TÍTULO SEO ---\s*([^\r\n]+)", base_content)
                if m_t:
                    preview_title = m_t.group(1).strip()
                m_tip = re.search(r"Tip de Valor Añadido:\s*([^\r\n]+)", base_content)
                if m_tip and not tip_text:
                    tip_text = m_tip.group(1).strip()
        except Exception as e:
            print(f"[ERROR LEYENDO KIT] {folder_path}: {e}")

    return {
        "folder": folder_path,
        "bname": bname,
        "title": preview_title,
        "est_price": est_price,
        "tip_text": tip_text,
        "base_content": base_content,
        "clean_photos_count": len(clean_photos)
    }


def get_recent_kits(limit=50):
    folders = glob.glob(os.path.join(KITS_DIR, "*"))
    folders = [f for f in folders if os.path.isdir(f)]
    folders.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    kits = []
    for f in folders[:limit]:
        info = get_kit_info_by_folder(f)
        if info:
            kits.append(info)
    return kits


def enhance_photo_organic(img_bytes):
    with Image.open(io.BytesIO(img_bytes)) as im:
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        im = im.convert("RGB")
        im = ImageOps.autocontrast(im, cutoff=0.5)
        im = ImageEnhance.Color(im).enhance(1.06)
        im = ImageEnhance.Brightness(im).enhance(1.03)
        im = ImageEnhance.Sharpness(im).enhance(1.18)

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=94, optimize=True)
        return buf.getvalue()


# ==================== IA COPYWRITER Y TASADOR (0 TRUNCAMIENTOS JSON) ====================

def call_ai_copywriter(product_name, base_text, extras_text, condition_notes, desired_price="", seller_city=SELLER_CITY):
    prompt = f"""
    Eres un particular meticuloso, ordenado y transparente vendiendo un artículo de segunda mano en Wallapop o Vinted en España.
    Tu objetivo es redactar un anuncio 100% NATURAL, creíble y profesional para particulares. Debe justificar el precio más alto del mercado inspirando total confianza, sin sonar JAMÁS como un robot, una tienda o un redactor publicitario.

    DATOS DEL PRODUCTO:
    - Producto / Modelo: {product_name}
    - Información base del kit / sniper:
    {base_text.strip()[:600]}
    - EXTRAS Y ACCESORIOS QUE INCLUYES:
    {extras_text if extras_text else "Sin extras adicionales especificados."}
    - ESTADO REAL Y DETALLES ESPECÍFICOS:
    {condition_notes if condition_notes else "En muy buen estado, totalmente probado y funcional."}
    - PRECIO / OBJETIVO DE VENTA:
    {desired_price if desired_price else "Optimizar para máxima rentabilidad rápida y justificada."}
    - CIUDAD PARA TRATO EN MANO: {seller_city}

    REGLAS DE ORO DE REDACCIÓN NATURAL (OBLIGATORIAS):
    1. TONO: Particular cuidadoso y honesto que cuida al máximo sus cosas. Cero lenguaje de marketing ni palabras grandilocuentes ("joya de la corona", "solución definitiva", "potencia tus partidas", "chollo", "ofertón", "experiencia inmersiva").
    2. PROHIBIDO USAR FRASES DE ESLOGAN: La primera línea debe empezar directamente con:
       "Vendo [Producto exacto] en un estado [excelente / impoluto / muy bueno], ideal para [uso o público]. Funciona a la perfección, muy cuidado/a."
    3. MÁXIMO 1 SIGNO DE EXCLAMACIÓN EN TODO EL TEXTO: La única exclamación permitida va en la frase final: "¡Echa un vistazo a mis valoraciones para comprar con total tranquilidad!".
    4. UBICACIÓN REAL (SIN PLACEHOLDERS):
       "Hago trato en mano en {seller_city} (puedes probarlo/a tranquilamente antes de llevártelo/a)."
    5. ESTRUCTURA EXACTA DEL TEXTO:
       Vendo [Producto exacto] en un estado [excelente / muy bueno], ideal para [uso]. Funciona a la perfección, muy cuidado/a.

       📦 ¿Qué entrego?
       • [Elemento principal]
       • [Accesorios originales: dock, cables, cargador, caja...]
       • [Extras si los hay: juegos, fundas, protector...]

       ✨ Estado real:
       • Pantalla / Exterior: [Detalle honesto]
       • Funcionamiento / Controles / Batería: [Todo probado al 100%]
       • Estética y cuidado: [Limpio y muy bien conservado]

       🤝 Entrega y envío:
       • Hago trato en mano en {seller_city} (puedes probarlo/a tranquilamente antes de llevártelo/a).
       • Hago envíos por la plataforma. Empaqueto muy bien con plástico de burbujas y caja rígida, y suelo depositarlo en Correos/InPost en menos de 24h.

       Cualquier duda o si necesitas alguna foto o vídeo extra encendido/a, escríbeme sin compromiso. ¡Echa un vistazo a mis valoraciones para comprar con total tranquilidad!

    6. TÍTULO SEO (MÁXIMO 48 CARACTERES estrictos): Nombre del modelo exacto y extras principales.

    Devuelve ÚNICAMENTE un objeto JSON válido con estas claves exactas:
    {{
      "seo_title": "Título máx 48 caracteres",
      "description": "Texto completo estructurado según las reglas anteriores",
      "listing_price": 220,
      "minimum_price": 195,
      "pricing_rationale": "Breve justificación de precio para el usuario"
    }}
    """

    if GEMINI_API_KEY:
        model = AI_MODEL if ("gemini" in AI_MODEL.lower() and "/" not in AI_MODEL) else "gemini-flash-latest"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
                "maxOutputTokens": 2500,
                "thinkingConfig": {"thinkingBudget": 0}
            }
        }
        try:
            resp = cffi_requests.post(url, json=payload, timeout=25)
            if resp.status_code == 200:
                res_data = resp.json()
                candidates = res_data.get("candidates", [])
                if candidates:
                    for p in candidates[0].get("content", {}).get("parts", []):
                        if "text" in p:
                            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", p["text"].strip(), flags=re.MULTILINE)
                            result = json.loads(clean)
                            desc = result.get("description", "")
                            desc = re.sub(r"\[(?:Tu )?Ciudad(?:/Zona)?\]", seller_city, desc, flags=re.IGNORECASE)
                            result["description"] = desc.strip()
                            return result
            else:
                print(f"[ERROR IA DIRECT GEMINI] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[ERROR IA DIRECT GEMINI] Excepción: {e}")

    if OPENROUTER_KEY:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://homelab.local",
            "X-Title": "Boti Relister"
        }
        fallback_model = AI_MODEL if "/" in AI_MODEL else "google/gemini-2.5-flash"
        payload = {
            "model": fallback_model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "response_format": {"type": "json_object"},
            "max_tokens": 1800,
            "temperature": 0.2
        }
        try:
            resp = cffi_requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["choices"][0]["message"].get("content", "")
                clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
                result = json.loads(clean)
                desc = result.get("description", "")
                desc = re.sub(r"\[(?:Tu )?Ciudad(?:/Zona)?\]", seller_city, desc, flags=re.IGNORECASE)
                result["description"] = desc.strip()
                return result
        except Exception as e:
            print(f"[ERROR IA OPENROUTER] Excepción: {e}")
    return None


def call_ai_appraiser(product_query, buy_price, cex_variants):
    cex_summary = "\n".join(
        [f"- {v['box_name']}: Efectivo {v['cash']:.0f}€ | Vale {v['voucher']:.0f}€ | PVP {v['sell']:.0f}€" for v in (cex_variants or [])[:5]]
    ) or "Sin coincidencias exactas en CeX."

    prompt = f"""
    Eres el tasador jefe de arbitraje en España. El usuario consulta por: "{product_query}" (Precio de compra planteado: {buy_price if buy_price > 0 else 'No indicado'}€).
    Datos actuales en vivo de CeX España:
    {cex_summary}

    Devuelve ÚNICAMENTE un JSON válido con:
    {{
      "clean_model_name": "Nombre exacto estandarizado del producto",
      "fast_resale_48h_eur": número entero en euros al que se vende en menos de 48h en Wallapop España,
      "patient_resale_eur": número entero en euros si se espera 1-2 semanas,
      "max_recommended_buy_eur": precio máximo al que deberías comprarlo (incluyendo envío) para ganar al menos 35€ limpios,
      "verdict": "Veredicto directo de 1-2 frases (¿Es rentable? ¿Qué tal la liquidez?)",
      "checklist_before_buying": "3 puntos clave o fallos típicos que debes revisar o preguntar al vendedor antes de pagar"
    }}
    """
    if GEMINI_API_KEY:
        model = AI_MODEL if ("gemini" in AI_MODEL.lower() and "/" not in AI_MODEL) else "gemini-flash-latest"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
                "maxOutputTokens": 1200,
                "thinkingConfig": {"thinkingBudget": 0}
            }
        }
        try:
            resp = cffi_requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    for p in candidates[0].get("content", {}).get("parts", []):
                        if "text" in p:
                            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", p["text"].strip(), flags=re.MULTILINE)
                            return json.loads(clean)
        except Exception as e:
            print(f"[WARN] Error en call_ai_appraiser: {e}")
    return None


# ==================== VISTAS Y MODALES DEL RELISTER ====================

class ExtrasModal(discord.ui.Modal, title="Personalizar Anuncio y Extras"):
    extras_input = discord.ui.TextInput(
        label="Extras incluidos (juegos, funda, SD...)",
        style=discord.TextStyle.paragraph,
        placeholder="Ej: Funda rígida, Mario Kart 8 en caja, cargador original, protector...",
        required=False,
        max_length=500
    )
    condition_input = discord.ui.TextInput(
        label="Estado real y detalles específicos",
        style=discord.TextStyle.paragraph,
        placeholder="Ej: Pantalla impecable sin arañazos, Joy-Cons sin drift, con caja...",
        required=False,
        max_length=500
    )
    price_input = discord.ui.TextInput(
        label="Precio deseado o notas (opcional)",
        style=discord.TextStyle.short,
        placeholder="Ej: Comprada por 110€, vender a 195€",
        required=False,
        max_length=100
    )

    def __init__(self, kit_info, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kit_info = kit_info

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        active_sessions[user_id] = {
            "kit_folder": self.kit_info["folder"],
            "kit_bname": self.kit_info["bname"],
            "kit_title": self.kit_info["title"],
            "base_txt": self.kit_info["base_content"],
            "extras": self.extras_input.value.strip(),
            "notes": self.condition_input.value.strip(),
            "price_note": self.price_input.value.strip(),
            "waiting_photos": False,
            "uploaded_photos": [],
            "channel_id": interaction.channel_id
        }

        embed = discord.Embed(
            title="📸 Paso Final: Selección de Fotos",
            description=(
                f"✅ **Datos guardados para:** `{self.kit_info['title']}`\n\n"
                f"**Extras indicados:** {self.extras_input.value.strip() or '*(Ninguno)*'}\n"
                f"**Estado:** {self.condition_input.value.strip() or '*(Estándar muy cuidado)*'}\n\n"
                "**¿Cómo quieres gestionar las fotos del anuncio?**\n"
                "• **Opción A (Fotos Propias):** Sube fotos desde tu móvil (limpieza EXIF/GPS y nitidez orgánica automática).\n"
                "• **Opción B (Fotos del Kit):** Usa al instante las fotos limpias descargadas por el sniper."
            ),
            color=0x2ECC71
        )
        embed.set_footer(text=f"Boti Relister v4.0 • Trato en mano en {SELLER_CITY}")
        view = PhotoChoiceView(user_id)
        await interaction.response.send_message(embed=embed, view=view)


class PhotoChoiceView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="Subir fotos desde el móvil", style=discord.ButtonStyle.success, emoji="📸")
    async def upload_mobile_photos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esta sesión pertenece a otro usuario.", ephemeral=True)
            return
        session = active_sessions.get(self.user_id)
        if not session:
            await interaction.response.send_message("La sesión ha expirado. Usa `/kits` de nuevo.", ephemeral=True)
            return

        session["waiting_photos"] = True
        embed = discord.Embed(
            title="📸 Modo Subida de Fotos Activado",
            description=(
                "**Envía ahora entre 1 y 6 fotos a este canal directamente desde tu móvil.**\n\n"
                "Cuando hayas enviado todas tus fotos, pulsa el botón verde **[ ✅ Generar Anuncio con mis Fotos ]** abajo."
            ),
            color=0x3498DB
        )
        view = PhotoUploadReadyView(self.user_id)
        await interaction.response.send_message(embed=embed, view=view)

    @discord.ui.button(label="Usar Fotos Originales del Kit", style=discord.ButtonStyle.primary, emoji="📦")
    async def use_kit_photos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esta sesión pertenece a otro usuario.", ephemeral=True)
            return
        session = active_sessions.get(self.user_id)
        if not session:
            await interaction.response.send_message("La sesión ha expirado. Usa `/kits` de nuevo.", ephemeral=True)
            return
        await interaction.response.defer()
        await finalize_listing(interaction, self.user_id, use_original_photos=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("No autorizado.", ephemeral=True)
            return
        active_sessions.pop(self.user_id, None)
        await interaction.response.edit_message(content="❌ Sesión cancelada.", embed=None, view=None)


class PhotoUploadReadyView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.user_id = user_id

    @discord.ui.button(label="Generar Anuncio con mis Fotos", style=discord.ButtonStyle.success, emoji="✅")
    async def finalize_upload(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esta sesión pertenece a otro usuario.", ephemeral=True)
            return
        session = active_sessions.get(self.user_id)
        if not session:
            await interaction.response.send_message("La sesión ha expirado. Usa `/kits` de nuevo.", ephemeral=True)
            return
        if not session.get("uploaded_photos"):
            await interaction.response.send_message("⚠️ Adjunta al menos 1 foto primero.", ephemeral=True)
            return
        await interaction.response.defer()
        await finalize_listing(interaction, self.user_id, use_original_photos=False)

    @discord.ui.button(label="Usar Fotos del Kit en su lugar", style=discord.ButtonStyle.primary, emoji="📦")
    async def fallback_kit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esta sesión pertenece a otro usuario.", ephemeral=True)
            return
        await interaction.response.defer()
        await finalize_listing(interaction, self.user_id, use_original_photos=True)


class KitSelectView(discord.ui.View):
    def __init__(self, kits):
        super().__init__(timeout=180)
        self.kits = kits
        options = []
        for idx, k in enumerate(kits[:25]):
            lbl = k["title"][:95] or k["bname"][:95]
            desc_parts = []
            if k["est_price"]:
                desc_parts.append(k["est_price"])
            if k["clean_photos_count"]:
                desc_parts.append(f"{k['clean_photos_count']} fotos")
            desc_parts.append(k["bname"][:35])
            options.append(discord.SelectOption(
                label=lbl,
                value=str(idx),
                description=" • ".join(desc_parts)[:95],
                emoji="📦"
            ))

        self.select_menu = discord.ui.Select(
            placeholder="Selecciona una compra / kit para generar su anuncio...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):
        idx = int(self.select_menu.values[0])
        kit = self.kits[idx]
        view = KitActionsView(kit)
        embed = discord.Embed(
            title=f"📦 {kit['title']}",
            description=(
                f"📁 **Carpeta:** `{kit['bname']}`\n"
                f"💰 **Precio Recomendado:** **{kit['est_price'] or 'No especificado'}**\n"
                f"🖼️ **Fotos disponibles:** **{kit['clean_photos_count']}** imágenes limpias"
            ),
            color=0x3498DB
        )
        if kit["tip_text"]:
            embed.add_field(name="💡 Tip del Sniper", value=kit["tip_text"], inline=False)
        if kit["base_content"]:
            embed.add_field(
                name="📋 Borrador Base",
                value=f"```{kit['base_content'][:600]}```",
                inline=False
            )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class KitActionsView(discord.ui.View):
    def __init__(self, kit_info):
        super().__init__(timeout=180)
        self.kit_info = kit_info

    @discord.ui.button(label="Personalizar y Generar Anuncio", style=discord.ButtonStyle.success, emoji="📝")
    async def edit_extras(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ExtrasModal(self.kit_info))

    @discord.ui.button(label="Generar Rápido (1-Clic sin cambios)", style=discord.ButtonStyle.primary, emoji="⚡")
    async def instant_generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        active_sessions[user_id] = {
            "kit_folder": self.kit_info["folder"],
            "kit_bname": self.kit_info["bname"],
            "kit_title": self.kit_info["title"],
            "base_txt": self.kit_info["base_content"],
            "extras": "",
            "notes": "",
            "price_note": self.kit_info["est_price"],
            "waiting_photos": False,
            "uploaded_photos": [],
            "channel_id": interaction.channel_id
        }
        await interaction.response.defer()
        await finalize_listing(interaction, user_id, use_original_photos=True)

    @discord.ui.button(label="Copiar Borrador Base", style=discord.ButtonStyle.secondary, emoji="📋")
    async def show_base(self, interaction: discord.Interaction, button: discord.ui.Button):
        txt = self.kit_info["base_content"] or "No hay texto disponible."
        await interaction.response.send_message(f"```text\n{txt[:1950]}\n```", ephemeral=True)


async def finalize_listing(interaction_or_channel, user_id, use_original_photos=False):
    session = active_sessions.get(user_id)
    if not session:
        return

    kit_dir = session["kit_folder"]
    output_photos = []
    channel = interaction_or_channel.channel if isinstance(interaction_or_channel, discord.Interaction) else interaction_or_channel
    status_notify = await channel.send("⏳ **Redactando anuncio natural de alta conversión y preparando fotos...**")

    if use_original_photos:
        existing = sorted(glob.glob(os.path.join(kit_dir, "foto_*_limpia.jpg")))
        for p in existing[:6]:
            try:
                with open(p, "rb") as f:
                    output_photos.append(f.read())
            except Exception:
                pass
    else:
        for raw_bytes in session.get("uploaded_photos", []):
            try:
                output_photos.append(enhance_photo_organic(raw_bytes))
            except Exception:
                output_photos.append(raw_bytes)

    if not output_photos and kit_dir and os.path.exists(kit_dir):
        existing = sorted(glob.glob(os.path.join(kit_dir, "foto_*_limpia.jpg")))
        for p in existing[:6]:
            try:
                with open(p, "rb") as f:
                    output_photos.append(f.read())
            except Exception:
                pass
        if not output_photos:
            meta_p = os.path.join(kit_dir, "meta.json")
            if os.path.exists(meta_p):
                try:
                    with open(meta_p, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                    urls = mdata.get("image_urls") or []
                    plat_m = str(mdata.get("platform", "")).upper()
                    raw_imgs = await asyncio.to_thread(
                        download_item_images, urls, None, 3, plat_m in ("EBAY", "TODOCOLECCION")
                    )
                    for idx_p, rb in enumerate(raw_imgs, start=1):
                        try:
                            clean_b = enhance_and_clean_image(rb)
                        except Exception:
                            clean_b = rb
                        output_photos.append(clean_b)
                        with open(os.path.join(kit_dir, f"foto_{idx_p}_limpia.jpg"), "wb") as pf:
                            pf.write(clean_b)
                except Exception:
                    pass

    ai_result = call_ai_copywriter(
        product_name=session["kit_title"],
        base_text=session["base_txt"],
        extras_text=session["extras"],
        condition_notes=session["notes"],
        desired_price=session.get("price_note", ""),
        seller_city=SELLER_CITY
    )

    if not ai_result:
        await status_notify.edit(content="❌ Error al invocar la IA. Inténtalo de nuevo en unos segundos.")
        return

    seo_title = ai_result.get("seo_title", session["kit_title"])[:48]
    description = ai_result.get("description", "")
    p_listing = ai_result.get("listing_price", 0)
    p_min = ai_result.get("minimum_price", 0)
    rationale = ai_result.get("pricing_rationale", "")

    if kit_dir and os.path.exists(kit_dir):
        out_dir = os.path.join(kit_dir, "anuncio_publicado")
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "ANUNCIO_FINAL.txt"), "w", encoding="utf-8") as f:
                f.write(f"TÍTULO:\n{seo_title}\n\nPRECIO SALIDA: {p_listing}€ (Mínimo: {p_min}€)\n\nDESCRIPCIÓN:\n{description}\n")
            for i, p_bytes in enumerate(output_photos, start=1):
                with open(os.path.join(out_dir, f"foto_final_{i}.jpg"), "wb") as f:
                    f.write(p_bytes)
        except Exception:
            pass

    await status_notify.delete()

    embed = discord.Embed(
        title=f"📋 Anuncio Listo: {seo_title}",
        color=0x2ECC71,
        description=(
            f"💰 **Estrategia de Precio Recomendada:**\n"
            f"• **Publicar a:** **`{p_listing}€`** (con margen para negociar)\n"
            f"• **Aceptar ofertas hasta:** **`{p_min}€`** (cierre rápido)\n"
            f"• *{rationale}*\n\n"
            f"📍 **Ubicación en mano:** `{SELLER_CITY}`\n"
            f"📱 **Copia los bloques de abajo con 1 toque en tu móvil:**"
        )
    )
    embed.set_footer(text="Boti Relister v4.0 • Redacción Natural Particular Meticuloso")

    files_to_send = [discord.File(io.BytesIO(pb), filename=f"foto_{i}.jpg") for i, pb in enumerate(output_photos[:6], start=1)]

    if isinstance(interaction_or_channel, discord.Interaction):
        await interaction_or_channel.followup.send(embed=embed)
    else:
        await channel.send(embed=embed)

    await channel.send(f"📱 **TÍTULO SEO (1 toque para copiar):**\n```text\n{seo_title}\n```")
    await channel.send(f"📝 **DESCRIPCIÓN COMPLETA (1 toque para copiar):**\n```text\n{description}\n```")
    if files_to_send:
        await channel.send("🖼️ **Fotos optimizadas sin EXIF listas para guardar:**", files=files_to_send)

    active_sessions.pop(user_id, None)


# ==================== GESTIÓN DE RADARES EN VIVO (/radares) ====================

class RadarEditModal(discord.ui.Modal):
    def __init__(self, target_idx, target_data):
        super().__init__(title=f"Editar: {target_data['name'][:32]}")
        self.target_idx = target_idx

        self.min_p = discord.ui.TextInput(
            label="Precio Mínimo (€)",
            default=str(target_data.get("min_price", 20)),
            required=True,
            max_length=10
        )
        self.max_p = discord.ui.TextInput(
            label="Precio Máximo (€)",
            default=str(target_data.get("max_price", 100)),
            required=True,
            max_length=10
        )
        self.tier_in = discord.ui.TextInput(
            label="Prioridad (hot = cada ronda | standard)",
            default=str(target_data.get("tier", "hot")),
            required=True,
            max_length=15
        )
        self.add_blacklist = discord.ui.TextInput(
            label="Añadir palabras a Blacklist (sep. por coma)",
            placeholder="Ej: pantalla rota, sin caja, bloqueado",
            required=False,
            max_length=200
        )
        self.add_item(self.min_p)
        self.add_item(self.max_p)
        self.add_item(self.tier_in)
        self.add_item(self.add_blacklist)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg = load_config()
            t = cfg["targets"][self.target_idx]
            t["min_price"] = int(float(self.min_p.value.strip()))
            t["max_price"] = int(float(self.max_p.value.strip()))
            tier_val = self.tier_in.value.strip().lower()
            t["tier"] = "hot" if "hot" in tier_val else "standard"

            extra_bl = [w.strip().lower() for w in self.add_blacklist.value.split(",") if w.strip()]
            if extra_bl:
                current_bl = t.get("blacklist", [])
                for w in extra_bl:
                    if w not in current_bl:
                        current_bl.append(w)
                t["blacklist"] = current_bl

            save_config(cfg)
            await interaction.response.send_message(
                f"✅ **Radar actualizado en vivo en `config.json`:**\n"
                f"• **Radar:** {t['name']}\n"
                f"• **Nuevo rango:** `{t['min_price']}€ - {t['max_price']}€` | **Tier:** `{t['tier'].upper()}`\n"
                f"• Los snipers aplicarán el cambio automáticamente en su siguiente ronda.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error guardando configuración: {e}", ephemeral=True)


class RadarActionView(discord.ui.View):
    def __init__(self, target_idx):
        super().__init__(timeout=180)
        self.target_idx = target_idx

    @discord.ui.button(label="Activar / Pausar Radar", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def toggle_radar(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_config()
        t = cfg["targets"][self.target_idx]
        t["enabled"] = not t.get("enabled", True)
        save_config(cfg)
        state_str = "🟢 **ACTIVADO**" if t["enabled"] else "🔴 **PAUSADO**"
        await interaction.response.send_message(f"{state_str} el radar **{t['name']}** en `config.json`.", ephemeral=True)

    @discord.ui.button(label="Editar Precios / Prioridad / Blacklist", style=discord.ButtonStyle.success, emoji="✏️")
    async def edit_radar(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_config()
        t = cfg["targets"][self.target_idx]
        await interaction.response.send_modal(RadarEditModal(self.target_idx, t))


class RadarSelectView(discord.ui.View):
    def __init__(self, targets):
        super().__init__(timeout=240)
        options = []
        for idx, t in enumerate(targets[:25]):
            enabled = t.get("enabled", True)
            tier = t.get("tier", "hot").upper()
            mode = "SERVIDOR" if t.get("mode") == "personal" else "REVENTA"
            plats = ",".join([p[:4].upper() for p in t.get("platforms", ["walla", "vint"])])
            options.append(discord.SelectOption(
                label=f"{'🟢' if enabled else '🔴'} {t['name'][:85]}",
                value=str(idx),
                description=f"{t['min_price']}-{t['max_price']}€ • {tier} • {mode} • [{plats}]"[:95]
            ))
        self.sel = discord.ui.Select(placeholder="Selecciona un radar para configurar o pausar...", options=options)
        self.sel.callback = self.on_select
        self.add_item(self.sel)

    async def on_select(self, interaction: discord.Interaction):
        idx = int(self.sel.values[0])
        cfg = load_config()
        t = cfg["targets"][idx]
        enabled = t.get("enabled", True)
        embed = discord.Embed(
            title=f"🎯 Configuración: {t['name']}",
            color=0x2ECC71 if enabled else 0xE74C3C,
            description=(
                f"• **Estado:** {'🟢 Activo' if enabled else '🔴 Pausado'}\n"
                f"• **Modo:** `{t.get('mode', 'resale').upper()}` | **Prioridad:** `{t.get('tier', 'hot').upper()}`\n"
                f"• **Rango de Compra:** **`{t['min_price']}€ — {t['max_price']}€`**\n"
                f"• **Mercados:** `{', '.join(t.get('platforms', []))}`\n"
                f"• **Ref. CeX / Mercado:** {t.get('cex_cash_ref', 'N/A')}\n"
                f"• **Queries Wallapop:** `{', '.join(t.get('wallapop_keywords', [t.get('keywords', '')]))}`"
            )
        )
        await interaction.response.send_message(embed=embed, view=RadarActionView(idx), ephemeral=True)


# ==================== GESTIÓN DE CARTERA / PORTFOLIO (/cartera) ====================

class MarkSoldModal(discord.ui.Modal, title="Registrar Venta Cerrada"):
    sold_price_in = discord.ui.TextInput(
        label="Precio final de venta cobrado (€)",
        placeholder="Ej: 195",
        required=True,
        max_length=12
    )

    def __init__(self, deal_id, title, bought_price):
        super().__init__()
        self.deal_id = deal_id
        self.item_title = title
        self.bought_price = float(bought_price or 0)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sold_p = float(self.sold_price_in.value.replace(",", ".").strip())
            conn = init_db()
            c = conn.cursor()
            c.execute("UPDATE deals SET status = 'sold', sold_price = ? WHERE deal_id = ?", (sold_p, self.deal_id))
            conn.commit()
            conn.close()
            real_profit = sold_p - self.bought_price
            roi = (real_profit / self.bought_price * 100) if self.bought_price > 0 else 0
            await interaction.response.send_message(
                f"🎉 **¡Venta registrada con éxito!**\n"
                f"• **Producto:** `{self.item_title}`\n"
                f"• **Coste Total:** `{self.bought_price:.1f}€` ➔ **Vendido por:** `{sold_p:.1f}€`\n"
                f"• **Beneficio Neto Real:** **`+{real_profit:.1f}€`** *(ROI: `{roi:.1f}%`)*",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error registrando venta: {e}", ephemeral=True)


class PortfolioView(discord.ui.View):
    def __init__(self, active_items):
        super().__init__(timeout=180)
        self.active_items = active_items
        if active_items:
            options = []
            for d in active_items[:25]:
                options.append(discord.SelectOption(
                    label=f"{d['model'][:75]} ({d['bought_price']:.0f}€)",
                    value=d["deal_id"],
                    description=f"Est. venta: {d['optimal_price']:.0f}€ (+{d['optimal_price'] - d['bought_price']:.0f}€) • {d['platform']}",
                    emoji="💰"
                ))
            self.sel = discord.ui.Select(placeholder="Selecciona un artículo en stock para marcar como VENDIDO...", options=options)
            self.sel.callback = self.on_select_sold
            self.add_item(self.sel)

    async def on_select_sold(self, interaction: discord.Interaction):
        deal_id = self.sel.values[0]
        item = next((x for x in self.active_items if x["deal_id"] == deal_id), None)
        if not item:
            await interaction.response.send_message("No encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(MarkSoldModal(deal_id, item["model"], item["bought_price"]))


def _generate_kit_photos_sync(kit_folder, plat, item_id, title, item_url, ai_data_kit, optimal_price, landed_cost, img_urls):
    enhanced_photos = []
    try:
        raw_photos = download_item_images(
            img_urls, rotator=None, max_images=3, force_safari=(str(plat).upper() in ("EBAY", "TODOCOLECCION"))
        )
        for p_bytes in raw_photos:
            try:
                enhanced_photos.append(enhance_and_clean_image(p_bytes))
            except Exception:
                enhanced_photos.append(p_bytes)
        save_resale_kit(
            kit_folder, plat, item_id, title, item_url,
            ai_data_kit, optimal_price, landed_cost, enhanced_photos, image_urls=img_urls
        )
    except Exception as e:
        print(f"[WARN] Error generando fotos del kit al comprar ({kit_folder}): {e}")


# ==================== INTERACCIONES PERSISTENTES EN ALERTAS (#avisos_chollos) ====================

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id", "")
    if not custom_id.startswith("deal:"):
        return

    parts = custom_id.split(":", 2)
    if len(parts) < 3:
        return
    action, deal_id = parts[1], parts[2]

    conn = init_db()
    c = conn.cursor()

    # 4. BOTÓN: 🚫 Descartar (también funciona para avisos Flash o antiguos)
    if action == "hide":
        try:
            c.execute("UPDATE deals SET status = 'discarded' WHERE deal_id = ?", (deal_id,))
            conn.commit()
        except Exception:
            pass
        conn.close()
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.send_message("🚫 Chollo descartado.", ephemeral=True)
        return

    c.execute(
        """SELECT deal_id, platform, item_id, title, price, landed_cost, optimal_price, net_profit,
                  identified_model, cex_search_query, item_url, kit_folder, fast_msg, haggle_msg,
                  seo_title, seo_description, value_add_tip, mode, radar_id,
                  image_url, COALESCE(image_urls_json, '[]'), COALESCE(visual_notes, '')
           FROM deals WHERE deal_id = ?""",
        (deal_id,)
    )
    row = c.fetchone()

    if not row:
        conn.close()
        await interaction.response.send_message("⚠️ Este anuncio es anterior a la base de datos v4.0 o ya no existe.", ephemeral=True)
        return

    (
        _, plat, item_id, title, price, landed_cost, optimal_price, net_profit,
        model_name, cex_q, item_url, kit_folder, fast_msg, haggle_msg,
        seo_title, seo_desc, tip, mode, radar_id,
        image_url, image_urls_json, visual_notes
    ) = row

    # 1. BOTÓN: 📋 Mensajes / Regateo 1-Clic
    if action == "msg":
        conn.close()
        f_msg = fast_msg or f"Hola! Me interesa el {model_name}, funciona todo bien? Si está disponible te lo compro ahora mismo por la aplicación."
        h_price = max(10, int(price * 0.84))
        h_msg = haggle_msg or f"Hola! Si te cuadran {h_price}€ te lo compro ahora mismo con pago inmediato por la aplicación, sin mareos. Gracias!"
        await interaction.response.send_message(
            f"📱 **Toca el bloque que prefieras para copiarlo con 1 toque:**\n\n"
            f"⚡ **Opción 1: Compra Directa / Asegurar Chollo ({price:.0f}€)**\n"
            f"```text\n{f_msg}\n```\n"
            f"🤝 **Opción 2: Regateo Rápido Pago Inmediato (~{h_price}€)**\n"
            f"```text\n{h_msg}\n```",
            ephemeral=True
        )
        return

    # 2. BOTÓN: 📊 Grados CeX en Vivo
    elif action == "cex":
        await interaction.response.defer(ephemeral=True)
        q_use = cex_q or model_name or title[:35]
        variants = fetch_cex_live(
            conn, q_use, reference_price=price, return_all_variants=True,
            target={"id": radar_id or ""}, title_context=title
        )
        conn.close()
        if not variants:
            await interaction.followup.send(f"🔍 No se encontraron variantes activas en CeX España para `{q_use}`.", ephemeral=True)
            return

        lines = []
        for v in variants[:6]:
            diff_cash = v["cash"] - landed_cost
            sign = "+" if diff_cash >= 0 else ""
            stock_icon = "🟢" if v.get("in_stock_online") else "⚪"
            lines.append(
                f"{stock_icon} **{v['box_name']}**\n"
                f"   💵 Efectivo: **`{v['cash']:.0f}€`** (`{sign}{diff_cash:.0f}€` neto) | 🎟️ Vale: `{v['voucher']:.0f}€` | 🏷️ PVP: `{v['sell']:.0f}€`"
            )

        embed = discord.Embed(
            title=f"🏪 Desglose CeX España en Vivo: {q_use}",
            description=f"💳 **Tu coste puesto en casa:** **`{landed_cost:.0f}€`**\n\n" + "\n\n".join(lines),
            color=0xE74C3C
        )
        embed.set_footer(text="Datos en tiempo real vía Algolia CeX España (🟢 =Con stock online)")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # 3. BOTÓN: ✅ ¡Comprado! -> Cartera + Generar Kit Bajo Demanda + Lanzar Reventa
    elif action == "buy":
        ai_data_kit = {
            "identified_model": model_name or title,
            "value_add_tip": tip or "Venta rápida en 48h",
            "visual_notes": visual_notes or ""
        }
        if not kit_folder:
            kit_folder = build_kit_folder_name(plat, item_id, ai_data_kit)

        c.execute(
            "UPDATE deals SET status = 'bought', bought_price = ?, kit_folder = ? WHERE deal_id = ?",
            (landed_cost, kit_folder, deal_id)
        )
        conn.commit()
        conn.close()

        try:
            img_urls = json.loads(image_urls_json or "[]")
            if not isinstance(img_urls, list):
                img_urls = []
        except Exception:
            img_urls = []
        if not img_urls and image_url:
            img_urls = [image_url]

        full_kit_path = os.path.join(KITS_DIR, kit_folder)
        need_photo_download = False

        if mode != "personal":
            existing_clean = glob.glob(os.path.join(full_kit_path, "foto_*_limpia.jpg")) if os.path.exists(full_kit_path) else []
            if not os.path.exists(full_kit_path):
                save_resale_kit(
                    kit_folder, plat, item_id, title, item_url,
                    ai_data_kit, optimal_price, landed_cost, [], image_urls=img_urls
                )
            if not existing_clean and img_urls:
                need_photo_download = True

        kit_info = get_kit_info_by_folder(full_kit_path)
        if not kit_info:
            kit_info = {
                "folder": full_kit_path,
                "bname": kit_folder or deal_id,
                "title": seo_title or model_name or title,
                "est_price": f"{optimal_price:.0f}€",
                "tip_text": tip or "",
                "base_content": f"--- TÍTULO SEO ---\n{seo_title}\n\n--- DESCRIPCIÓN SEO ---\n{seo_desc}",
                "clean_photos_count": 0
            }

        if mode == "personal":
            await interaction.response.send_message(
                f"🎉 **¡Compra Personal Registrada para tu Servidor!**\n"
                f"• **Disco / Hardware:** `{model_name}`\n"
                f"• **Coste Puesto en Casa:** **`{landed_cost:.0f}€`** *(Ahorro estimado: **`+{net_profit:.0f}€`**)*\n"
                f"• Guardado en `/cartera`.",
                ephemeral=False
            )
        else:
            await interaction.response.send_modal(ExtrasModal(kit_info))
            if need_photo_download:
                await asyncio.to_thread(
                    _generate_kit_photos_sync,
                    kit_folder, plat, item_id, title, item_url,
                    ai_data_kit, optimal_price, landed_cost, img_urls
                )
        return


# ==================== EVENTOS Y SLASH COMMANDS ====================

@bot.event
async def on_ready():
    init_db()
    print(f"[BOT ONLINE] Conectado como {bot.user} (ID: {bot.user.id})")
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"[SLASH COMMANDS] Sincronizados con éxito en servidor {GUILD_ID}")
    except Exception as e:
        print(f"[ERROR SYNC] {e}")


@bot.tree.command(name="kits", description="Ver compras recientes y generar anuncios de reventa en 1 clic")
async def slash_kits(interaction: discord.Interaction):
    kits = get_recent_kits(50)
    if not kits:
        await interaction.response.send_message("❌ Aún no hay kits generados en `/app/kits`.", ephemeral=True)
        return
    view = KitSelectView(kits)
    embed = discord.Embed(
        title="📦 Centro de Reventa Automático (Boti Relister v4.0)",
        description="Selecciona cualquier chollo detectado o comprado para generar su anuncio optimizado:",
        color=0x9B59B6
    )
    embed.set_footer(text=f"Total kits listos: {len(kits)} • Trato en mano: {SELLER_CITY}")
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="tasar", description="Tasa cualquier producto en vivo con CeX España + IA de Reventa")
@app_commands.describe(
    producto="Modelo a tasar (ej: 'New Nintendo 3DS XL', 'Samsung 870 EVO 1TB', 'Sony A6000')",
    precio_compra="Precio que te piden por él con envío incluido (opcional)"
)
async def slash_tasar(interaction: discord.Interaction, producto: str, precio_compra: float = 0.0):
    await interaction.response.defer()
    conn = init_db()
    variants = fetch_cex_live(conn, producto, reference_price=precio_compra, return_all_variants=True)
    conn.close()

    ai_val = call_ai_appraiser(producto, precio_compra, variants)

    cex_lines = []
    if variants:
        for v in variants[:5]:
            net_str = f" *(Neto: `+{v['cash'] - precio_compra:.0f}€`)*" if precio_compra > 0 else ""
            cex_lines.append(
                f"• **{v['box_name']}**\n"
                f"  💵 Efectivo: **`{v['cash']:.0f}€`**{net_str} | 🎟️ Vale: `{v['voucher']:.0f}€` | 🏷️ PVP: `{v['sell']:.0f}€`"
            )
    else:
        cex_lines.append("*(No se encontraron fichas exactas en CeX España para este término)*")

    embed = discord.Embed(
        title=f"🔎 Tasación en Vivo: {(ai_val or {}).get('clean_model_name', producto)}",
        color=0x3498DB
    )
    embed.add_field(name="🏪 Precios Oficiales CeX España (Tiempo Real)", value="\n".join(cex_lines)[:1020], inline=False)

    if ai_val:
        fast_48h = float(ai_val.get("fast_resale_48h_eur", 0) or 0)
        patient_p = float(ai_val.get("patient_resale_eur", 0) or 0)
        max_buy = float(ai_val.get("max_recommended_buy_eur", 0) or 0)
        profit_note = ""
        if precio_compra > 0 and fast_48h > 0:
            est_net = fast_48h - precio_compra - 2.0
            sign = "+" if est_net >= 0 else ""
            profit_note = f"\n💰 **Beneficio estimado comprando a {precio_compra:.0f}€:** **`{sign}{est_net:.0f}€` limpios**"

        embed.add_field(
            name="📈 Valor de Reventa en Wallapop / Vinted España",
            value=(
                f"• **Venta Rápida (<48h):** **`{fast_48h:.0f}€`**\n"
                f"• **Venta Paciente (1-2 sem):** `{patient_p:.0f}€`\n"
                f"• **Compra Máxima Recomendada (puesto en casa):** **`<={max_buy:.0f}€`**{profit_note}\n"
                f"• **Veredicto:** {ai_val.get('verdict', '')}"
            )[:1020],
            inline=False
        )
        embed.add_field(
            name="🛡️ Qué revisar antes de pagar",
            value=str(ai_val.get("checklist_before_buying", "Comprobar funcionamiento general."))[:1020],
            inline=False
        )

    cex_url = f"https://es.webuy.com/search?stext={urllib.parse.quote(producto)}"
    embed.add_field(name="🔗 Enlaces Directos", value=f"[Abrir en CeX webuy.com]({cex_url})", inline=False)
    embed.set_footer(text="Boti Tasador v4.0 • Algolia CeX Live + Gemini Flash Auditor")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="radares", description="Panel de control en vivo de config.json (Activar/Pausar y Editar precios)")
async def slash_radares(interaction: discord.Interaction):
    cfg = load_config()
    targets = cfg.get("targets", [])
    lines = []
    for t in targets:
        st = "🟢" if t.get("enabled", True) else "🔴"
        tier_icon = "🔥" if t.get("tier", "hot") == "hot" else "⏱️"
        plats = "/".join([p[:2].upper() for p in t.get("platforms", [])])
        lines.append(f"{st} {tier_icon} **{t['name']}** — `{t['min_price']}€ a {t['max_price']}€` *[{plats}]*")

    embed = discord.Embed(
        title="🎛️ Centro de Control de Radares (`config.json` en Vivo)",
        description=(
            f"💰 **Margen mín. Reventa:** `+{cfg.get('min_net_profit_eur', 35)}€` | "
            f"💾 **Ahorro mín. Servidor:** `+{cfg.get('min_personal_savings_eur', 25)}€` | "
            f"⭐ **Nota mín.:** `{cfg.get('min_deal_score', 8)}/10`\n\n"
            + "\n".join(lines)
        )[:4000],
        color=0x1ABC9C
    )
    embed.set_footer(text="🔥 = Escaneo en cada ronda (Hot Tier) • ⏱️ = Escaneo alterno (Standard Tier)")
    await interaction.response.send_message(embed=embed, view=RadarSelectView(targets))


@bot.tree.command(name="cartera", description="Ver tu stock comprado, registrar ventas y consultar tu beneficio neto real")
async def slash_cartera(interaction: discord.Interaction):
    conn = init_db()
    c = conn.cursor()

    c.execute(
        """SELECT deal_id, platform, identified_model, bought_price, optimal_price, mode, created_at
           FROM deals WHERE status = 'bought' ORDER BY created_at DESC"""
    )
    bought_rows = c.fetchall()

    c.execute(
        """SELECT COUNT(*), COALESCE(SUM(bought_price), 0), COALESCE(SUM(sold_price), 0)
           FROM deals WHERE status = 'sold'"""
    )
    sold_cnt, total_cost_sold, total_rev_sold = c.fetchone()
    conn.close()

    realized_profit = float(total_rev_sold) - float(total_cost_sold)
    roi_pct = (realized_profit / float(total_cost_sold) * 100) if float(total_cost_sold) > 0 else 0.0

    active_items = []
    stock_lines = []
    active_invested = 0.0
    est_potential_profit = 0.0

    for r in bought_rows:
        did, plat, model_n, b_price, opt_price, mode, c_at = r
        b_p = float(b_price or 0)
        o_p = float(opt_price or 0)
        active_invested += b_p
        if mode != "personal":
            est_potential_profit += max(0.0, o_p - b_p)
            active_items.append({
                "deal_id": did,
                "platform": plat,
                "model": model_n,
                "bought_price": b_p,
                "optimal_price": o_p
            })
            stock_lines.append(f"• 📦 **{model_n}** ({plat}) — Coste: `{b_p:.0f}€` ➔ Obj. Venta: **`{o_p:.0f}€`** *(+{o_p - b_p:.0f}€)*")
        else:
            stock_lines.append(f"• 💾 **[SERVIDOR] {model_n}** ({plat}) — Coste: `{b_p:.0f}€` *(Valor: `{o_p:.0f}€`)*")

    embed = discord.Embed(
        title="💼 Cartera de Arbitraje & Inversiones",
        color=0xF1C40F,
        description=(
            f"🏆 **Balances Cerrados (Ventas Completadas: `{sold_cnt}`):**\n"
            f"• **Beneficio Neto Real Cobrado:** **`+{realized_profit:.1f}€`** *(ROI medio: `{roi_pct:.1f}%`)*\n\n"
            f"📦 **Inventario Activo (`{len(bought_rows)}` artículos):**\n"
            f"• **Capital Invertido en Curso:** `{active_invested:.0f}€`\n"
            f"• **Beneficio Latente Estimado:** **`+{est_potential_profit:.0f}€`**\n\n"
            + ("\n".join(stock_lines[:15]) if stock_lines else "*(No tienes artículos marcados como comprados actualmente. Pulsa **✅ ¡Comprado!** en cualquier alerta de `#avisos_chollos` para añadirlo a tu cartera).*")
        )
    )
    embed.set_footer(text="Boti Portfolio Manager v4.0 • Selecciona un artículo abajo cuando lo vendas")
    await interaction.response.send_message(embed=embed, view=PortfolioView(active_items))


@bot.tree.command(name="estado", description="Estado en tiempo real de los 6 motores de escaneo y base de datos")
async def slash_estado(interaction: discord.Interaction):
    conn = init_db()
    c = conn.cursor()
    platforms = ["WALLAPOP", "VINTED", "MILANUNCIOS", "CASHCONVERTERS", "TODOCOLECCION", "EBAY"]
    counts = {}
    for p in platforms:
        c.execute("SELECT COUNT(*) FROM market_seen_items WHERE platform = ?", (p,))
        counts[p] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM cex_cache")
    cex_cnt = c.fetchone()[0]

    c.execute(
        """SELECT platform, identified_model, price, net_profit, deal_score, item_url
           FROM deals ORDER BY created_at DESC LIMIT 5"""
    )
    recent_deals = c.fetchall()
    conn.close()

    kits_cnt = len(get_recent_kits(100))
    cfg = load_config()
    active_radars = sum(1 for t in cfg.get("targets", []) if t.get("enabled", True))

    plat_lines = [
        f"• **Wallapop Engine:** `{counts['WALLAPOP']:,}` anuncios indexados",
        f"• **Vinted Engine:** `{counts['VINTED']:,}` anuncios indexados",
        f"• **Milanuncios:** `{counts['MILANUNCIOS']:,}` anuncios indexados",
        f"• **Cash Converters ES:** `{counts['CASHCONVERTERS']:,}` artículos indexados",
        f"• **TodoColección:** `{counts['TODOCOLECCION']:,}` lotes indexados",
        f"• **eBay España (¡Cómpralo Ya!):** `{counts['EBAY']:,}` anuncios indexados"
    ]

    deal_lines = []
    for plat, mod, pr, prof, sc, url in recent_deals:
        deal_lines.append(f"• **[{plat}]** [{mod}]({url}) — `{pr:.0f}€` (**`+{prof:.0f}€`** | ⭐ `{sc}/10`)")

    embed = discord.Embed(
        title="📡 Telemetría Global: Boti Sniper Suite v4.0",
        color=0x2ECC71,
        description=(
            f"🎯 **Radares Activos:** `{active_radars}/{len(cfg.get('targets', []))}` | "
            f"🏪 **Modelos en Caché CeX:** `{cex_cnt}` | "
            f"📁 **Kits en Disco:** `{kits_cnt}`\n\n"
            f"📊 **Base de Datos Multi-Mercado (`sniper.db` WAL):**\n" + "\n".join(plat_lines)
        )
    )
    if deal_lines:
        embed.add_field(name="🔥 Últimos 5 Chollos Detectados", value="\n".join(deal_lines)[:1020], inline=False)
    embed.set_footer(text=f"Servidor Linux miky-p6657es • {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="guia", description="Guía de 60 segundos para hacer fotos que venden en <24h")
async def slash_guia(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📸 Masterclass: Fotos que Venden en 24h (60 Segundos)",
        color=0xF1C40F,
        description=(
            "**1. El Spot de Oro en tu casa:**\n"
            "• Ponte a 1-2 metros de una ventana lateral (luz natural indirecta).\n"
            "• Jamás a contraluz, sin flash y sobre una mesa limpia de madera o tono neutro.\n\n"
            "**2. Las 4 Fotos Clave que Cierran Ventas:**\n"
            "• **Foto 1 (Portada):** Consola/Cámara encendida en el menú principal + caja o funda detrás.\n"
            "• **Foto 2 (Bodegón ordenado):** Todo el conjunto alineado en paralelo limpio.\n"
            "• **Foto 3 (Confianza total):** Trasera limpia enfocando estado de bisagras/lente/etiqueta.\n"
            "• **Foto 4 (Extras):** Juegos físicos, cargador original o accesorios de cerca.\n\n"
            "✨ *El bot elimina automáticamente los metadatos EXIF/GPS y aplica nitidez orgánica.*"
        )
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ayuda", description="Muestra todos los comandos y automatizaciones de Boti v4.0")
async def slash_ayuda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Centro de Mando Boti Arbitrage & Homelab v4.0",
        color=0x3498DB,
        description=(
            "**Botones Interactivos en `#avisos_chollos`:**\n"
            "• **⚡ Comprar en {Plataforma}:** Te abre el anuncio directamente en la app o web oficial con el botón 'Comprar' listo para pago en 1 toque.\n"
            "• **📋 Mensajes / Regateo 1-Clic:** Genera mensajes de compra directa y regateo (-15%) copiables con 1 toque.\n"
            "• **📊 Grados CeX:** Consulta en tiempo real todos los precios de CeX España (Caja, Sin Caja, Rebajada).\n"
            "• **✅ ¡Comprado! -> Reventa:** Registra la compra en `/cartera` y abre el creador del anuncio de reventa.\n\n"
            "**Slash Commands disponibles:**\n"
            "• `/tasar <producto> [precio]`: Tasación instantánea en CeX España + IA de reventa en 48h.\n"
            "• `/radares`: Activa/pausa nichos y edita precios o blacklist de `config.json` en vivo.\n"
            "• `/cartera`: Controla tu stock comprado, registra ventas cerradas y mide tu ROI real.\n"
            "• `/kits`: Genera anuncios de reventa con redacción natural y limpieza EXIF de fotos.\n"
            "• `/estado`: Estadísticas en vivo de los 6 mercados (Wallapop, Vinted, Milanuncios, CashConverters, TodoColección, eBay).\n"
            "• `/guia`: Masterclass rápida de fotografía para vender al precio más alto."
        )
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    session = active_sessions.get(message.author.id)
    if session and session.get("waiting_photos") and message.attachments:
        valid_attachments = [
            a for a in message.attachments
            if (a.content_type and "image" in a.content_type) or a.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ]
        if valid_attachments:
            for att in valid_attachments:
                try:
                    b = await att.read()
                    session.setdefault("uploaded_photos", []).append(b)
                except Exception as e:
                    print(f"[ERROR DESCARGANDO ADJUNTO]: {e}")

            total_photos = len(session.get("uploaded_photos", []))
            view = PhotoUploadReadyView(message.author.id)
            await message.channel.send(
                f"📸 **Recibida(s) {len(valid_attachments)} foto(s)** de `{message.author.display_name}` (Total: {total_photos}).\n"
                "Pulsa el botón verde **[ ✅ Generar Anuncio con mis Fotos ]** cuando estén todas:",
                view=view
            )
            return

    await bot.process_commands(message)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("[FATAL] Falta DISCORD_BOT_TOKEN en variables de entorno.")
    else:
        bot.run(BOT_TOKEN)
