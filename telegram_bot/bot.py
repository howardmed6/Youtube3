import os
import re
import requests
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ─── Variables de entorno ───────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TMDB_API_KEY        = os.environ["TMDB_API_KEY"]
CLAUDE_API_KEY      = os.environ["CLAUDE_API_KEY"]
GITHUB_TOKEN        = os.environ["GITHUB_TOKEN"]
GITHUB_REPO         = os.environ["GITHUB_REPO"]   # formato: usuario/repo

# ─── Estado de conversación por usuario ─────────────────────────
user_state = {}


# ════════════════════════════════════════════════════════════════
#  UTILIDADES
# ════════════════════════════════════════════════════════════════

def traducir_sinopsis(texto: str) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",          # ← fix: modelo actualizado
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": (
                "Traduce este texto al español. "
                "Si ya está en español devuélvelo tal cual "
                "sin comentarios ni explicaciones. "
                "Solo devuelve el texto final:\n\n" + texto
            )
        }]
    )
    return msg.content[0].text.strip()


def obtener_mejor_overview(data: dict) -> str:
    """
    Prioridad:
      1. Overview en inglés (en-US)
      2. Overview más largo entre TODAS las traducciones disponibles en TMDB
    Así funciona con películas en cualquier idioma.
    """
    overview_en = data.get("overview", "").strip()
    if overview_en:
        return overview_en

    traducciones = data.get("translations", {}).get("translations", [])
    mejor_texto = ""
    for t in traducciones:
        ov = t.get("data", {}).get("overview", "").strip()
        if len(ov) > len(mejor_texto):
            mejor_texto = ov

    return mejor_texto


def buscar_pelicula(nombre: str) -> dict | None:
    """Busca una película/serie en TheMovieDB por nombre."""
    url = "https://api.themoviedb.org/3/search/multi"
    params = {
        "api_key": TMDB_API_KEY,
        "query": nombre,
        "language": "en-US",
        "append_to_response": "translations"
    }
    r = requests.get(url, params=params)
    resultados = r.json().get("results", [])
    for item in resultados:
        if item.get("media_type") in ("movie", "tv"):
            return item
    return None


def buscar_por_url(tmdb_url: str) -> tuple[dict, str] | None:
    """Extrae el ID y tipo desde una URL de themoviedb.org y consulta la API."""
    patron = r"themoviedb\.org/(movie|tv)/(\d+)"
    m = re.search(patron, tmdb_url)
    if not m:
        return None

    tipo      = m.group(1)          # "movie" o "tv"
    movie_id  = m.group(2)
    endpoint  = "movie" if tipo == "movie" else "tv"

    url = f"https://api.themoviedb.org/3/{endpoint}/{movie_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "watch/providers,translations"
    }
    r = requests.get(url, params=params)
    data = r.json()

    # Fallback de overview universal
    data["overview"] = obtener_mejor_overview(data)

    return data, endpoint


def disparar_github_action(payload: dict) -> bool:
    """Dispara el workflow de GitHub Actions via repository_dispatch."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    data = {"event_type": "make_short", "client_payload": payload}
    r = requests.post(url, json=data, headers=headers)
    return r.status_code == 204


def formatear_resultado(data: dict, media_type: str) -> tuple[str, str | None, str]:
    """Formatea la info de la peli para mostrarla en Telegram."""
    titulo      = data.get("title") or data.get("name", "Sin título")
    sinopsis_raw = obtener_mejor_overview(data)
    sinopsis_es  = traducir_sinopsis(sinopsis_raw) if sinopsis_raw else "Sin sinopsis disponible"
    año          = (data.get("release_date") or data.get("first_air_date", ""))[:4]
    poster       = data.get("poster_path", "")
    poster_url   = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None
    tipo         = "🎬 Película" if media_type == "movie" else "📺 Serie"

    texto = (
        f"{tipo}: *{titulo}* ({año})\n\n"
        f"📝 *Sinopsis:*\n{sinopsis_es}"
    )
    return texto, poster_url, titulo


# ════════════════════════════════════════════════════════════════
#  HANDLERS
# ════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola! Soy tu bot de YouTube Shorts.\n"
        "Escribe /nuevo para crear un nuevo short."
    )


async def nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo preguntando el modo del video."""
    user_id = update.effective_user.id
    user_state[user_id] = {}

    teclado = [
        [InlineKeyboardButton("🖼 Imagen de Drive",        callback_data="modo_1")],
        [InlineKeyboardButton("🎥 Trailer de Drive",       callback_data="modo_2")],
        [InlineKeyboardButton("🤖 Imagen con IA (Gemini)", callback_data="modo_3")],
    ]
    await update.message.reply_text(
        "¿Cómo será el video?",
        reply_markup=InlineKeyboardMarkup(teclado)
    )


async def elegir_modo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el modo elegido y pide el nombre de la peli."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    modo = int(query.data.split("_")[1])
    user_state[user_id] = {"modo": modo, "paso": "esperando_nombre"}

    modos = {1: "Imagen de Drive", 2: "Trailer de Drive", 3: "Imagen IA"}
    await query.edit_message_text(
        f"✅ Modo: *{modos[modo]}*\n\n"
        f"Ahora escríbeme el nombre de la película o serie\n"
        f"_(también puedes pegar directamente el enlace de themoviedb.org)_",
        parse_mode="Markdown"
    )


