import os
import json
import random
import requests
import anthropic
import subprocess
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google import genai
import io
from datetime import datetime

# ─── Variables de entorno ────────────────────────────────────────
CLAUDE_API_KEY          = os.environ["CLAUDE_API_KEY"]
GEMINI_API_KEY          = os.environ["GEMINI_API_KEY"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
YOUTUBE_CLIENT_SECRET   = os.environ["YOUTUBE_CLIENT_SECRET"]

TELEGRAM_BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID        = os.environ["TELEGRAM_CHAT_ID"]
DRIVE_FOLDER_ID         = "1NLhq9q1wxmfTpydDv72Iu4LRt3SoJ-tO"

# ─── Clientes ────────────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ─── Credenciales Google ─────────────────────────────────────────
creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=[
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/youtube.upload"
    ]
)
drive_service = build("drive", "v3", credentials=creds)


def limpiar_texto(texto: str) -> str:
    reemplazos = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
        'ñ': 'n', 'Ñ': 'N', 'ü': 'u', 'Ü': 'U',
        "'": "", '"': '', ':': '-', '\\': '', '/': '-',
        '(': '', ')': '', '[': '', ']': '', '&': 'y',
        '!': '', '?': '', ',': ' ', ';': ' '
    }
    for k, v in reemplazos.items():
        texto = texto.replace(k, v)
    return texto


def justificar_lineas(sinopsis: str, max_chars: int = 44, max_lineas: int = 5) -> list:
    palabras = sinopsis.split()
    lineas_raw = []
    linea_actual = []
    cuenta = 0
    for palabra in palabras:
        if cuenta + len(palabra) + len(linea_actual) <= max_chars:
            linea_actual.append(palabra)
            cuenta += len(palabra)
        else:
            lineas_raw.append(linea_actual)
            linea_actual = [palabra]
            cuenta = len(palabra)
    if linea_actual:
        lineas_raw.append(linea_actual)
    lineas_raw = lineas_raw[:max_lineas]
    lineas_justificadas = []
    for i, palabras_linea in enumerate(lineas_raw):
        if i == len(lineas_raw) - 1 or len(palabras_linea) == 1:
            lineas_justificadas.append(" ".join(palabras_linea))
            continue
        chars_palabras = sum(len(p) for p in palabras_linea)
        espacios_totales = max_chars - chars_palabras
        gaps = len(palabras_linea) - 1
        espacio_base = espacios_totales // gaps
        extras = espacios_totales % gaps
        linea = ""
        for j, palabra in enumerate(palabras_linea):
            linea += palabra
            if j < gaps:
                linea += " " * espacio_base
                if j < extras:
                    linea += " "
        lineas_justificadas.append(linea)
    return lineas_justificadas


def obtener_resolucion(ruta: str) -> tuple:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0", ruta
    ]
    resultado = subprocess.run(cmd, capture_output=True, text=True)
    partes = resultado.stdout.strip().split(",")
    ancho = int(partes[0])
    alto  = int(partes[1])
    print(f"Resolucion detectada: {ancho}x{alto}")
    return ancho, alto


