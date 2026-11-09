# -*- coding: utf-8 -*-
"""
Boti Relister & Command Center v4.3 (El Bot Definitivo de Discord).
Incluye:
- Botones interactivos persistentes en cada alerta de #avisos_chollos (¡Comprado! -> Reventa, Regateo Multilingüe 1-Clic, Grados CeX, Descartar).
- Soporte completo para Oportunidades de Chollo (Hacer Oferta) y enlaces directos 1-Tap.
- /kits    : Centro de Reventa con IA Copywriter (tono Particular Meticuloso, 0 truncamientos JSON) y optimizador EXIF de fotos.
- /tasar   : Tasador en vivo CeX España (Algolia) + Auditoría IA de precio de reventa en 48h y compra máxima recomendada.
- /radares : Panel de control en vivo para activar/pausar nichos y editar precios/filtros de config.json desde Discord.
- /cartera : Contabilidad de arbitraje (Stock comprado, Registrar Venta, Capital invertido, Beneficio Neto Real y ROI %).
- /estado  : Telemetría en tiempo real de los 6 mercados (Wallapop, Vinted, Milanuncios, CashConverters, TodoColección, eBay).
- /guia y /ayuda
"""

import os
import io
import re
import json
import glob
import time
import asyncio
import threading
import urllib.parse
from datetime import datetime
from PIL import Image, ImageOps, ImageEnhance

import discord
from discord import app_commands
from discord.ext import commands, tasks
from curl_cffi import requests as cffi_requests

from logger import setup_logger, log_metric
from core_engine import (
    DB_PATH, CONFIG_PATH, KITS_DIR,
    init_db, get_db, load_config, load_raw_config, save_config as engine_save_config, fetch_cex_live,
    download_item_images, enhance_and_clean_image,
    build_kit_folder_name, save_resale_kit,
    normalize_and_build_links, generate_multilingual_haggle_msg,
    touch_heartbeat, ai_gateway_call, BudgetExceededError,
    DEAL_STATUS_LABELS, get_deal, mark_deal_bought, create_manual_deal, cancel_deal_purchase,
    mark_deal_sold, update_deal_fields, delete_deal, undo_last_deal_change, get_portfolio_summary,
    search_deals, count_stale_alerts, purge_stale_alerts, export_deals_csv
)

# ==================== CONFIGURACIÓN Y VARIABLES ====================
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if GEMINI_API_KEY and len(GEMINI_API_KEY) < 8:
    print("[WARN] GEMINI_API_KEY sospechosamente corta en bot.py. Verifique el archivo .env.")
AI_MODEL = os.environ.get("AI_MODEL", "gemini-3.5-flash-lite").strip()
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1564559739960959036"))
RELISTER_CHANNEL_ID = int(os.environ.get("RELISTER_CHANNEL_ID", "1565139139949764708"))
SELLER_CITY = os.environ.get("SELLER_CITY", "Sevilla").strip()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
logger = setup_logger("RELISTER-BOT")

active_sessions = {}


def save_config(cfg):
    engine_save_config(cfg, CONFIG_PATH)


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
    photo_urls = []
    clean_photos = sorted(glob.glob(os.path.join(folder_path, "foto_*_limpia.jpg")))

    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
                preview_title = meta.get("seo_title") or meta.get("identified_model") or preview_title
                if meta.get("optimal_price"):
                    est_price = f"{float(meta['optimal_price']):.0f}€"
                tip_text = meta.get("value_add_tip", "")
                photo_urls = meta.get("image_urls") or []
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
        "clean_photos_count": len(clean_photos),
        "photo_urls": photo_urls
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


# ==================== IA COPYWRITER Y TASADOR ====================

def call_ai_copywriter(product_name, base_text, extras_text, condition_notes, desired_price="", seller_city=SELLER_CITY):
    p_name = str(product_name or "").strip()[:80]
    b_text = str(base_text or "").strip()[:300]
    e_text = str(extras_text or "").strip()[:180]
    c_notes = str(condition_notes or "").strip()[:180]
    d_price = str(desired_price or "").strip()[:40]
    s_city = str(seller_city or "Sevilla").strip()[:30]

    prompt = f"""
    Eres un particular meticuloso, ordenado y transparente vendiendo un artículo de segunda mano en Wallapop o Vinted en España.
    Tu objetivo es redactar un anuncio 100% NATURAL, creíble y profesional para particulares. Debe justificar el precio más alto del mercado inspirando total confianza, sin sonar JAMÁS como un robot, una tienda o un redactor publicitario.

    DATOS DEL PRODUCTO:
    - Producto / Modelo: {p_name}
    - Información base del kit / sniper:
    {b_text}
    - EXTRAS Y ACCESORIOS QUE INCLUYES:
    {e_text if e_text else "Sin extras adicionales especificados."}
    - ESTADO REAL Y DETALLES ESPECÍFICOS:
    {c_notes if c_notes else "En muy buen estado, totalmente probado y funcional."}
    - PRECIO / OBJETIVO DE VENTA:
    {d_price if d_price else "Optimizar para máxima rentabilidad rápida y justificada."}
    - CIUDAD PARA TRATO EN MANO: {s_city}

    REGLAS DE ORO DE REDACCIÓN NATURAL (OBLIGATORIAS):
    1. TONO: Particular cuidadoso y honesto que cuida al máximo sus cosas. Cero lenguaje de marketing ni palabras grandilocuentes.
    2. PROHIBIDO USAR FRASES DE ESLOGAN: La primera línea debe empezar directamente con:
       "Vendo [Producto exacto] en un estado [excelente / impoluto / muy bueno], ideal para [uso o público]. Funciona a la perfección, muy cuidado/a."
    3. MÁXIMO 1 SIGNO DE EXCLAMACIÓN EN TODO EL TEXTO: La única exclamación permitida va en la frase final: "¡Echa un vistazo a mis valoraciones para comprar con total tranquilidad!".
    4. UBICACIÓN REAL (SIN PLACEHOLDERS):
       "Hago trato en mano en {s_city} (puedes probarlo/a tranquilamente antes de llevártelo/a)."
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
       • Hago trato en mano en {s_city} (puedes probarlo/a tranquilamente antes de llevártelo/a).
       • Hago envíos por la plataforma. Empaqueto muy bien con plástico de burbujas y caja rígida, y suelo depositarlo en Correos/InPost en menos de 24h.

       Cualquier duda o si necesitas alguna foto o vídeo extra encendido/a, escríbeme sin compromiso. ¡Echa un vistazo a mis valoraciones para comprar con total tranquilidad!

    6. TÍTULO SEO (MÁXIMO 48 CARACTERES estrictos): Nombre del modelo exacto y extras principales.

    IMPORTANTE: Cero texto conversacional ni saludos. Devuelve ÚNICAMENTE un objeto JSON válido con estas claves exactas:
    {{
      "seo_title": "Título máx 48 caracteres",
      "description": "Texto completo estructurado según las reglas anteriores",
      "listing_price": 220,
      "minimum_price": 195,
      "pricing_rationale": "Breve justificación de precio para el usuario"
    }}
    """

    conn = None
    try:
        conn = get_db()
        result = ai_gateway_call(
            prompt=prompt,
            candidate_models=["gemini-3.5-flash-lite", "gemini-flash-lite-latest"],
            openrouter_models=["google/gemini-2.5-flash-lite", "openai/gpt-4o-mini"],
            max_tokens=350,
            temperature=0.2,
            conn=conn,
            platform="DISCORD_BOT",
            radar_id="relister",
            call_type="bot_description",
            timeout=15,
            enforce_budget=True,
            schema_defaults={
                "seo_title": "",
                "description": "",
                "listing_price": 0,
                "minimum_price": 0,
                "pricing_rationale": ""
            }
        )
        if result and isinstance(result, dict):
            desc = result.get("description", "")
            desc = re.sub(r"\[(?:Tu )?Ciudad(?:/Zona)?\]", seller_city, desc, flags=re.IGNORECASE)
            result["description"] = desc.strip()
            return result
    except BudgetExceededError as e:
        print(f"[BOT RELISTER] Cortafuegos IA diario activo: {e}")
        return None
    except Exception as e:
        print(f"[BOT RELISTER] Error en llamada IA: {e}")
    finally:
        if conn:
            conn.close()
    return None