async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja todos los mensajes de texto según el estado del usuario."""
    user_id = update.effective_user.id
    texto   = update.message.text.strip()
    estado  = user_state.get(user_id, {})
    paso    = estado.get("paso")

    # ── Esperando nombre o URL de la peli ───────────────────────
    if paso == "esperando_nombre":

        # Si pega un enlace de TMDB lo procesamos directo
        if "themoviedb.org" in texto:
            await update.message.reply_text("🔍 Buscando con esa URL...")
            resultado_url = buscar_por_url(texto)

            if not resultado_url:
                await update.message.reply_text(
                    "❌ No pude extraer la info de esa URL. Verifica que sea correcta."
                )
                return

            data, media_type = resultado_url
            movie_id = data.get("id")
            descripcion, poster_url, titulo = formatear_resultado(data, media_type)

        else:
            # Búsqueda por nombre normal
            await update.message.reply_text(f"🔍 Buscando *{texto}*...", parse_mode="Markdown")
            try:
                resultado = buscar_pelicula(texto)
            except Exception as e:
                await update.message.reply_text(f"❌ Error al buscar: {str(e)}")
                return

            if not resultado:
                await update.message.reply_text(
                    "❌ No encontré nada. Intenta con otro nombre, pega el enlace de TMDB, "
                    "o escribe /nuevo para empezar de nuevo."
                )
                return

            media_type  = resultado.get("media_type", "movie")
            movie_id    = resultado.get("id")
            descripcion, poster_url, titulo = formatear_resultado(resultado, media_type)

        user_state[user_id].update({
            "paso"      : "confirmando",
            "movie_id"  : movie_id,
            "media_type": media_type,
            "titulo"    : titulo
        })

        teclado = [[
            InlineKeyboardButton("✅ Sí, es esta",      callback_data="confirmar_si"),
            InlineKeyboardButton("❌ No, buscar otra", callback_data="confirmar_no"),
        ]]
        reply_markup = InlineKeyboardMarkup(teclado)

        if poster_url:
            await update.message.reply_photo(
                photo=poster_url,
                caption=descripcion,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                descripcion, parse_mode="Markdown", reply_markup=reply_markup
            )

    # ── Esperando URL de themoviedb (flujo "no era la correcta") ─
    elif paso == "esperando_url":
        if "themoviedb.org" not in texto:
            await update.message.reply_text(
                "⚠️ Por favor envía una URL válida de themoviedb.org\n"
                "Ejemplo: `https://www.themoviedb.org/movie/26954-puppet-master-ii`",
                parse_mode="Markdown"
            )
            return

        await update.message.reply_text("🔍 Buscando con esa URL...")
        resultado_url = buscar_por_url(texto)

        if not resultado_url:
            await update.message.reply_text(
                "❌ No pude extraer la info de esa URL. Verifica que sea correcta."
            )
            return

        data, media_type = resultado_url
        movie_id = data.get("id")
        descripcion, poster_url, titulo = formatear_resultado(data, media_type)

        user_state[user_id].update({
            "paso"      : "confirmando",
            "movie_id"  : movie_id,
            "media_type": media_type,
            "titulo"    : titulo
        })

        teclado = [[
            InlineKeyboardButton("✅ Sí, es esta",    callback_data="confirmar_si"),
            InlineKeyboardButton("❌ No es correcta", callback_data="confirmar_no"),
        ]]
        reply_markup = InlineKeyboardMarkup(teclado)

        if poster_url:
            await update.message.reply_photo(
                photo=poster_url,
                caption=descripcion,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                descripcion, parse_mode="Markdown", reply_markup=reply_markup
            )

    else:
        await update.message.reply_text(
            "Escribe /nuevo para crear un short o /start para ver las opciones."
        )


async def confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la confirmación de la búsqueda."""
    query   = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    estado  = user_state.get(user_id, {})

    if query.data == "confirmar_si":
        payload = {
            "movie_id"  : estado["movie_id"],
            "media_type": estado["media_type"],
            "modo"      : estado["modo"],
            "titulo"    : estado["titulo"]
        }
        exito = disparar_github_action(payload)

        if exito:
            await query.edit_message_caption(
                caption=(
                    f"🚀 ¡Perfecto! Procesando *{estado['titulo']}*...\n\n"
                    f"AutoShort está generando el short. "
                    f"En unos minutos estará en YouTube. ✅"
                ),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_caption(
                caption="❌ Hubo un error al disparar GitHub Actions. Intenta de nuevo con /nuevo.",
                parse_mode="Markdown"
            )
        user_state.pop(user_id, None)

    elif query.data == "confirmar_no":
        user_state[user_id]["paso"] = "esperando_url"
        await query.edit_message_caption(
            caption=(
                "Okay, envíame la URL exacta de la película en themoviedb.org\n\n"
                "Ejemplo:\n`https://www.themoviedb.org/movie/26954-puppet-master-ii`"
            ),
            parse_mode="Markdown"
        )


# ════════════════════════════════════════════════════════════════
#  HEALTH CHECK + MAIN
# ════════════════════════════════════════════════════════════════

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheck)
    server.serve_forever()

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("nuevo", nuevo))
    app.add_handler(CallbackQueryHandler(elegir_modo,  pattern="^modo_"))
    app.add_handler(CallbackQueryHandler(confirmar,    pattern="^confirmar_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))
    print("Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()