def descargar_desde_drive():
    resultados = drive_service.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name, mimeType)"
    ).execute()
    archivos = resultados.get("files", [])
    if not archivos:
        raise Exception("No hay archivos en la carpeta de Drive")
    archivo    = archivos[0]
    file_id    = archivo["id"]
    nombre     = archivo["name"]
    ext        = Path(nombre).suffix.lower()
    print(f"Descargando {nombre} de Drive...")
    request    = drive_service.files().get_media(fileId=file_id)
    ruta_local = f"/tmp/media{ext}"
    with open(ruta_local, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return ruta_local, ext


def buscar_imagen_gemini(titulo: str) -> str:
    import urllib.request
    response = gemini_client.models.generate_content(
        model="gemini-1.5-flash",
        contents=f"Give me a direct image URL (jpg or png) related to the movie or TV show '{titulo}'. Return only the URL, nothing else."
    )
    url  = response.text.strip()
    ruta = "/tmp/gemini_image.jpg"
    urllib.request.urlretrieve(url, ruta)
    return ruta


def construir_video(data: dict, ruta_media: str, es_video: bool) -> str:
    sinopsis   = limpiar_texto(data["sinopsis"])
    generos    = limpiar_texto(", ".join(data["generos"][:3]))
    año        = data["año"]
    pais       = limpiar_texto(data["pais"][:25])
    tipo       = limpiar_texto(data["tipo"])
    poster_url = data["poster_url"]

    ancho_orig, alto_orig = obtener_resolucion(ruta_media)
    ratio   = alto_orig / ancho_orig
    h_sec2  = int(1080 * ratio)
    h_sec2  = max(500, min(1300, h_sec2))
    espacio = 1920 - h_sec2
    h_sec1  = espacio // 2
    h_sec3  = espacio - h_sec1
    y_sec1  = 0
    y_sec2  = h_sec1
    y_sec3  = y_sec2 + h_sec2

    print(f"Layout: sec1={h_sec1}px sec2={h_sec2}px sec3={h_sec3}px total={h_sec1+h_sec2+h_sec3}px")

    alto_poster  = h_sec3 - 12
    ancho_poster = int(alto_poster * 0.67)
    y_poster     = y_sec3 + 8
    x_datos      = ancho_poster + 25

    separacion  = h_sec3 // 4
    font_tit    = min(48, separacion - 10)
    font_dat    = min(42, separacion - 15)
    chars_datos = (1080 - x_datos) // 2 // (font_dat // 2)
    lineas_gen  = justificar_lineas(generos, max_chars=chars_datos, max_lineas=2)
    lineas_pais = justificar_lineas(pais, max_chars=chars_datos, max_lineas=2)
    y_g_label   = y_sec3 + (separacion * 0) + 10
    y_g_val     = y_sec3 + (separacion * 1) + 10
    y_pais      = y_sec3 + (separacion * 2) + 10
    y_tipo      = y_sec3 + (separacion * 3) + 10

    paletas = [
        {"marco": "0x00FFFF", "titulo": "cyan",     "fondo": "0x001a1a@0.9"},
        {"marco": "0xFF6B00", "titulo": "orange",   "fondo": "0x1a0a00@0.9"},
        {"marco": "0xFF00FF", "titulo": "fuchsia",  "fondo": "0x1a001a@0.9"},
        {"marco": "0x00FF88", "titulo": "0x00FF88", "fondo": "0x001a0a@0.9"},
        {"marco": "0xFFD700", "titulo": "gold",     "fondo": "0x1a1400@0.9"},
    ]
    paleta       = random.choice(paletas)
    color_marco  = paleta["marco"]
    color_titulo = paleta["titulo"]
    color_fondo  = paleta["fondo"]

    poster_path = "/tmp/poster.jpg"
    if poster_url:
        r = requests.get(poster_url)
        with open(poster_path, "wb") as f:
            f.write(r.content)

    salida   = "/tmp/output_video.mp4"
    duracion = random.randint(30, 60) if not es_video else None

    espacio_sinopsis = h_sec1 - 70
    ancho_texto      = 1020
    for font_sin in range(120, 16, -2):
        line_height  = int(font_sin * 1.4)
        chars_linea  = int(ancho_texto / (font_sin * 0.55))
        max_lin      = espacio_sinopsis // line_height
        lineas       = justificar_lineas(sinopsis, max_chars=chars_linea, max_lineas=99)
        if len(lineas) <= max_lin:
            break
    lineas = lineas[:max_lin]

    sinopsis_filters = ""
    v_actual = "vs0"
    for i, linea in enumerate(lineas):
        v_siguiente = f"vs{i+1}"
        y_pos = 70 + (i * line_height)
        sinopsis_filters += (
            f"[{v_actual}]drawtext=text='{linea}':fontsize={font_sin}:fontcolor=white"
            f":x=30:y={y_pos}:shadowcolor=black:shadowx=2:shadowy=2[{v_siguiente}];"
        )
        v_actual = v_siguiente

    filtros = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[base];"
        f"[base]drawbox=x=0:y=0:w=1080:h=1920:color=black@0.5:t=fill[dark];"
        f"[dark]drawbox=x=0:y=0:w=1080:h={h_sec1}:color={color_fondo}:t=fill[sec1];"
        f"[sec1]drawbox=x=0:y=0:w=1080:h={h_sec1}:color={color_marco}@0.9:t=4[sec1b];"
        f"[sec1b]drawtext=text='Sinopsis':fontsize=34:fontcolor={color_titulo}"
        f":x=30:y=20:shadowcolor=black:shadowx=2:shadowy=2[vs0];"
        f"{sinopsis_filters}"
        f"[{v_actual}]drawbox=x=0:y={y_sec2}:w=1080:h={h_sec2}:color={color_marco}@0.9:t=4[sec2];"
        f"[sec2]drawbox=x=0:y={y_sec3}:w=1080:h={h_sec3}:color={color_fondo}:t=fill[sec3];"
        f"[sec3]drawbox=x=0:y={y_sec3}:w=1080:h={h_sec3}:color={color_marco}@0.9:t=4[sec3b];"
        f"[1:v]scale={ancho_poster}:{alto_poster}[poster];"
        f"[sec3b][poster]overlay=10:{y_poster}[con_poster];"
        f"[con_poster]drawtext=text='Generos':fontsize={font_tit}:fontcolor={color_titulo}"
        f":x={x_datos}:y={y_g_label}:shadowcolor=black:shadowx=1:shadowy=1[d1];"
        f"[d1]drawtext=text='{lineas_gen[0]}':fontsize={font_dat}:fontcolor=white:x={x_datos}:y={y_g_val}[d1b];"
        f"[d1b]drawtext=text='{lineas_gen[-1]}':fontsize={font_dat}:fontcolor=white:x={x_datos}:y={y_g_val + font_dat + 5}[d2];"
        f"[d2]drawtext=text='{lineas_pais[0]}':fontsize={font_dat}:fontcolor=white:x={x_datos}:y={y_pais}[d2b];"
        f"[d2b]drawtext=text='{lineas_pais[-1]}':fontsize={font_dat}:fontcolor=white:x={x_datos}:y={y_pais + font_dat + 5}[d3];"
        f"[d3]drawtext=text='{tipo}  {año}':fontsize={font_dat}:fontcolor=white:x={x_datos}:y={y_tipo}[d4];"
        f"[d4]drawtext=text='':fontsize=1:fontcolor=black:x=0:y=0[out]"
    )

    if es_video:
        cmd = [
            "ffmpeg", "-y",
            "-i", ruta_media,
            "-i", poster_path,
            "-filter_complex", filtros,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            salida
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duracion), "-i", ruta_media,
            "-i", poster_path,
            "-filter_complex", filtros,
            "-map", "[out]",
            "-c:v", "libx264",
            "-t", str(duracion),
            salida
        ]

    subprocess.run(cmd, check=True)
    return salida