def call_ai_appraiser(product_query, buy_price, cex_variants):
    p_query = str(product_query or "").strip()[:80]
    cex_summary = "\n".join(
        [f"- {v.get('box_name', '')[:40]}: Efectivo {float(v.get('cash', 0)):.0f}€ | PVP {float(v.get('sell', 0)):.0f}€" for v in (cex_variants or [])[:3]]
    ) or "Sin coincidencias exactas en CeX."

    prompt = f"""
    Tasador de arbitraje segunda mano España.
    Consulta: "{p_query}" (Compra planteada: {buy_price if buy_price > 0 else 'No indicado'}€).
    CeX España:
    {cex_summary}

    IMPORTANTE: Cero texto conversacional. Devuelve ÚNICAMENTE un JSON válido:
    {{
      "clean_model_name": "Nombre exacto del producto",
      "fast_resale_48h_eur": int,
      "patient_resale_eur": int,
      "max_recommended_buy_eur": int,
      "verdict": "Veredicto directo 1-2 frases",
      "checklist_before_buying": "3 puntos clave a revisar antes de pagar"
    }}
    """
    conn = None
    try:
        conn = get_db()
        result = ai_gateway_call(
            prompt=prompt,
            candidate_models=["gemini-3.5-flash-lite", "gemini-flash-lite-latest"],
            openrouter_models=["google/gemini-2.5-flash-lite", "openai/gpt-4o-mini"],
            max_tokens=300,
            temperature=0.1,
            conn=conn,
            platform="DISCORD_BOT",
            radar_id="appraiser",
            call_type="bot_appraiser",
            timeout=15,
            enforce_budget=True,
            schema_defaults={
                "clean_model_name": "",
                "fast_resale_48h_eur": 0,
                "patient_resale_eur": 0,
                "max_recommended_buy_eur": 0,
                "verdict": "",
                "checklist_before_buying": ""
            }
        )
        if result and isinstance(result, dict):
            return result
    except BudgetExceededError as e:
        print(f"[BOT APPRAISER] Cortafuegos IA diario activo: {e}")
        return None
    except Exception as e:
        print(f"[BOT APPRAISER] Error en llamada IA: {e}")
    finally:
        if conn:
            conn.close()
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
        embed.set_footer(text=f"Boti Relister v4.3 • Trato en mano en {SELLER_CITY}")
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


def _prepare_photos_sync(use_original_photos: bool, kit_dir: str, uploaded_photos: list) -> list:
    """Procesado intensivo de fotos con Pillow y lectura/escritura en disco fuera del event loop."""
    output_photos = []
    if use_original_photos:
        if kit_dir and os.path.exists(kit_dir):
            existing = sorted(glob.glob(os.path.join(kit_dir, "foto_*_limpia.jpg")))
            for p in existing[:6]:
                try:
                    with open(p, "rb") as f:
                        output_photos.append(f.read())
                except Exception:
                    pass
    else:
        for raw_bytes in (uploaded_photos or []):
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
                    raw_imgs = download_item_images(urls, None, 3, plat_m in ("EBAY", "TODOCOLECCION"))
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
    return output_photos


def _save_published_listing_sync(kit_dir: str, seo_title: str, p_listing, p_min, description: str, output_photos: list):
    """Escritura en disco del kit publicado fuera del event loop."""
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


