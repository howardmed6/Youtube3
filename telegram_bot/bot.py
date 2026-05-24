import os
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

# ────────────────────────────────────────────────────────────────
# UTILIDADES
# ────────────────────────────────────────────────────────────────

def traducir_sinopsis(texto_en: str) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"Traduce este texto al español. Si ya está en español devuélvelo tal cual sin comentarios ni explicaciones. Solo devuelve el texto final:\n\n{texto_en}"
        }]
    )
    return msg.content[0].text.strip()


def buscar_pelicula(nombre: str):
    """Busca una película/serie en TheMovieDB por nombre."""
    url = f"https://api.themoviedb.org/3/search/multi"
    params = {"api_key": TMDB_API_KEY, "query": nombre, "language": "es-ES"}
    r = requests.get(url, params=params)
    resultados = r.json().get("results", [])
    if not resultados:
        return None
    # Toma el primer resultado relevante
    for item in resultados:
        if item.get("media_type") in ("movie", "tv"):
            return item
    return None


def buscar_por_url(tmdb_url: str):
    """Extrae el ID y tipo desde una URL de themoviedb.org y consulta la API."""
    # Ejemplo: https://www.themoviedb.org/movie/26954-puppet-master-ii
    partes = tmdb_url.rstrip("/").split("/")
    tipo_raw = partes[-2] if partes[-1].replace("-","").isalpha() else partes[-2]
    segmento = partes[-1] if not partes[-1].startswith("http") else partes[-2]

    # Detectar tipo y ID
    for i, p in enumerate(partes):
        if p in ("movie", "tv"):
            tipo = p
            id_raw = partes[i + 1].split("-")[0]
            movie_id = int(id_raw)
            break
    else:
        return None

    endpoint = "movie" if tipo == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{movie_id}"
    params = {"api_key": TMDB_API_KEY, "language": "en-US",
              "append_to_response": "watch/providers"}
    r = requests.get(url, params=params)
    return r.json(), endpoint


def disparar_github_action(payload: dict):
    """Dispara el workflow de GitHub Actions via repository_dispatch."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    data = {"event_type": "make_short", "client_payload": payload}
    r = requests.post(url, json=data, headers=headers)
    return r.status_code == 204


def formatear_resultado(data: dict, media_type: str) -> str:
    """Formatea la info de la peli para mostrarla en Telegram."""
    titulo = data.get("title") or data.get("name", "Sin título")
    sinopsis_en = data.get("overview", "Sin sinopsis disponible")
    sinopsis_es = traducir_sinopsis(sinopsis_en) if sinopsis_en else "Sin sinopsis"
    año = (data.get("release_date") or data.get("first_air_date", ""))[:4]
    poster = data.get("poster_path", "")
    poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None
    tipo = "🎬 Película" if media_type == "movie" else "📺 Serie"

    texto = (
        f"{tipo}: *{titulo}* ({año})\n\n"
        f"📝 *Sinopsis:*\n{sinopsis_es}"
    )
    return texto, poster_url, titulo


# ────────────────────────────────────────────────────────────────
# HANDLERS
# ────────────────────────────────────────────────────────────────

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
        [InlineKeyboardButton("🖼 Imagen de Drive", callback_data="modo_1")],
        [InlineKeyboardButton("🎥 Trailer de Drive", callback_data="modo_2")],
        [InlineKeyboardButton("🤖 Imagen con IA (Gemini)", callback_data="modo_3")],
    ]
    reply_markup = InlineKeyboardMarkup(teclado)
    await update.message.reply_text(
        "¿Cómo será el video?", reply_markup=reply_markup
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
        f"Ahora escríbeme el nombre de la película o serie:",
        parse_mode="Markdown"
    )


async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja todos los mensajes de texto según el estado del usuario."""
    user_id = update.effective_user.id
    texto = update.message.text.strip()
    estado = user_state.get(user_id, {})
    paso = estado.get("paso")

    # ── Esperando nombre de la peli ──────────────────────────────
    if paso == "esperando_nombre":
        await update.message.reply_text(f"🔍 Buscando *{texto}*...", parse_mode="Markdown")
        try:
            resultado = buscar_pelicula(texto)
        except Exception as e:
            await update.message.reply_text(f"❌ Error al buscar: {str(e)}")
            return

        if not resultado:
            await update.message.reply_text(
                "❌ No encontré nada. Intenta con otro nombre o escribe /nuevo para empezar de nuevo."
            )
            return

        if not resultado:
            await update.message.reply_text(
                "❌ No encontré nada. Intenta con otro nombre o escribe /nuevo para empezar de nuevo."
            )
            return

        media_type = resultado.get("media_type", "movie")
        movie_id = resultado.get("id")
        descripcion, poster_url, titulo = formatear_resultado(resultado, media_type)

        user_state[user_id].update({
            "paso": "confirmando",
            "movie_id": movie_id,
            "media_type": media_type,
            "titulo": titulo
        })

        teclado = [
            [
                InlineKeyboardButton("✅ Sí, es esta", callback_data="confirmar_si"),
                InlineKeyboardButton("❌ No, buscar otra", callback_data="confirmar_no"),
            ]
        ]
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

    # ── Esperando URL de themoviedb ──────────────────────────────
    elif paso == "esperando_url":
        if "themoviedb.org" not in texto:
            await update.message.reply_text(
                "⚠️ Por favor envía una URL válida de themoviedb.org\n"
                "Ejemplo: https://www.themoviedb.org/movie/26954-puppet-master-ii"
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
            "paso": "confirmando",
            "movie_id": movie_id,
            "media_type": media_type,
            "titulo": titulo
        })

        teclado = [
            [
                InlineKeyboardButton("✅ Sí, es esta", callback_data="confirmar_si"),
                InlineKeyboardButton("❌ No es correcta", callback_data="confirmar_no"),
            ]
        ]
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
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    estado = user_state.get(user_id, {})

    if query.data == "confirmar_si":
        # Disparar GitHub Actions
        payload = {
            "movie_id": estado["movie_id"],
            "media_type": estado["media_type"],
            "modo": estado["modo"],
            "titulo": estado["titulo"]
        }
        exito = disparar_github_action(payload)

        if exito:
            await query.edit_message_caption(
                caption=f"🚀 ¡Perfecto! Procesando *{estado['titulo']}*...\n\n"
                        f"GitHub Actions está generando el short. "
                        f"En unos minutos estará en YouTube. ✅",
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
            caption="Okay, envíame la URL exacta de la película en themoviedb.org\n\n"
                    "Ejemplo:\n`https://www.themoviedb.org/movie/26954-puppet-master-ii`",
            parse_mode="Markdown"
        )

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
    app.add_handler(CallbackQueryHandler(elegir_modo, pattern="^modo_"))
    app.add_handler(CallbackQueryHandler(confirmar, pattern="^confirmar_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))
    print("Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()