def agregar_audio(video_path: str, generos: list) -> str:
    genero_principal = generos[0].lower() if generos else "accion"
    mapeo = {
        "action": "accion", "thriller": "accion",
        "horror": "terror", "terror": "terror",
        "comedy": "comedia", "comedia": "comedia",
        "romance": "romance", "drama": "drama",
        "science fiction": "scifi", "sci-fi": "scifi",
        "animation": "comedia", "fantasy": "fantasia",
        "adventure": "aventura", "aventura": "aventura",
        "crime": "crimen", "mystery": "crimen",
        "history": "historia", "western": "historia"
    }
    categoria = mapeo.get(genero_principal, "accion")
    audio_dir = Path("audio")
    audios    = list(audio_dir.glob(f"{categoria}*.mp3"))
    if not audios:
        audios = list(audio_dir.glob("*.mp3"))
    if not audios:
        print("No se encontro audio continuando sin musica")
        return video_path
    audio_elegido = random.choice(audios)
    print(f"Audio elegido: {audio_elegido}")
    salida = "/tmp/output_with_audio.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", str(audio_elegido),
        "-filter_complex", "[1:a]volume=0.3[a]",
        "-map", "0:v",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        salida
    ]
    subprocess.run(cmd, check=True)
    return salida


def generar_metadata(data: dict) -> dict:
    titulo             = data["titulo"]
    sinopsis           = data["sinopsis"]
    generos            = ", ".join(data["generos"])
    plataformas_pago   = ", ".join(data["plataformas"]["pago"][:3])
    plataformas_gratis = ", ".join(data["plataformas"]["gratis"][:3])
    msg = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Genera metadata para un YouTube Short recomendando esta pelicula o serie.
Titulo: {titulo}
Sinopsis: {sinopsis}
Generos: {generos}
Disponible en: {plataformas_pago}
Gratis en: {plataformas_gratis}