async def finalize_listing(interaction_or_channel, user_id, use_original_photos=False):
    session = active_sessions.get(user_id)
    if not session:
        return

    kit_dir = session["kit_folder"]
    channel = interaction_or_channel.channel if isinstance(interaction_or_channel, discord.Interaction) else interaction_or_channel
    status_notify = await channel.send("⏳ **Redactando anuncio natural de alta conversión y preparando fotos...**")

    output_photos = await asyncio.to_thread(
        _prepare_photos_sync,
        use_original_photos,
        kit_dir,
        session.get("uploaded_photos", [])
    )

    ai_result = await asyncio.to_thread(
        call_ai_copywriter,
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

    await asyncio.to_thread(
        _save_published_listing_sync,
        kit_dir, seo_title, p_listing, p_min, description, output_photos
    )

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
    embed.set_footer(text="Boti Relister v4.3 • Redacción Natural Particular Meticuloso")

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
        base_max = target_data.get("direct_buy_max_price") or target_data.get("max_price", 100)

        self.min_p = discord.ui.TextInput(
            label="Precio Mínimo (€)",
            default=str(target_data.get("min_price", 20)),
            required=True,
            max_length=10
        )
        self.max_p = discord.ui.TextInput(
            label="Precio Máximo Compra Directa (€)",
            default=str(base_max),
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
            cfg = load_config(expand_for_offers=False)
            t = cfg["targets"][self.target_idx]
            new_min = int(float(self.min_p.value.strip()))
            new_max = int(float(self.max_p.value.strip()))
            t["min_price"] = new_min
            t["max_price"] = new_max
            t["direct_buy_max_price"] = new_max
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
            offer_max = int(round(new_max * 1.30))
            await interaction.response.send_message(
                f"✅ **Radar actualizado en vivo en `config.json`:**\n"
                f"• **Radar:** {t['name']}\n"
                f"• **Compra Directa:** `{new_min}€ - {new_max}€` *(Escaneo Ofertas +30%: hasta `{offer_max}€`)*\n"
                f"• **Prioridad:** `{t['tier'].upper()}`",
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
        cfg = load_config(expand_for_offers=False)
        t = cfg["targets"][self.target_idx]
        t["enabled"] = not t.get("enabled", True)
        save_config(cfg)
        state_str = "🟢 **ACTIVADO**" if t["enabled"] else "🔴 **PAUSADO**"
        await interaction.response.send_message(f"{state_str} el radar **{t['name']}** en `config.json`.", ephemeral=True)

    @discord.ui.button(label="Editar Precios / Prioridad / Blacklist", style=discord.ButtonStyle.success, emoji="✏️")
    async def edit_radar(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_config(expand_for_offers=False)
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
            base_max = t.get("direct_buy_max_price") or t.get("max_price", 100)
            plats = ",".join([p[:4].upper() for p in t.get("platforms", ["walla", "vint"])])
            options.append(discord.SelectOption(
                label=f"{'🟢' if enabled else '🔴'} {t['name'][:85]}",
                value=str(idx),
                description=f"{t['min_price']}-{base_max}€ (Ofertas: {int(base_max*1.3)}€) • {tier} • {mode} [{plats}]"[:95]
            ))
        self.sel = discord.ui.Select(placeholder="Selecciona un radar para configurar o pausar...", options=options)
        self.sel.callback = self.on_select
        self.add_item(self.sel)

    async def on_select(self, interaction: discord.Interaction):
        idx = int(self.sel.values[0])
        cfg = load_config()
        t = cfg["targets"][idx]
        enabled = t.get("enabled", True)
        base_max = t.get("direct_buy_max_price") or t.get("max_price", 100)
        embed = discord.Embed(
            title=f"🎯 Configuración: {t['name']}",
            color=0x2ECC71 if enabled else 0xE74C3C,
            description=(
                f"• **Estado:** {'🟢 Activo' if enabled else '🔴 Pausado'}\n"
                f"• **Modo:** `{t.get('mode', 'resale').upper()}` | **Prioridad:** `{t.get('tier', 'hot').upper()}`\n"
                f"• **Rango Compra Directa:** **`{t['min_price']}€ — {base_max}€`**\n"
                f"• **Techo Escaneo Ofertas (+30%):** **`hasta {t['max_price']}€`**\n"
                f"• **Mercados:** `{', '.join(t.get('platforms', []))}`\n"
                f"• **Ref. CeX / Mercado:** {t.get('cex_cash_ref', 'N/A')}"
            )
        )
        await interaction.response.send_message(embed=embed, view=RadarActionView(idx), ephemeral=True)


# ==================== CARTERA: FICHAS, BOTONES Y MODALES ====================
# Toda la lógica de datos vive en core_engine (mark_deal_bought, cancel_deal_purchase, ...).
# Aquí solo hay presentación. Los botones de una ficha usan custom_id "deal:<acción>:<deal_id>"
# y los resuelve on_interaction, así que siguen funcionando después de reiniciar el bot.

def _run_db(fn, *args, **kwargs):
    conn = get_db()
    try:
        return fn(conn, *args, **kwargs)
    finally:
        conn.close()


async def db_call(fn, *args, **kwargs):
    """Ejecuta una función de BD (conn, ...) en un hilo para no bloquear el bucle de Discord."""
    return await asyncio.to_thread(_run_db, fn, *args, **kwargs)


def _parse_eur(raw, field="importe", allow_empty=False):
    txt = (raw or "").replace("€", "").replace(",", ".").strip()
    if not txt:
        if allow_empty:
            return None
        raise ValueError(f"Falta el {field}.")
    try:
        val = float(txt)
    except ValueError:
        raise ValueError(f"El {field} `{raw}` no es un número válido.")
    if val < 0:
        raise ValueError(f"El {field} no puede ser negativo.")
    return val


def _deal_name(deal):
    return str(deal.get("identified_model") or deal.get("title") or deal.get("deal_id") or "Artículo").strip()


def _fmt_date(val):
    if not val:
        return "—"
    try:
        return datetime.fromisoformat(str(val)[:19]).strftime("%d/%m/%Y")
    except Exception:
        return str(val)[:10]


def _default_paid_price(deal):
    """Precio sugerido al registrar la compra: la oferta recomendada si era una oportunidad, si no el coste puesto en casa."""
    rec = float(deal.get("recommended_offer") or 0)
    fees = float(deal.get("fees") or 0)
    if deal.get("deal_type") == "offer_opportunity" and rec > 0:
        return round(rec + fees, 2)
    return round(
        float(deal.get("bought_price") or 0) or float(deal.get("landed_cost") or 0) or float(deal.get("price") or 0), 2
    )


def build_deal_embed(deal, title=None, color=None):
    status = deal.get("status") or "alerted"
    url = deal.get("item_url") or ""
    bought = float(deal.get("bought_price") or 0)
    target = float(deal.get("optimal_price") or 0)
    lines = [f"**{DEAL_STATUS_LABELS.get(status, status)}** • `{deal.get('platform') or 'MANUAL'}` • ID `{deal['deal_id']}`"]
    if url:
        lines.append(f"🔗 [Abrir anuncio]({url})")
    lines.append("")
    if float(deal.get("price") or 0) > 0:
        lines.append(f"• **Precio del anuncio:** `{float(deal.get('price') or 0):.2f}€`")
    if status in ("bought", "sold", "cancelled") and bought > 0:
        lines.append(f"• **Pagado:** **`{bought:.2f}€`** (el {_fmt_date(deal.get('bought_at'))})")
    if target > 0 and status != "sold":
        base_cost = bought if bought > 0 else float(deal.get("landed_cost") or deal.get("price") or 0)
        lat = target - base_cost
        lines.append(f"• **Objetivo de venta:** `{target:.2f}€` ({'+' if lat >= 0 else ''}{lat:.0f}€ estimado)")
    if status == "sold":
        sold = float(deal.get("sold_price") or 0)
        fees = float(deal.get("sale_fees") or 0)
        net = sold - bought - fees
        roi = (net / bought * 100) if bought > 0 else 0.0
        sign = "+" if net >= 0 else ""
        fee_txt = f" (comisiones `{fees:.2f}€`)" if fees else ""
        lines.append(f"• **Vendido por:** **`{sold:.2f}€`** el {_fmt_date(deal.get('sold_at'))}{fee_txt}")
        lines.append(f"• **Beneficio neto:** **`{sign}{net:.2f}€`** (ROI `{sign}{roi:.1f}%`)")
    if deal.get("notes"):
        lines.append(f"📝 {str(deal['notes'])[:300]}")

    colors = {"bought": 0x2ECC71, "sold": 0xF1C40F, "cancelled": 0x95A5A6, "discarded": 0x7F8C8D, "alerted": 0x3498DB}
    embed = discord.Embed(
        title=(title or f"🧾 {_deal_name(deal)}")[:250],
        description="\n".join(lines)[:4000],
        color=color or colors.get(status, 0x3498DB)
    )
    if deal.get("image_url"):
        try:
            embed.set_thumbnail(url=deal["image_url"])
        except Exception:
            pass
    embed.set_footer(text="Cualquier acción se puede deshacer con ⏪ o /deshacer")
    return embed


class DealActionsView(discord.ui.View):
    """Botones de la ficha según su estado. Sin callbacks: los atiende on_interaction por custom_id."""
    def __init__(self, deal):
        super().__init__(timeout=None)
        did = deal["deal_id"]
        status = deal.get("status")
        buttons = []
        if status == "bought":
            if deal.get("mode") != "personal":
                buttons.append(("Crear anuncio de reventa", "📝", discord.ButtonStyle.success, "relist"))
            buttons += [
                ("Vendido", "💰", discord.ButtonStyle.primary, "sold"),
                ("Editar", "✏️", discord.ButtonStyle.secondary, "edit"),
                ("La compra no se hizo", "↩️", discord.ButtonStyle.secondary, "cancel"),
            ]
        elif status == "sold":
            buttons.append(("Editar venta", "✏️", discord.ButtonStyle.secondary, "edit"))
        else:
            buttons.append(("Registrar compra", "✅", discord.ButtonStyle.success, "buy"))
        buttons += [
            ("Deshacer último cambio", "⏪", discord.ButtonStyle.secondary, "undo"),
            ("Eliminar", "🗑️", discord.ButtonStyle.danger, "delete"),
        ]
        for label, emoji, style, action in buttons:
            self.add_item(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"deal:{action}:{did}"))


class UndoOnlyView(discord.ui.View):
    def __init__(self, deal_id, label="Deshacer"):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label=label, emoji="⏪", style=discord.ButtonStyle.secondary, custom_id=f"deal:undo:{deal_id}"))


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, deal_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Sí, eliminar", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id=f"deal:delete_ok:{deal_id}"))


class BuyModal(discord.ui.Modal, title="Registrar compra"):
    def __init__(self, deal):
        super().__init__()
        self.deal_id = deal["deal_id"]
        self.price_in = discord.ui.TextInput(
            label="Precio final pagado, con envío (€)", default=f"{_default_paid_price(deal):.2f}", max_length=12
        )
        self.notes_in = discord.ui.TextInput(
            label="Notas (opcional)", style=discord.TextStyle.paragraph, required=False, max_length=300,
            placeholder="Ej: quedamos en mano el sábado, incluye funda", default=(deal.get("notes") or "")[:300] or None
        )
        self.add_item(self.price_in)
        self.add_item(self.notes_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = _parse_eur(self.price_in.value, "precio de compra")
            deal = await db_call(mark_deal_bought, self.deal_id, price, self.notes_in.value)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_deal_embed(deal, title=f"🛒 Compra registrada: {_deal_name(deal)}"), view=DealActionsView(deal)
        )
        schedule_kit_preparation(deal)


class SoldModal(discord.ui.Modal, title="Registrar venta"):
    def __init__(self, deal):
        super().__init__()
        self.deal_id = deal["deal_id"]
        default_sold = float(deal.get("sold_price") or 0) or float(deal.get("optimal_price") or 0)
        self.sold_in = discord.ui.TextInput(
            label="Precio final de venta cobrado (€)", default=f"{default_sold:.2f}" if default_sold > 0 else None,
            placeholder="Ej: 195", max_length=12
        )
        self.fees_in = discord.ui.TextInput(
            label="Comisiones y envío que pagaste (€, opcional)", required=False, max_length=12,
            default=f"{float(deal.get('sale_fees') or 0):.2f}" if float(deal.get("sale_fees") or 0) > 0 else None,
            placeholder="Ej: 4.50"
        )
        self.add_item(self.sold_in)
        self.add_item(self.fees_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sold = _parse_eur(self.sold_in.value, "precio de venta")
            fees = _parse_eur(self.fees_in.value, "importe de comisiones", allow_empty=True) or 0.0
            deal = await db_call(mark_deal_sold, self.deal_id, sold, fees)
            summary = await db_call(get_portfolio_summary)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_sale_embed(deal, summary), view=DealActionsView(deal))


class EditDealModal(discord.ui.Modal, title="Editar datos"):
    def __init__(self, deal):
        super().__init__()
        self.deal_id = deal["deal_id"]
        self.is_sold = deal.get("status") == "sold"
        self.bought_in = discord.ui.TextInput(
            label="Precio de compra (€)", required=False, max_length=12,
            default=f"{float(deal.get('bought_price') or 0):.2f}" if float(deal.get("bought_price") or 0) > 0 else None
        )
        self.target_in = discord.ui.TextInput(
            label="Objetivo de venta (€)", required=False, max_length=12,
            default=f"{float(deal.get('optimal_price') or 0):.2f}" if float(deal.get("optimal_price") or 0) > 0 else None
        )
        self.add_item(self.bought_in)
        self.add_item(self.target_in)
        if self.is_sold:
            self.sold_in = discord.ui.TextInput(
                label="Precio de venta (€)", required=False, max_length=12,
                default=f"{float(deal.get('sold_price') or 0):.2f}"
            )
            self.fees_in = discord.ui.TextInput(
                label="Comisiones de venta (€)", required=False, max_length=12,
                default=f"{float(deal.get('sale_fees') or 0):.2f}"
            )
            self.add_item(self.sold_in)
            self.add_item(self.fees_in)
        self.notes_in = discord.ui.TextInput(
            label="Notas", style=discord.TextStyle.paragraph, required=False, max_length=500,
            default=(deal.get("notes") or "")[:500] or None
        )
        self.add_item(self.notes_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            kwargs = {
                "bought_price": _parse_eur(self.bought_in.value, "precio de compra", allow_empty=True),
                "target_price": _parse_eur(self.target_in.value, "objetivo de venta", allow_empty=True),
                "notes": self.notes_in.value.strip(),
            }
            if self.is_sold:
                kwargs["sold_price"] = _parse_eur(self.sold_in.value, "precio de venta", allow_empty=True)
                kwargs["sale_fees"] = _parse_eur(self.fees_in.value, "importe de comisiones", allow_empty=True)
            deal = await db_call(update_deal_fields, self.deal_id, **kwargs)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_deal_embed(deal, title=f"✏️ Datos actualizados: {_deal_name(deal)}"), view=DealActionsView(deal), ephemeral=True
        )


class CancelPurchaseModal(discord.ui.Modal, title="La compra no se hizo"):
    reason_in = discord.ui.TextInput(
        label="Motivo (opcional)", required=False, max_length=200,
        placeholder="Ej: el vendedor no contestó / ya estaba vendido"
    )

    def __init__(self, deal_id):
        super().__init__()
        self.deal_id = deal_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            deal = await db_call(cancel_deal_purchase, self.deal_id, self.reason_in.value)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_deal_embed(deal, title=f"↩️ Compra cancelada: {_deal_name(deal)}"),
            view=DealActionsView(deal)
        )


