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

  # ── Detectar resolución y calcular alturas ───────────────────
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

    # Poster ocupa toda la altura de sección 3
    alto_poster  = h_sec3 - 12
    ancho_poster = int(alto_poster * 0.67)
    y_poster     = y_sec3 + 8
    x_datos      = ancho_poster + 25

    # Distribuir datos ocupando todo el alto de sección 3
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

    # ── Colores aleatorios ───────────────────────────────────────
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

    # ── Descargar poster ─────────────────────────────────────────
    poster_path = "/tmp/poster.jpg"
    if poster_url:
        r = requests.get(poster_url)
        with open(poster_path, "wb") as f:
            f.write(r.content)

    # ── Sinopsis justificada ─────────────────────────────────────
    salida   = "/tmp/output_video.mp4"
    duracion = random.randint(30, 60) if not es_video else None

    # Calcular fuente y lineas para ocupar todo el espacio disponible
    espacio_sinopsis = h_sec1 - 70
    ancho_texto      = 1020  # 1080 - 60px de margen
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

    # [0] = media, [1] = poster
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


def mandar_a_telegram(video_path: str, data: dict, metadata: dict):
    plataformas_pago   = ", ".join(data["plataformas"]["pago"]) or "No disponible"
    plataformas_gratis = ", ".join(data["plataformas"]["gratis"]) or "No disponible"

    url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    mensaje = (
        f"🎬 *{data['titulo']}* ({data['año']})\n\n"
        f"📺 *Donde ver:*\n"
        f"💰 Pago: {plataformas_pago}\n"
        f"🆓 Gratis: {plataformas_gratis}\n\n"
        f"📝 *YouTube:* {metadata['titulo_yt']}"
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
            "caption": f"✅ *{data['titulo']}* — Listo para YouTube",
            "parse_mode": "Markdown"
        }, files={"video": video})

    print("Video y plataformas enviados a Telegram")


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
    return response["id"]


def subir_a_tiktok(video_path: str, metadata: dict):
    access_token  = os.environ["TIKTOK_ACCESS_TOKEN"]
    video_size    = os.path.getsize(video_path)

    # Paso 1: Iniciar upload
    init_data = json.dumps({
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1
        }
    }).encode()

    init_req = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
        data=init_data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    )
    init_resp = init_req.json()
    upload_url = init_resp["data"]["upload_url"]
    publish_id = init_resp["data"]["publish_id"]
    print(f"TikTok upload iniciado: {publish_id}")

    # Paso 2: Subir video
    with open(video_path, "rb") as f:
        video_data = f.read()

    requests.put(
        upload_url,
        data=video_data,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size-1}/{video_size}"
        }
    )
    print(f"TikTok video subido: {publish_id}")

def main():
    with open("movie_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    modo     = int(data["modo"])
    generos  = data["generos"]
    es_video = False

    print(f"Procesando: {data['titulo']} — Modo {modo}")

    if modo == 1:
        ruta_media, ext = descargar_desde_drive()
        es_video = ext in [".mp4", ".mov", ".avi", ".mkv"]
    elif modo == 2:
        ruta_media, ext = descargar_desde_drive()
        es_video = True
    elif modo == 3:
        ruta_media = buscar_imagen_gemini(data["titulo"])
        es_video = False

    video_path = construir_video(data, ruta_media, es_video)

    if not es_video:
        video_path = agregar_audio(video_path, generos)

    metadata = generar_metadata(data)
    print(f"Titulo YT: {metadata['titulo_yt']}")

    mandar_a_telegram(video_path, data, metadata)

    subir_a_youtube(video_path, metadata)
    print("Subiendo a TikTok despues de YouTube...")
    # subir_a_tiktok(video_path, metadata)
    print("Video subido a TikTok exitosamente")


if __name__ == "__main__":
    main()