Responde SOLO en este formato JSON exacto:
{{
  "titulo_yt": "titulo llamativo para YouTube Shorts maximo 60 caracteres incluyendo emoji",
  "descripcion": "descripcion corta maximo 200 caracteres recomendando la peli mencionando plataformas",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "Shorts", "peliculas", "series"]
}}"""
        }]
    )
    texto = msg.content[0].text.strip()
    texto = texto.replace("```json", "").replace("```", "").strip()
    return json.loads(texto)


def generar_metadata_tiktok(data: dict) -> dict:
    """Genera metadata optimizada para TikTok con hashtags virales embebidos en el título."""
    titulo           = data["titulo"]
    sinopsis         = data["sinopsis"]
    generos          = ", ".join(data["generos"])
    plataformas_pago = data["plataformas"]["pago"]
    año_actual       = datetime.now().year

    # Detectar plataformas conocidas para hashtags específicos
    hashtags_plataforma = []
    plataformas_lower = [p.lower() for p in plataformas_pago]
    if any("netflix" in p for p in plataformas_lower):
        hashtags_plataforma.append(f"#Netflix #Netflix{año_actual}")
    if any("hbo" in p or "max" in p for p in plataformas_lower):
        hashtags_plataforma.append(f"#HBOMax #Max")
    if any("disney" in p for p in plataformas_lower):
        hashtags_plataforma.append(f"#DisneyPlus")
    if any("prime" in p or "amazon" in p for p in plataformas_lower):
        hashtags_plataforma.append(f"#PrimeVideo #AmazonPrime")
    if any("apple" in p for p in plataformas_lower):
        hashtags_plataforma.append(f"#AppleTVPlus")

    hashtags_extra = " ".join(hashtags_plataforma)

    msg = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"""Genera el titulo para un video de TikTok recomendando esta pelicula o serie.
Titulo: {titulo}
Sinopsis: {sinopsis}
Generos: {generos}
Plataformas donde se ve: {", ".join(plataformas_pago)}
Año actual: {año_actual}
Hashtags de plataforma ya detectados: {hashtags_extra}