def build_sale_embed(deal, summary):
    bought = float(deal.get("bought_price") or 0)
    sold = float(deal.get("sold_price") or 0)
    fees = float(deal.get("sale_fees") or 0)
    net = sold - bought - fees
    roi = (net / bought * 100) if bought > 0 else 0.0
    sign = "+" if net >= 0 else ""
    if net > 0:
        title, color = ("🎉🚀 ¡Venta muy rentable!" if roi >= 35 else "🎉 ¡Venta cerrada!"), (0xF1C40F if roi >= 35 else 0x2ECC71)
    elif net == 0:
        title, color = "⚖️ Venta al coste", 0x95A5A6
    else:
        title, color = "📉 Venta con pérdida", 0xE74C3C
    tot = summary["sold"]
    month = summary["month"]
    url = deal.get("item_url") or ""
    name = f"[{_deal_name(deal)}]({url})" if url else f"**{_deal_name(deal)}**"
    fee_line = f"• **Comisiones/envío:** `{fees:.2f}€`\n" if fees else ""
    desc = (
        f"• **Producto:** {name}\n"
        f"• **Compra:** `{bought:.2f}€` ➔ **Venta:** **`{sold:.2f}€`**\n{fee_line}"
        f"• **Beneficio neto:** **`{sign}{net:.2f}€`** (ROI **`{sign}{roi:.1f}%`**)\n\n"
        f"📅 **Este mes:** `{month['count']}` ventas • **`{month['profit']:+.2f}€`**\n"
        f"💼 **Total:** `{tot['count']}` ventas • **`{tot['profit']:+.2f}€`** (ROI medio `{tot['roi']:.1f}%`)\n"
        f"📦 **Stock restante:** `{len(summary['stock'])}` artículos"
    )
    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text="Boti Portfolio • /cartera para ver todo • ⏪ para deshacer")
    return embed


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


def _ensure_kit_sync(deal_id, download_photos=False):
    """Crea (si falta) la carpeta del kit de reventa de un deal y devuelve kit_info para ExtrasModal.
    Con download_photos=True también descarga y limpia las fotos del anuncio (lento: solo en segundo plano)."""
    conn = get_db()
    try:
        deal = get_deal(conn, deal_id)
        if not deal:
            return None
        plat = deal.get("platform") or "MANUAL"
        item_id = deal.get("item_id") or deal_id
        model_name = _deal_name(deal)
        ai_data_kit = {
            "identified_model": model_name,
            "value_add_tip": deal.get("value_add_tip") or "Venta rápida en 48h",
            "visual_notes": deal.get("visual_notes") or ""
        }
        kit_folder = deal.get("kit_folder") or build_kit_folder_name(plat, item_id, ai_data_kit)
        if not deal.get("kit_folder"):
            conn.execute("UPDATE deals SET kit_folder = ? WHERE deal_id = ?", (kit_folder, deal_id))
            conn.commit()
    finally:
        conn.close()

    try:
        img_urls = json.loads(deal.get("image_urls_json") or "[]")
        if not isinstance(img_urls, list):
            img_urls = []
    except Exception:
        img_urls = []
    if not img_urls and deal.get("image_url"):
        img_urls = [deal["image_url"]]

    item_url = deal.get("item_url") or ""
    if plat != "MANUAL" and deal.get("item_id"):
        try:
            item_url, _ = normalize_and_build_links(plat, deal["item_id"], item_url)
        except Exception:
            pass

    optimal = float(deal.get("optimal_price") or 0)
    paid = float(deal.get("bought_price") or 0) or float(deal.get("landed_cost") or 0)
    raw_title = deal.get("title") or model_name
    full_kit_path = os.path.join(KITS_DIR, kit_folder)

    if deal.get("mode") != "personal":
        existing_clean = glob.glob(os.path.join(full_kit_path, "foto_*_limpia.jpg")) if os.path.exists(full_kit_path) else []
        if not os.path.exists(full_kit_path):
            save_resale_kit(kit_folder, plat, item_id, raw_title, item_url, ai_data_kit, optimal, paid, [], image_urls=img_urls)
        if download_photos and not existing_clean and img_urls:
            _generate_kit_photos_sync(kit_folder, plat, item_id, raw_title, item_url, ai_data_kit, optimal, paid, img_urls)

    kit_info = get_kit_info_by_folder(full_kit_path)
    if not kit_info:
        kit_info = {
            "folder": full_kit_path,
            "bname": kit_folder or deal_id,
            "title": deal.get("seo_title") or model_name,
            "est_price": f"{optimal:.0f}€",
            "tip_text": deal.get("value_add_tip") or "",
            "base_content": f"--- TÍTULO SEO ---\n{deal.get('seo_title') or model_name}\n\n--- DESCRIPCIÓN SEO ---\n{deal.get('seo_description') or ''}",
            "clean_photos_count": 0,
            "photo_urls": img_urls
        }
    elif not kit_info.get("photo_urls") and img_urls:
        kit_info["photo_urls"] = img_urls
    if kit_info.get("clean_photos_count", 0) == 0 and kit_info.get("photo_urls"):
        kit_info["clean_photos_count"] = len(kit_info["photo_urls"])
    return kit_info


def schedule_kit_preparation(deal):
    """Tras una compra de reventa, prepara el kit y sus fotos en segundo plano."""
    if not deal or deal.get("mode") == "personal":
        return

    async def _run():
        try:
            await asyncio.to_thread(_ensure_kit_sync, deal["deal_id"], True)
        except Exception as e:
            print(f"[WARN] No se pudo preparar el kit de {deal.get('deal_id')}: {e}")

    asyncio.create_task(_run())


async def _reply_error(interaction: discord.Interaction, text):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ {text}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {text}", ephemeral=True)
    except Exception:
        pass


# ==================== INTERACCIONES PERSISTENTES (alertas y fichas de cartera) ====================

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
    try:
        await _handle_deal_action(interaction, action, deal_id)
    except ValueError as e:
        await _reply_error(interaction, str(e))
    except Exception as e:
        print(f"[BOT ERROR] Acción {action} sobre {deal_id}: {e}")
        await _reply_error(interaction, f"Error inesperado: {e}")


def _discard_alert_sync(conn, deal_id):
    # Solo descarta alertas pendientes: nunca saca de la cartera algo comprado o vendido
    conn.execute(
        "UPDATE deals SET status = 'discarded', status_changed_at = CURRENT_TIMESTAMP WHERE deal_id = ? AND status = 'alerted'",
        (deal_id,)
    )
    conn.commit()


async def _handle_deal_action(interaction: discord.Interaction, action, deal_id):
    if action == "hide":
        await db_call(_discard_alert_sync, deal_id)
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.send_message("🚫 Aviso descartado.", ephemeral=True)
        return

    if action == "purge_ok":
        days = int(deal_id)
        deleted = await db_call(purge_stale_alerts, days)
        await interaction.response.send_message(
            f"🧹 Eliminadas **{deleted}** alertas de hace más de {days} días que nunca compraste. "
            f"Compras, ventas y cancelaciones se conservan."
        )
        return

    if action == "undo":
        undone, deal = await db_call(undo_last_deal_change, deal_id)
        labels = {"buy": "la compra", "sell": "la venta", "cancel_purchase": "la cancelación", "edit": "la edición",
                  "delete": "el borrado", "create_manual": "la compra manual"}
        what = labels.get(undone, undone)
        if not deal:
            await interaction.response.send_message(f"⏪ Deshecho {what}: el registro ya no existe.")
            return
        await interaction.response.send_message(
            embed=build_deal_embed(deal, title=f"⏪ Deshecho {what}: {_deal_name(deal)}"), view=DealActionsView(deal)
        )
        return

    if action == "delete_ok":
        deal = await db_call(delete_deal, deal_id)
        await interaction.response.send_message(
            f"🗑️ Eliminado **{_deal_name(deal)}** (`{deal_id}`). Si fue un error, pulsa deshacer.",
            view=UndoOnlyView(deal_id, label="Deshacer borrado")
        )
        return

    deal = await db_call(get_deal, deal_id)
    if not deal:
        await interaction.response.send_message("⚠️ Este anuncio ya no está en la base de datos.", ephemeral=True)
        return
    status = deal.get("status")

    if action == "view":
        await interaction.response.send_message(embed=build_deal_embed(deal), view=DealActionsView(deal), ephemeral=True)
        return

    if action == "buy":
        if status == "sold":
            raise ValueError("Este artículo ya está vendido. Usa ⏪ si quieres deshacer la venta.")
        await interaction.response.send_modal(BuyModal(deal))
        return

    if action == "sold":
        if status not in ("bought", "sold"):
            raise ValueError("Primero registra la compra con ✅.")
        await interaction.response.send_modal(SoldModal(deal))
        return

    if action == "edit":
        await interaction.response.send_modal(EditDealModal(deal))
        return

    if action == "cancel":
        if status != "bought":
            raise ValueError("Solo se puede cancelar una compra que está en stock.")
        await interaction.response.send_modal(CancelPurchaseModal(deal_id))
        return

    if action == "delete":
        await interaction.response.send_message(
            f"¿Eliminar **{_deal_name(deal)}** ({DEAL_STATUS_LABELS.get(status, status)})? Se guarda una copia y podrás deshacerlo con `/deshacer`.",
            view=ConfirmDeleteView(deal_id), ephemeral=True
        )
        return

    if action == "relist":
        if status not in ("bought", "sold"):
            raise ValueError("Registra primero la compra para crear el anuncio de reventa.")
        kit_info = await asyncio.to_thread(_ensure_kit_sync, deal_id, False)
        if not kit_info:
            raise ValueError("No se pudo preparar el kit de reventa.")
        await interaction.response.send_modal(ExtrasModal(kit_info))
        return

    plat = deal.get("platform") or ""
    price = float(deal.get("price") or 0)
    title = deal.get("title") or ""
    model_name = _deal_name(deal)
    recommended_offer = float(deal.get("recommended_offer") or 0)
    try:
        clean_item_url, _ = normalize_and_build_links(plat, deal.get("item_id"), deal.get("item_url") or "")
    except Exception:
        clean_item_url = deal.get("item_url") or ""

    # 📋 Mensajes / Regateo 1-Clic (ES/FR/IT con la oferta calculada)
    if action == "msg":
        desc_text = deal.get("description") or ""
        offer_val = int(round(recommended_offer)) if recommended_offer > 0 else max(15, int(round(price * 0.82)))
        f_msg = deal.get("fast_msg") or f"¡Hola! Me interesa el {model_name}, ¿funciona todo correctamente? Si está disponible te lo compro ahora mismo por la aplicación."
        msg_es = generate_multilingual_haggle_msg(title, desc_text, "ES", offer_val, model_name)
        msg_fr = generate_multilingual_haggle_msg(title, desc_text, "FR", offer_val, model_name)
        msg_it = generate_multilingual_haggle_msg(title, desc_text, "IT", offer_val, model_name)
        await interaction.response.send_message(
            f"🎯 **Oferta Recomendada para Rentabilidad Segura: `{offer_val}€`** *(Anuncio: `{price:.0f}€`)*\n"
            f"👉 [Abrir Anuncio para Lanzar Oferta]({clean_item_url})\n\n"
            f"🇪🇸 **Oferta en Español ({offer_val}€):**\n```text\n{msg_es}\n```\n"
            f"🇫🇷 **Oferta en Francés (Vinted FR/BE - {offer_val}€):**\n```text\n{msg_fr}\n```\n"
            f"🇮🇹 **Oferta en Italiano (Vinted IT - {offer_val}€):**\n```text\n{msg_it}\n```\n"
            f"⚡ **Compra Directa ({price:.0f}€):**\n```text\n{f_msg}\n```",
            ephemeral=True
        )
        return

    # 📊 Grados CeX en vivo
    if action == "cex":
        await interaction.response.defer(ephemeral=True)
        q_use = deal.get("cex_search_query") or model_name or title[:35]
        variants = await asyncio.to_thread(
            fetch_cex_live,
            None, q_use, reference_price=price, return_all_variants=True,
            target={"id": deal.get("radar_id") or ""}, title_context=title
        )
        if not variants:
            await interaction.followup.send(f"🔍 No se encontraron variantes activas en CeX España para `{q_use}`.", ephemeral=True)
            return
        fees = float(deal.get("fees") or 0)
        effective_landed = (recommended_offer + fees) if (deal.get("deal_type") == "offer_opportunity" and recommended_offer > 0) else float(deal.get("landed_cost") or price)
        lines = []
        for v in variants[:6]:
            diff_cash = v["cash"] - effective_landed
            sign = "+" if diff_cash >= 0 else ""
            stock_icon = "🟢" if v.get("in_stock_online") else "⚪"
            lines.append(
                f"{stock_icon} **{v['box_name']}**\n"
                f"   💵 Efectivo: **`{v['cash']:.0f}€`** (`{sign}{diff_cash:.0f}€` neto) | 🎟️ Vale: `{v['voucher']:.0f}€` | 🏷️ PVP: `{v['sell']:.0f}€`"
            )
        embed = discord.Embed(
            title=f"🏪 Desglose CeX España en Vivo: {q_use}",
            description=f"💳 **Coste de referencia en casa:** **`{effective_landed:.0f}€`**\n\n" + "\n\n".join(lines),
            color=0xE74C3C
        )
        embed.set_footer(text="Datos en tiempo real vía Algolia CeX España (🟢 = Con stock online)")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return


# ==================== EVENTOS Y SLASH COMMANDS ====================

@tasks.loop(seconds=30)
async def bot_heartbeat():
    touch_heartbeat()

@bot.event
async def on_ready():
    init_db()
    if not bot_heartbeat.is_running():
        bot_heartbeat.start()
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
        title="📦 Centro de Reventa Automático (Boti Relister v4.3)",
        description="Selecciona cualquier chollo detectado o comprado para generar su anuncio optimizado:",
        color=0x9B59B6
    )
    embed.set_footer(text=f"Total kits listos: {len(kits)} • Trato en mano: {SELLER_CITY}")
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="tasar", description="Tasa cualquier producto en vivo con CeX España + IA de Reventa")
@app_commands.describe(
    producto="Modelo a tasar (ej: 'New Nintendo 3DS XL', 'WD Red 4TB', 'Sony A6000')",
    precio_compra="Precio que te piden por él con envío incluido (opcional)"
)
async def slash_tasar(interaction: discord.Interaction, producto: str, precio_compra: float = 0.0):
    await interaction.response.defer()
    variants = await asyncio.to_thread(fetch_cex_live, None, producto, reference_price=precio_compra, return_all_variants=True)
    ai_val = await asyncio.to_thread(call_ai_appraiser, producto, precio_compra, variants)

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
    embed.set_footer(text="Boti Tasador v4.3 • Algolia CeX Live + Gemini Flash Auditor")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="radares", description="Panel de control en vivo de config.json (Activar/Pausar y Editar precios)")
async def slash_radares(interaction: discord.Interaction):
    cfg = load_config()
    targets = cfg.get("targets", [])
    lines = []
    for t in targets:
        st = "🟢" if t.get("enabled", True) else "🔴"
        tier_icon = "🔥" if t.get("tier", "hot") == "hot" else "⏱️"
        base_max = t.get("direct_buy_max_price") or t.get("max_price", 100)
        plats = "/".join([p[:2].upper() for p in t.get("platforms", [])])
        lines.append(
            f"{st} {tier_icon} **{t['name']}** — Directo: `{t['min_price']}-{base_max}€` "
            f"*(Ofertas: `{t['max_price']}€`)* *[{plats}]*"
        )

    embed = discord.Embed(
        title="🎛️ Centro de Control de Radares (`config.json` en Vivo)",
        description=(
            f"💰 **Margen mín. Reventa:** `+{cfg.get('min_net_profit_eur', 35)}€` | "
            f"💾 **Ahorro mín. Servidor:** `+{cfg.get('min_personal_savings_eur', 25)}€` | "
            f"🤝 **Modo Ofertas (+30%):** `ACTIVO`\n\n"
            + "\n".join(lines)
        )[:4000],
        color=0x1ABC9C
    )
    embed.set_footer(text="🔥 = Hot Tier • 🤝 = Escanea hasta +30% sobre el precio máximo para cazar Oportunidades de Oferta")
    await interaction.response.send_message(embed=embed, view=RadarSelectView(targets))


def find_deal_in_db(conn, query_str, preferred_status=None, exact_only=False):
    """
    Busca un deal de forma flexible:
    1. Por deal_id exacto (lo que devuelve el autocompletado).
    2. Por item_id exacto o coincidencia en item_url.
    3. Si no es exact_only: por palabras clave en título/modelo, priorizando preferred_status.
    """
    c = conn.cursor()
    q = (query_str or "").strip()
    if not q:
        return None

    def _one(sql, params):
        c.execute(sql, params)
        row = c.fetchone()
        return dict(zip([d[0] for d in c.description], row)) if row else None

    found = _one("SELECT * FROM deals WHERE deal_id = ?", (q,))
    if found:
        return found
    found = _one("SELECT * FROM deals WHERE item_id = ? OR item_url = ? OR (length(?) > 8 AND item_url LIKE ?) ORDER BY created_at DESC LIMIT 1",
                 (q, q, q, f"%{q}%"))
    if found or exact_only:
        return found

    words = [w for w in re.split(r"\s+", q) if len(w) > 1] or [q]
    clauses, params = [], []
    for w in words[:4]:
        clauses.append("(title LIKE ? OR identified_model LIKE ?)")
        params.extend([f"%{w}%", f"%{w}%"])
    where_sql = " AND ".join(clauses)
    if preferred_status:
        found = _one(f"SELECT * FROM deals WHERE status = ? AND ({where_sql}) ORDER BY created_at DESC LIMIT 1",
                     [preferred_status] + params)
        if found:
            return found
    return _one(f"SELECT * FROM deals WHERE {where_sql} ORDER BY created_at DESC LIMIT 1", params)


def _deal_choices_sync(current, statuses=None):
    choices = []
    try:
        rows, _ = _run_db(search_deals, current or "", statuses=statuses, limit=25)
        for r in rows:
            icon = DEAL_STATUS_LABELS.get(r["status"], "•").split(" ", 1)[0]
            shown_price = r["bought_price"] if r["status"] in ("bought", "sold", "cancelled") and r["bought_price"] else r["price"]
            label = f"{icon} {str(r['model'])[:62]} ({float(shown_price or 0):.0f}€ • {r['platform'] or 'MANUAL'})"
            choices.append(app_commands.Choice(name=label[:100], value=str(r["deal_id"])[:100]))
    except Exception as e:
        print(f"[AUTOCOMPLETE ERROR] {e}")
    return choices[:25]


async def _ac_any(interaction: discord.Interaction, current: str):
    return await asyncio.to_thread(_deal_choices_sync, current, None)