Reglas estrictas:
- El titulo debe ser un gancho corto e impactante (maximo 80 caracteres sin contar hashtags)
- Agrega al final hashtags virales en ESPAÑOL e INGLES mezclados
- Incluye siempre: #peliculas #pelicula #series #recomendacion #fyp #foryou #parati
- Si es Netflix incluye: #Netflix #Netflix{año_actual}
- Si hay otras plataformas usa sus hashtags correspondientes
- Añade hashtags del genero en español e ingles (ej: #terror #horror #accion #action)
- Añade #{año_actual} y #quevertiktok #quedver
- Total del texto (titulo + hashtags) maximo 150 caracteres

Responde SOLO en este formato JSON exacto sin ningun texto adicional:
{{
  "titulo_tiktok": "gancho impactante #hashtag1 #hashtag2 #hashtag3 ..."
}}"""
        }]
    )
    texto = msg.content[0].text.strip()
    texto = texto.replace("```json", "").replace("```", "").strip()
    result = json.loads(texto)

    # Asegurar que no excede 150 chars (límite de TikTok)
    if len(result["titulo_tiktok"]) > 150:
        result["titulo_tiktok"] = result["titulo_tiktok"][:147] + "..."

    return result


def mandar_a_telegram(video_path: str, data: dict, metadata: dict, video_id: str = None):
    plataformas_pago   = ", ".join(data["plataformas"]["pago"]) or "No disponible"
    plataformas_gratis = ", ".join(data["plataformas"]["gratis"]) or "No disponible"
    link_youtube = f"https://youtube.com/watch?v={video_id}" if video_id else "Subiendo..."

    url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    mensaje = (
        f"🎬 *{data['titulo']}* ({data['año']})\n\n"
        f"🔗 *YouTube:* {link_youtube}\n\n"
        f"💰 Pago: {plataformas_pago}\n"
        f"🆓 Gratis: {plataformas_gratis}\n\n"
        f"📝 *Título:* {metadata['titulo_yt']}"
    )
    requests.post(url_msg, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    })

    url_vid = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    with open(video_path, "rb") as video:
        requests.post(url_vid, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": f"✅ *{data['titulo']}* — Publicado",
            "parse_mode": "Markdown"
        }, files={"video": video})
    print("Video y link enviados a Telegram")


def publicar_comentario_youtube(youtube, video_id, data):
    pago   = ", ".join(data["plataformas"]["pago"][:3])
    gratis = ", ".join(data["plataformas"]["gratis"][:3])
    texto = (
        f"🎬 ¿Dónde ver esta película?\n"
        f"💰 Disponible en: {pago}\n"
        f"🆓 Gratis en: {gratis}\n\n"
        "¡Suscríbete para más recomendaciones diarias!"
    )
    youtube.commentThreads().insert(
        part="snippet",
        body={"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": texto}}}}
    ).execute()
    print("Comentario informativo publicado en YouTube")


def subir_a_youtube(video_path: str, metadata: dict):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id="319336541942-viq6kp008eolvngq6afr4vmh2h2fh8bq.apps.googleusercontent.com",
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"]
    )
    creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": metadata["titulo_yt"],
            "description": metadata["descripcion"],
            "tags": metadata["tags"],
            "categoryId": "24"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )
    print("Subiendo a YouTube...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Subiendo... {int(status.progress() * 100)}%")
    print(f"Video subido: https://youtube.com/watch?v={response['id']}")
    return response["id"], youtube


def subir_a_tiktok(video_path: str, metadata: dict, access_token: str):
    video_size = os.path.getsize(video_path)

    # Estructura exacta de tu versión que funcionaba
    payload = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1
        }
    }

    # Endpoint de INBOX (Borrador/Bandeja)
    url_init = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
    
    init_req = requests.post(
        url_init,
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    )
    
    # Verificamos si la respuesta es JSON antes de intentar acceder a 'data'
    try:
        init_resp = init_req.json()
    except Exception as e:
        print(f"Error de respuesta (No es JSON): {init_req.text}")
        raise e

    if "data" not in init_resp:
        raise Exception(f"Error en init: {init_resp}")

    upload_url = init_resp["data"]["upload_url"]
    publish_id = init_resp["data"]["publish_id"]
    print(f"✅ TikTok upload iniciado (ID: {publish_id})")

    # Subida del binario
    with open(video_path, "rb") as f:
        upload_resp = requests.put(
            upload_url,
            data=f,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{video_size-1}/{video_size}"
            }
        )
    
    if upload_resp.status_code != 200:
        raise Exception(f"Error subiendo el video: {upload_resp.text}")

    print(f"✅ TikTok video subido con éxito: {publish_id}")
    return publish_id
 
def log_telegram(mensaje: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    })
    print(mensaje)


def preguntar_plataformas() -> str:
    """Envía mensaje con botones y espera selección del usuario."""
    url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url_send, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "📤 *¿Dónde quieres publicar el video?*",
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "▶️ Solo YouTube", "callback_data": "solo_youtube"},
                {"text": "▶️ YouTube + TikTok", "callback_data": "youtube_tiktok"}
            ]]
        }
    })

    url_updates = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    offset = None
    print("Esperando respuesta del usuario en Telegram...")

    while True:
        params = {"timeout": 30, "allowed_updates": ["callback_query"]}
        if offset:
            params["offset"] = offset
        resp = requests.get(url_updates, params=params).json()
        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            if "callback_query" in update:
                respuesta = update["callback_query"]["data"]
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": update["callback_query"]["id"], "text": "✅ Recibido"}
                )
                return respuesta


def pedir_token_tiktok() -> str:
    """
    Pide al usuario que envíe el access token de TikTok por Telegram.
    Se usa porque el token vence cada 24h y no se puede guardar en env.
    """
    url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url_send, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": (
            "🔑 *Token de TikTok requerido*\n\n"
            "El token vence cada 24h\\. Por favor envía tu `access_token` "
            "de TikTok ahora mismo como mensaje de texto\\."
        ),
        "parse_mode": "MarkdownV2"
    })

    url_updates = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    offset = None
    print("Esperando token de TikTok del usuario en Telegram...")

    while True:
        params = {"timeout": 60, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        resp = requests.get(url_updates, params=params).json()
        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            if "message" in update and "text" in update["message"]:
                token = update["message"]["text"].strip()
                # Confirmación
                requests.post(url_send, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": "✅ Token recibido, continuando con TikTok...",
                    "parse_mode": "Markdown"
                })
                return token

def obtener_access_token_tiktok():
    access_token = os.environ.get("TIKTOK_ACCESS_TOKEN")
    
    # 1. Validación
    if access_token:
        url_info = "https://open.tiktokapis.com/v2/user/info/"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"fields": "display_name"}
        resp = requests.get(url_info, headers=headers, params=params)
        
        if resp.status_code == 200:
            return access_token

    # 2. Refresco
    log_telegram("🔄 *TikTok*: Token expirado o inexistente. Refrescando...")
    
    response = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"]
        }
    )
    
    data = response.json()
    if "access_token" not in data:
        log_telegram(f"❌ *TikTok ERROR*: {data}")
        raise Exception("Error obteniendo token")
    
    nuevo_token = data["access_token"]
    log_telegram(f"✅ *Nuevo token generado y verificado:*\n`{nuevo_token}`")
    
    return nuevo_token


def main():
    with open("movie_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    modo    = int(data["modo"])
    generos = data["generos"]
    es_video = False

    log_telegram(f"🚀 *Iniciando proceso*\n📽️ Título: *{data['titulo']}* ({data['año']})\n⚙️ Modo: {modo}")

    # ── Pregunta de plataformas ──────────────────────────────────
    destino = preguntar_plataformas()
    log_telegram(f"📌 Destino seleccionado: *{'Solo YouTube' if destino == 'solo_youtube' else 'YouTube + TikTok'}*")

    # ── Si es TikTok, pedir token inmediatamente ─────────────────
    tiktok_token = None
    if destino == "youtube_tiktok":
        tiktok_token = obtener_access_token_tiktok()
        log_telegram("🔑 Token de TikTok recibido correctamente")

    if modo == 1:
        log_telegram("📥 Descargando archivo desde Google Drive...")
        ruta_media, ext = descargar_desde_drive()
        es_video = ext in [".mp4", ".mov", ".avi", ".mkv"]
        log_telegram(f"✅ Archivo descargado: `{ext}` — {'Video' if es_video else 'Imagen'}")
    elif modo == 2:
        log_telegram("📥 Descargando video desde Google Drive...")
        ruta_media, ext = descargar_desde_drive()
        es_video = True
        log_telegram(f"✅ Video descargado: `{ext}`")
    elif modo == 3:
        log_telegram(f"🔍 Buscando imagen con Gemini para *{data['titulo']}*...")
        ruta_media = buscar_imagen_gemini(data["titulo"])
        es_video = False
        log_telegram("✅ Imagen obtenida desde Gemini")

    log_telegram("🎬 Construyendo video con FFmpeg...")
    video_path = construir_video(data, ruta_media, es_video)
    log_telegram("✅ Video construido correctamente")

    if not es_video:
        log_telegram("🎵 Agregando música de fondo...")
        video_path = agregar_audio(video_path, generos)
        log_telegram("✅ Audio agregado")

    log_telegram("🤖 Generando metadata con Claude...")
    metadata = generar_metadata(data)
    log_telegram(f"✅ Metadata YouTube generada\n📝 Título YT: *{metadata['titulo_yt']}*")

    # ── Metadata TikTok si aplica ────────────────────────────────
    metadata_tiktok = None
    if destino == "youtube_tiktok":
        log_telegram("🤖 Generando metadata optimizada para TikTok...")
        metadata_tiktok = generar_metadata_tiktok(data)
        log_telegram(f"✅ Metadata TikTok generada\n📝 Título TikTok: `{metadata_tiktok['titulo_tiktok']}`")

    log_telegram("📤 Subiendo video a YouTube...")
    video_id_youtube, yt_service = subir_a_youtube(video_path, metadata)
    log_telegram(f"✅ Video publicado en YouTube\n🔗 https://youtube.com/watch?v={video_id_youtube}")

    log_telegram("💬 Publicando comentario informativo en YouTube...")
    publicar_comentario_youtube(yt_service, video_id_youtube, data)
    log_telegram("✅ Comentario publicado en YouTube")

    if destino == "youtube_tiktok":
        log_telegram("📤 Subiendo y publicando video en TikTok...")
        publish_id = subir_a_tiktok(video_path, metadata_tiktok, tiktok_token)
        log_telegram(f"✅ Video publicado en TikTok\n🆔 publish\\_id: `{publish_id}`")

    log_telegram("📨 Enviando resumen final a Telegram...")
    mandar_a_telegram(video_path, data, metadata, video_id=video_id_youtube)

    log_telegram("🏁 *Proceso completado exitosamente* ✅")


if __name__ == "__main__":
    main()