async def _ac_buyable(interaction: discord.Interaction, current: str):
    return await asyncio.to_thread(_deal_choices_sync, current, ["alerted", "discarded", "cancelled", "bought"])


async def _ac_stock(interaction: discord.Interaction, current: str):
    return await asyncio.to_thread(_deal_choices_sync, current, ["bought"])


async def _ac_stock_or_sold(interaction: discord.Interaction, current: str):
    return await asyncio.to_thread(_deal_choices_sync, current, ["bought", "sold"])


async def _resolve_deal(interaction: discord.Interaction, anuncio, preferred_status=None, exact_only=False):
    deal = await db_call(find_deal_in_db, anuncio, preferred_status, exact_only)
    if not deal:
        await interaction.response.send_message(
            f"❌ No encuentro ningún anuncio que coincida con `{anuncio}`. Elige uno del autocompletado o mira `/historial`.",
            ephemeral=True
        )
    return deal


@bot.tree.command(name="comprado", description="Registra una compra (de una alerta o manual) con el precio realmente pagado")
@app_commands.describe(
    anuncio="Elige la alerta en el autocompletado, o escribe el nombre de una compra hecha fuera de los radares",
    precio_compra="Precio final pagado con envío (€). Si lo omites, se usa el coste de la alerta",
    notas="Notas opcionales: punto de entrega, extras incluidos...",
    objetivo_venta="Solo para compras manuales: precio al que esperas venderlo (€)"
)
@app_commands.autocomplete(anuncio=_ac_buyable)
async def slash_comprado(interaction: discord.Interaction, anuncio: str, precio_compra: float = None,
                         notas: str = None, objetivo_venta: float = None):
    deal = await db_call(find_deal_in_db, anuncio, None, True)
    try:
        if deal:
            price = float(precio_compra) if precio_compra and precio_compra > 0 else _default_paid_price(deal)
            deal = await db_call(mark_deal_bought, deal["deal_id"], price, notas)
            title = f"🛒 Compra registrada: {_deal_name(deal)}"
        else:
            if not precio_compra or precio_compra <= 0:
                await interaction.response.send_message(
                    f"❌ `{anuncio}` no es ninguna alerta guardada. Para registrar una compra hecha fuera de los radares, "
                    f"indica el precio: `/comprado anuncio:\"{anuncio}\" precio_compra:50`",
                    ephemeral=True
                )
                return
            deal = await db_call(create_manual_deal, anuncio, precio_compra, objetivo_venta, "MANUAL", notas or "")
            title = f"🛒 Compra manual registrada: {_deal_name(deal)}"
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_deal_embed(deal, title=title), view=DealActionsView(deal))
    schedule_kit_preparation(deal)


@bot.tree.command(name="vendido", description="Registra una venta y calcula el beneficio neto real (descontando comisiones)")
@app_commands.describe(
    anuncio="Artículo de tu stock (autocompletado)",
    precio_venta="Precio final cobrado (€)",
    comisiones="Comisiones de la plataforma y envío que pagaste tú (€, opcional)"
)
@app_commands.autocomplete(anuncio=_ac_stock_or_sold)
async def slash_vendido(interaction: discord.Interaction, anuncio: str, precio_venta: float, comisiones: float = 0.0):
    deal = await _resolve_deal(interaction, anuncio, preferred_status="bought")
    if not deal:
        return
    try:
        deal = await db_call(mark_deal_sold, deal["deal_id"], precio_venta, comisiones or 0.0)
        summary = await db_call(get_portfolio_summary)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_sale_embed(deal, summary), view=DealActionsView(deal))


@bot.tree.command(name="cancelar_compra", description="La compra no se llegó a hacer: la saca del stock sin borrar el historial")
@app_commands.describe(anuncio="Artículo en stock (autocompletado)", motivo="Por qué no se hizo (opcional)")
@app_commands.autocomplete(anuncio=_ac_stock)
async def slash_cancelar_compra(interaction: discord.Interaction, anuncio: str, motivo: str = ""):
    deal = await _resolve_deal(interaction, anuncio, preferred_status="bought")
    if not deal:
        return
    try:
        deal = await db_call(cancel_deal_purchase, deal["deal_id"], motivo)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=build_deal_embed(deal, title=f"↩️ Compra cancelada: {_deal_name(deal)}"), view=DealActionsView(deal)
    )


@bot.tree.command(name="editar", description="Corrige el precio de compra, de venta, el objetivo o las notas de un artículo")
@app_commands.describe(
    anuncio="Artículo (autocompletado)",
    precio_compra="Nuevo precio de compra (€)",
    precio_venta="Nuevo precio de venta (€), si ya está vendido",
    comisiones="Comisiones de venta (€)",
    objetivo_venta="Nuevo objetivo de venta (€)",
    notas="Sustituye las notas"
)
@app_commands.autocomplete(anuncio=_ac_any)
async def slash_editar(interaction: discord.Interaction, anuncio: str, precio_compra: float = None, precio_venta: float = None,
                       comisiones: float = None, objetivo_venta: float = None, notas: str = None):
    deal = await _resolve_deal(interaction, anuncio)
    if not deal:
        return
    if all(v is None for v in (precio_compra, precio_venta, comisiones, objetivo_venta, notas)):
        # Sin parámetros: abrir el formulario con los valores actuales
        await interaction.response.send_modal(EditDealModal(deal))
        return
    try:
        deal = await db_call(update_deal_fields, deal["deal_id"], precio_compra, precio_venta, objetivo_venta, notas, comisiones)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=build_deal_embed(deal, title=f"✏️ Datos actualizados: {_deal_name(deal)}"), view=DealActionsView(deal)
    )


@bot.tree.command(name="eliminar", description="Elimina un anuncio o compra de la base de datos (se puede deshacer)")
@app_commands.describe(anuncio="Anuncio o compra (autocompletado)")
@app_commands.autocomplete(anuncio=_ac_any)
async def slash_eliminar(interaction: discord.Interaction, anuncio: str):
    deal = await _resolve_deal(interaction, anuncio, exact_only=True)
    if not deal:
        return
    await interaction.response.send_message(
        f"¿Eliminar **{_deal_name(deal)}** ({DEAL_STATUS_LABELS.get(deal.get('status'), deal.get('status'))})? "
        f"Se guarda una copia y podrás recuperarlo con `/deshacer`.",
        view=ConfirmDeleteView(deal["deal_id"]), ephemeral=True
    )


@bot.tree.command(name="deshacer", description="Deshace el último cambio de cartera (compra, venta, cancelación, edición o borrado)")
@app_commands.describe(anuncio="Opcional: deshacer el último cambio de este artículo concreto")
@app_commands.autocomplete(anuncio=_ac_any)
async def slash_deshacer(interaction: discord.Interaction, anuncio: str = None):
    deal_id = None
    if anuncio:
        # Un borrado ya no está en deals: aceptar también el deal_id tal cual
        found = await db_call(find_deal_in_db, anuncio, None, True)
        deal_id = found["deal_id"] if found else anuncio.strip()
    try:
        await _handle_deal_action(interaction, "undo", deal_id) if deal_id else await _undo_global(interaction)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)


async def _undo_global(interaction: discord.Interaction):
    undone, deal = await db_call(undo_last_deal_change, None)
    if not deal:
        await interaction.response.send_message(f"⏪ Deshecho el último cambio ({undone}). El registro ya no existe.")
        return
    await interaction.response.send_message(
        embed=build_deal_embed(deal, title=f"⏪ Deshecho: {_deal_name(deal)}"), view=DealActionsView(deal)
    )


@bot.tree.command(name="ficha", description="Muestra la ficha de un anuncio o compra con todos sus botones de gestión")
@app_commands.describe(anuncio="Anuncio o compra (autocompletado)")
@app_commands.autocomplete(anuncio=_ac_any)
async def slash_ficha(interaction: discord.Interaction, anuncio: str):
    deal = await _resolve_deal(interaction, anuncio)
    if not deal:
        return
    await interaction.response.send_message(embed=build_deal_embed(deal), view=DealActionsView(deal), ephemeral=True)


HISTORY_PAGE = 10


def _history_embed(rows, total, page, estado, texto):
    pages = max(1, (total + HISTORY_PAGE - 1) // HISTORY_PAGE)
    lines = []
    for r in rows:
        st = DEAL_STATUS_LABELS.get(r["status"], r["status"])
        icon = st.split(" ", 1)[0]
        amount = ""
        if r["status"] == "sold":
            amount = f"`{float(r['bought_price'] or 0):.0f}€` ➔ `{float(r['sold_price'] or 0):.0f}€`"
        elif r["status"] in ("bought", "cancelled") and r["bought_price"]:
            amount = f"pagado `{float(r['bought_price']):.0f}€`"
        else:
            amount = f"anuncio `{float(r['price'] or 0):.0f}€`"
        name = str(r["model"])[:55]
        name = f"[{name}]({r['item_url']})" if r.get("item_url") else name
        lines.append(f"{icon} {name} • {amount} • {_fmt_date(r['updated_at'])}\n   `{r['deal_id']}`")
    filt = []
    if estado:
        filt.append(DEAL_STATUS_LABELS.get(estado, estado))
    if texto:
        filt.append(f"«{texto}»")
    embed = discord.Embed(
        title=f"🗂️ Historial ({total} registros){' • ' + ' • '.join(filt) if filt else ''}",
        description="\n".join(lines)[:4000] if lines else "*No hay registros con esos filtros.*",
        color=0x3498DB
    )
    embed.set_footer(text=f"Página {page + 1}/{pages} • Usa /ficha con el ID para gestionar uno")
    return embed


class HistoryView(discord.ui.View):
    def __init__(self, estado, texto, total, page=0):
        super().__init__(timeout=600)
        self.estado, self.texto, self.total, self.page = estado, texto, total, page
        self._sync_buttons()

    def _sync_buttons(self):
        pages = max(1, (self.total + HISTORY_PAGE - 1) // HISTORY_PAGE)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= pages - 1

    async def _show(self, interaction: discord.Interaction):
        statuses = [self.estado] if self.estado else None
        rows, self.total = await db_call(search_deals, self.texto or "", statuses, HISTORY_PAGE, self.page * HISTORY_PAGE)
        self._sync_buttons()
        await interaction.response.edit_message(embed=_history_embed(rows, self.total, self.page, self.estado, self.texto), view=self)

    @discord.ui.button(label="Anterior", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    @discord.ui.button(label="Siguiente", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self._show(interaction)


@bot.tree.command(name="historial", description="Busca en todas tus alertas, compras, ventas y cancelaciones")
@app_commands.describe(estado="Filtrar por estado", texto="Texto a buscar en título, modelo, ID o URL")
@app_commands.choices(estado=[
    app_commands.Choice(name="En stock", value="bought"),
    app_commands.Choice(name="Vendidos", value="sold"),
    app_commands.Choice(name="Compras canceladas", value="cancelled"),
    app_commands.Choice(name="Alertas pendientes", value="alerted"),
    app_commands.Choice(name="Descartados", value="discarded"),
])
async def slash_historial(interaction: discord.Interaction, estado: app_commands.Choice[str] = None, texto: str = ""):
    est = estado.value if estado else None
    rows, total = await db_call(search_deals, texto or "", [est] if est else None, HISTORY_PAGE, 0)
    await interaction.response.send_message(
        embed=_history_embed(rows, total, 0, est, texto), view=HistoryView(est, texto, total), ephemeral=True
    )


@bot.tree.command(name="exportar", description="Descarga tu cartera (compras, ventas y cancelaciones) en CSV para Excel")
async def slash_exportar(interaction: discord.Interaction):
    csv_text = await db_call(export_deals_csv)
    data = io.BytesIO(("﻿" + csv_text).encode("utf-8"))  # BOM para que Excel lea bien las tildes
    fname = f"cartera_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await interaction.response.send_message(
        "📤 Aquí tienes tu cartera completa (separador `;`).", file=discord.File(data, filename=fname), ephemeral=True
    )


@bot.tree.command(name="limpiar_alertas", description="Borra alertas antiguas que nunca compraste (conserva compras y ventas)")
@app_commands.describe(dias="Antigüedad mínima en días (por defecto 30)")
async def slash_limpiar_alertas(interaction: discord.Interaction, dias: int = 30):
    if dias < 7 or dias > 365:
        await interaction.response.send_message("❌ Indica entre 7 y 365 días.", ephemeral=True)
        return
    n = await db_call(count_stale_alerts, dias)
    if n == 0:
        await interaction.response.send_message(f"✨ No hay alertas sin comprar de hace más de {dias} días.", ephemeral=True)
        return
    view = discord.ui.View(timeout=300)
    view.add_item(discord.ui.Button(label=f"Sí, borrar {n} alertas", emoji="🧹", style=discord.ButtonStyle.danger,
                                    custom_id=f"deal:purge_ok:{int(dias)}"))
    await interaction.response.send_message(
        f"Se borrarán **{n}** alertas (pendientes o descartadas) de hace más de **{dias} días**. "
        f"Tus compras, ventas y cancelaciones no se tocan. Esta limpieza **no se puede deshacer**.",
        view=view, ephemeral=True
    )


class PortfolioView(discord.ui.View):
    """Selector del stock: abre la ficha del artículo con todos sus botones."""
    def __init__(self, stock):
        super().__init__(timeout=600)
        if stock:
            options = []
            for d in stock[:25]:
                days = f" • {d['days_in_stock']}d en stock" if d.get("days_in_stock") is not None else ""
                options.append(discord.SelectOption(
                    label=f"{d['model'][:70]} ({d['bought_price']:.0f}€)"[:100],
                    value=d["deal_id"][:100],
                    description=f"Objetivo {d['optimal_price']:.0f}€ • {d['platform']}{days}"[:100],
                    emoji="💾" if d.get("mode") == "personal" else "📦"
                ))
            self.sel = discord.ui.Select(placeholder="Elige un artículo para venderlo, editarlo o cancelarlo...", options=options)
            self.sel.callback = self.on_select
            self.add_item(self.sel)

    async def on_select(self, interaction: discord.Interaction):
        await _handle_deal_action(interaction, "view", self.sel.values[0])


@bot.tree.command(name="cartera", description="Tu stock, capital invertido y beneficio real (total y del mes)")
async def slash_cartera(interaction: discord.Interaction):
    s = await db_call(get_portfolio_summary)
    tot, month = s["sold"], s["month"]

    stock_lines = []
    for d in s["stock"][:15]:
        days = f" • {d['days_in_stock']}d" if d.get("days_in_stock") is not None else ""
        stale = " ⏳" if (d.get("days_in_stock") or 0) >= 21 else ""
        if d.get("mode") == "personal":
            stock_lines.append(f"• 💾 **{d['model'][:40]}** — `{d['bought_price']:.0f}€`{days}")
        else:
            diff = d["optimal_price"] - d["bought_price"]
            stock_lines.append(f"• 📦 **{d['model'][:40]}** — `{d['bought_price']:.0f}€` ➔ `{d['optimal_price']:.0f}€` (`{diff:+.0f}€`){days}{stale}")
    if len(s["stock"]) > 15:
        stock_lines.append(f"*…y {len(s['stock']) - 15} más (usa `/historial estado:En stock`).*")

    sold_lines = []
    for r in s["recent_sold"]:
        net = r["sold_price"] - r["bought_price"] - r["sale_fees"]
        sold_lines.append(f"• ✅ **{r['model'][:38]}** — `{r['bought_price']:.0f}€` ➔ `{r['sold_price']:.0f}€` (`{net:+.0f}€`) • {_fmt_date(r['sold_at'])}")

    desc = [
        f"📅 **Este mes:** `{month['count']}` ventas • beneficio **`{month['profit']:+.2f}€`**",
        f"🏆 **Total:** `{tot['count']}` ventas • ingresos `{tot['revenue']:.2f}€` • comisiones `{tot['fees']:.2f}€`",
        f"• **Beneficio neto real:** **`{tot['profit']:+.2f}€`** (ROI medio `{tot['roi']:.1f}%`)\n",
        f"📦 **Stock (`{len(s['stock'])}` artículos):** invertido `{s['invested']:.2f}€` • latente **`{s['latent_profit']:+.2f}€`** (`{s['latent_roi']:.1f}%`)",
    ]
    desc.append("\n".join(stock_lines) if stock_lines else "*No tienes stock. Pulsa ✅ en una alerta o usa `/comprado`.*")
    if sold_lines:
        desc.append("\n**Últimas ventas:**\n" + "\n".join(sold_lines))
    desc.append("\n💡 Elige un artículo abajo para gestionarlo • `/historial` • `/exportar` • `/deshacer`")

    embed = discord.Embed(title="💼 Cartera", description="\n".join(desc)[:4000], color=0xF1C40F)
    embed.set_footer(text="⏳ = más de 3 semanas en stock: plantéate bajar el precio")
    await interaction.response.send_message(embed=embed, view=PortfolioView(s["stock"]))


def _fetch_estado_data():
    conn = get_db()
    try:
        c = conn.cursor()
        platforms = ["WALLAPOP", "VINTED", "MILANUNCIOS", "CASHCONVERTERS", "TODOCOLECCION", "EBAY"]
        counts = {}
        for p in platforms:
            c.execute("SELECT COUNT(*) FROM market_seen_items WHERE platform = ?", (p,))
            counts[p] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM cex_cache")
        cex_cnt = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM cex_catalog_mirror")
        mirror_cnt = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM ai_prescreen_cache")
        prescreen_cnt = c.fetchone()[0]

        c.execute("SELECT COALESCE(SUM(usd), 0.0), COUNT(*) FROM ai_cost_log WHERE created_at >= datetime('now', 'start of day')")
        today_ai_cost, today_ai_calls = c.fetchone()

        c.execute(
            """SELECT platform, identified_model, price, net_profit, deal_score, item_url, COALESCE(deal_type, 'direct_chollo'), COALESCE(recommended_offer, 0)
               FROM deals ORDER BY created_at DESC LIMIT 5"""
        )
        recent_deals = c.fetchall()
    finally:
        conn.close()
    return counts, cex_cnt, mirror_cnt, prescreen_cnt, today_ai_cost, today_ai_calls, recent_deals


@bot.tree.command(name="estado", description="Estado en tiempo real de los 6 motores de escaneo, IA y base de datos")
async def slash_estado(interaction: discord.Interaction):
    data = await asyncio.to_thread(_fetch_estado_data)
    counts, cex_cnt, mirror_cnt, prescreen_cnt, today_ai_cost, today_ai_calls, recent_deals = data

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
    for plat, mod, pr, prof, sc, url, dtype, rec_off in recent_deals:
        if dtype == "offer_opportunity" and rec_off > 0:
            deal_lines.append(f"• 🤝 **[{plat}]** [{mod}]({url}) — Ofertar `{rec_off:.0f}€` (Anuncio `{pr:.0f}€` | **`+{prof:.0f}€`** | 🎯 `{sc}/10`)")
        else:
            deal_lines.append(f"• 🔥 **[{plat}]** [{mod}]({url}) — `{pr:.0f}€` (**`+{prof:.0f}€`** | ⭐ `{sc}/10`)")

    embed = discord.Embed(
        title="📡 Telemetría Global: Boti Sniper Suite v4.3",
        color=0x2ECC71,
        description=(
            f"🎯 **Radares Activos:** `{active_radars}/{len(cfg.get('targets', []))}` | "
            f"🏪 **CeX (Espejo/Caché):** `{mirror_cnt}` / `{cex_cnt}` | "
            f"🤖 **Pre-filtro IA en Caché:** `{prescreen_cnt}`\n"
            f"💰 **Gasto IA Hoy:** `${today_ai_cost:.4f}` ({today_ai_calls} llamadas) | "
            f"📁 **Kits en Disco:** `{kits_cnt}`\n\n"
            f"📊 **Base de Datos Multi-Mercado (`sniper.db` WAL):**\n" + "\n".join(plat_lines)
        )
    )
    if deal_lines:
        embed.add_field(name="🔥 Últimos 5 Chollos y Oportunidades de Oferta", value="\n".join(deal_lines)[:1020], inline=False)
    embed.set_footer(text=f"Servidor Linux Homelab • {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    await interaction.response.send_message(embed=embed)


def _fetch_stats_fraude():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM seen_image_hashes")
        total_hashes = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM ai_prescreen_cache WHERE decision = 'reject'")
        rejected_prescreen = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM fraud_log")
        fraud_log_count = c.fetchone()[0]

        c.execute(
            """SELECT reject_reason, confidence, evaluated_at 
               FROM ai_prescreen_cache 
               WHERE decision = 'reject' AND reject_reason != '' 
               ORDER BY evaluated_at DESC LIMIT 5"""
        )
        recent_rejections = c.fetchall()
    finally:
        conn.close()
    return total_hashes, rejected_prescreen, fraud_log_count, recent_rejections


@bot.tree.command(name="stats_fraude", description="Métricas de fotos hasheadas, descartes por IA y bloqueos antifraude")
async def slash_stats_fraude(interaction: discord.Interaction):
    total_hashes, rejected_prescreen, fraud_log_count, recent_rejections = await asyncio.to_thread(_fetch_stats_fraude)

    reasons_lines = []
    for reason, conf, eval_time in recent_rejections:
        time_str = str(eval_time).split()[1] if " " in str(eval_time) else str(eval_time)
        reasons_lines.append(f"• `[{conf.upper()}]` {reason} *({time_str})*")

    embed = discord.Embed(
        title="🛡️ Estadísticas del Motor Antifraude y Filtro IA",
        color=0xE74C3C,
        description=(
            f"🖼️ **Huellas Visuales en BD (`seen_image_hashes`):** `{total_hashes:,}`\n"
            f"🚫 **Anuncios Descartados en Puerta 1 (IA Prescreen):** `{rejected_prescreen:,}`\n"
            f"🛑 **Alertas Críticas de Fraude (`fraud_log`):** `{fraud_log_count:,}`\n"
        )
    )
    if reasons_lines:
        embed.add_field(name="📋 Últimos 5 Motivos de Descarte de IA", value="\n".join(reasons_lines)[:1020], inline=False)
    else:
        embed.add_field(name="📋 Últimos Motivos de Descarte", value="No hay descartes registrados aún.", inline=False)
    embed.set_footer(text=f"Antifraude Activo • {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    await interaction.response.send_message(embed=embed)


def _run_purgar_vistas(horas: int):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM market_seen_items WHERE seen_at >= datetime('now', ?)", (f"-{horas} hours",))
        deleted_market = c.rowcount
        c.execute("DELETE FROM seen_items WHERE seen_at >= datetime('now', ?)", (f"-{horas} hours",))
        deleted_seen = c.rowcount
        c.execute("DELETE FROM ai_prescreen_cache WHERE evaluated_at >= datetime('now', ?)", (f"-{horas} hours",))
        deleted_prescreen = c.rowcount
        c.execute("DELETE FROM ai_eval_cache WHERE evaluated_at >= datetime('now', ?)", (f"-{horas} hours",))
        deleted_eval = c.rowcount
        c.execute("DELETE FROM ai_prompt_cache WHERE created_at >= datetime('now', ?)", (f"-{horas} hours",))
        deleted_prompt = c.rowcount
        c.execute("DELETE FROM item_embeddings WHERE created_at >= datetime('now', ?)", (f"-{horas} hours",))
        deleted_embeddings = c.rowcount
        conn.commit()
    finally:
        conn.close()
    return {
        "market_seen_items": deleted_market + deleted_seen,
        "ai_prescreen_cache": deleted_prescreen + deleted_eval + deleted_prompt,
        "item_embeddings": deleted_embeddings
    }


@bot.tree.command(name="purgar_vistas", description="Elimina el historial de anuncios vistos y caché reciente para forzar re-escaneo")
@app_commands.describe(horas="Número de horas hacia atrás a purgar (por defecto: 24)")
async def slash_purgar_vistas(interaction: discord.Interaction, horas: int = 24):
    if horas <= 0 or horas > 720:
        await interaction.response.send_message("❌ El rango de horas debe estar entre 1 y 720 (máx 30 días).", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)
    res = await asyncio.to_thread(_run_purgar_vistas, horas)

    embed = discord.Embed(
        title="🧹 Purga de Historial Completada",
        color=0x3498DB,
        description=(
            f"Se ha purgado el historial de las últimas **{horas} horas** para permitir re-escanear el mercado:\n\n"
            f"• **Anuncios Vistos:** `{res['market_seen_items']:,}` filas eliminadas\n"
            f"• **Caché Pre-Filtro IA:** `{res['ai_prescreen_cache']:,}` filas eliminadas\n"
            f"• **Embeddings Semánticos:** `{res['item_embeddings']:,}` filas eliminadas\n\n"
            f"⚡ *Los escáneres volverán a evaluar los anuncios recientes en su siguiente ciclo.*"
        )
    )
    embed.set_footer(text=f"Operación Homelab • {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    await interaction.followup.send(embed=embed)


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


@bot.tree.command(name="ayuda", description="Muestra todos los comandos y cómo gestionar tus compras con Boti")
async def slash_ayuda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Boti: centro de mando de chollos y cartera",
        color=0x3498DB,
        description=(
            "**En cada alerta de chollo:**\n"
            "• **✅ ¡Comprado!** abre un formulario con el precio pagado (ya relleno) y notas. Queda en tu cartera con una ficha y sus botones.\n"
            "• **📋 Mensajes** te da el texto de oferta en ES/FR/IT. **📊 Grados CeX** muestra los precios de CeX en vivo. **🚫 Descartar** borra el aviso.\n\n"
            "**En la ficha de una compra:**\n"
            "• **📝 Crear anuncio de reventa**, **💰 Vendido** (con comisiones), **✏️ Editar**,\n"
            "• **↩️ La compra no se hizo** (sale del stock sin perder el historial), **🗑️ Eliminar** y **⏪ Deshacer**.\n\n"
            "**Cartera:**\n"
            "• `/cartera`: stock, capital invertido y beneficio real, total y del mes.\n"
            "• `/comprado`: registra una compra de una alerta o una compra manual (con precio).\n"
            "• `/vendido`: registra una venta y descuenta comisiones.\n"
            "• `/cancelar_compra`, `/editar`, `/eliminar`, `/deshacer`: corrige cualquier error.\n"
            "• `/ficha`: abre la ficha de cualquier anuncio. `/historial`: busca por estado o texto.\n"
            "• `/exportar`: descarga la cartera en CSV para Excel.\n\n"
            "**Mantenimiento:**\n"
            "• `/limpiar_alertas`: borra alertas viejas que nunca compraste.\n"
            "• `/purgar_vistas`: fuerza a reescanear anuncios recientes.\n"
            "• `/estado` y `/stats_fraude`: salud de los escáneres y del antifraude.\n\n"
            "**Herramientas:** `/tasar` (CeX + IA), `/radares` (editar radares), `/kits` (anuncios de reventa), `/guia` (fotos)."
        )
    )
    embed.set_footer(text="Todas las acciones de cartera se pueden deshacer con /deshacer")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    session = active_sessions.get(message.author.id)
    if session and session.get("waiting_photos") and message.attachments:
        if session.get("channel_id") and message.channel.id != session["channel_id"]:
            return

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
    touch_heartbeat()
    if not BOT_TOKEN:
        print("[FATAL] Falta DISCORD_BOT_TOKEN en variables de entorno.")
    else:
        bot.run(BOT_TOKEN)