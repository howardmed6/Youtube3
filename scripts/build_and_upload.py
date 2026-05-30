import os
import json
import random
import requests
import anthropic
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google import genai
import io
import textwrap

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
        '"': '', '\\': '', '[': '', ']': '', '&': 'y',
    }
    for k, v in reemplazos.items():
        texto = texto.replace(k, v)
    return texto


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
    archivo   = archivos[0]
    file_id   = archivo["id"]
    nombre    = archivo["name"]
    ext       = Path(nombre).suffix.lower()
    print(f"Descargando {nombre} de Drive...")
    request   = drive_service.files().get_media(fileId=file_id)
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


def construir_frame(data: dict, ruta_media: str, es_video: bool) -> str:
    """Construye el frame PNG usando Pillow y devuelve la ruta."""

    sinopsis   = limpiar_texto(data["sinopsis"][:300])
    generos    = limpiar_texto(", ".join(data["generos"][:3]))
    año        = data["año"]
    pais       = limpiar_texto(data["pais"][:30])
    tipo       = limpiar_texto(data["tipo"])
    poster_url = data["poster_url"]

    # ── Colores aleatorios ───────────────────────────────────────
    paletas = [
        {"marco": (0, 255, 255),   "titulo": (0, 255, 255),   "fondo": (0, 26, 26)},
        {"marco": (255, 107, 0),   "titulo": (255, 107, 0),   "fondo": (26, 10, 0)},
        {"marco": (255, 0, 255),   "titulo": (255, 0, 255),   "fondo": (26, 0, 26)},
        {"marco": (0, 255, 136),   "titulo": (0, 255, 136),   "fondo": (0, 26, 10)},
        {"marco": (255, 215, 0),   "titulo": (255, 215, 0),   "fondo": (26, 20, 0)},
    ]
    paleta       = random.choice(paletas)
    color_marco  = paleta["marco"]
    color_titulo = paleta["titulo"]
    color_fondo  = paleta["fondo"]
    color_blanco = (255, 255, 255)
    grosor_marco = 4

    # ── Detectar resolución del media ────────────────────────────
    if es_video:
        ancho_orig, alto_orig = obtener_resolucion(ruta_media)
    else:
        with Image.open(ruta_media) as img_temp:
            ancho_orig, alto_orig = img_temp.size

    ratio         = alto_orig / ancho_orig
    altura_media  = int(1080 * ratio)
    altura_media  = max(500, min(1300, altura_media))

    # ── Dimensiones secciones ────────────────────────────────────
    W           = 1080
    H           = 1920
    y_sec1      = 0
    h_sec1      = 250
    y_sec2      = h_sec1
    h_sec2      = altura_media
    y_sec3      = y_sec2 + h_sec2
    h_sec3      = H - y_sec3

    print(f"Layout: sec1={h_sec1}px sec2={h_sec2}px sec3={h_sec3}px")

    # ── Canvas negro ─────────────────────────────────────────────
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    # ── Fuentes ──────────────────────────────────────────────────
    try:
        font_titulo_sec = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_sinopsis   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        font_datos_tit  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_datos      = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        font_titulo_sec = ImageFont.load_default()
        font_sinopsis   = ImageFont.load_default()
        font_datos_tit  = ImageFont.load_default()
        font_datos      = ImageFont.load_default()

    # ════════════════════════════════════════════════════════════
    # SECCIÓN 1 — SINOPSIS
    # ════════════════════════════════════════════════════════════
    draw.rectangle([0, y_sec1, W, y_sec1 + h_sec1], fill=color_fondo)
    draw.rectangle([0, y_sec1, W, y_sec1 + h_sec1], outline=color_marco, width=grosor_marco)
    draw.text((30, y_sec1 + 15), "Sinopsis", font=font_titulo_sec, fill=color_titulo)

    lineas_sin = textwrap.wrap(sinopsis, width=52)[:5]
    for i, linea in enumerate(lineas_sin):
        draw.text((30, y_sec1 + 65 + i * 34), linea, font=font_sinopsis, fill=color_blanco)

    # ════════════════════════════════════════════════════════════
    # SECCIÓN 2 — MEDIA
    # ════════════════════════════════════════════════════════════
    draw.rectangle([0, y_sec2, W, y_sec2 + h_sec2], outline=color_marco, width=grosor_marco)

    if not es_video:
        try:
            media_img = Image.open(ruta_media).convert("RGB")
            media_img = media_img.resize((W, h_sec2), Image.LANCZOS)
            canvas.paste(media_img, (0, y_sec2))
            draw.rectangle([0, y_sec2, W, y_sec2 + h_sec2], outline=color_marco, width=grosor_marco)
        except Exception as e:
            print(f"Error cargando imagen: {e}")

    # ════════════════════════════════════════════════════════════
    # SECCIÓN 3 — POSTER + DATOS
    # ════════════════════════════════════════════════════════════
    draw.rectangle([0, y_sec3, W, y_sec3 + h_sec3], fill=color_fondo)
    draw.rectangle([0, y_sec3, W, y_sec3 + h_sec3], outline=color_marco, width=grosor_marco)

    # Poster
    alto_poster  = min(h_sec3 - 20, 380)
    ancho_poster = int(alto_poster * 0.67)
    y_poster     = y_sec3 + (h_sec3 - alto_poster) // 2
    x_datos      = ancho_poster + 25

    if poster_url:
        try:
            r            = requests.get(poster_url)
            poster_img   = Image.open(io.BytesIO(r.content)).convert("RGB")
            poster_img   = poster_img.resize((ancho_poster, alto_poster), Image.LANCZOS)
            canvas.paste(poster_img, (10, y_poster))
        except Exception as e:
            print(f"Error cargando poster: {e}")

    # Datos
    y_cur = y_sec3 + 20
    draw.text((x_datos, y_cur), "Generos", font=font_datos_tit, fill=color_titulo)
    y_cur += 40
    draw.text((x_datos, y_cur), generos, font=font_datos, fill=color_blanco)
    y_cur += 40
    draw.text((x_datos, y_cur), pais, font=font_datos, fill=color_blanco)
    y_cur += 40
    draw.text((x_datos, y_cur), f"{tipo}  {año}", font=font_datos, fill=color_blanco)

    # Guardar frame
    frame_path = "/tmp/frame.png"
    canvas.save(frame_path)
    print(f"Frame guardado: {frame_path}")
    return frame_path


def construir_video(data: dict, ruta_media: str, es_video: bool) -> str:
    """Combina el frame PNG con el video/imagen usando FFmpeg simple."""
    frame_path = construir_frame(data, ruta_media, es_video)
    salida     = "/tmp/output_video.mp4"
    duracion   = random.randint(30, 60) if not es_video else None

    # Detectar altura de sección 2
    if es_video:
        ancho_orig, alto_orig = obtener_resolucion(ruta_media)
    else:
        with Image.open(ruta_media) as img_temp:
            ancho_orig, alto_orig = img_temp.size

    ratio        = alto_orig / ancho_orig
    altura_media = int(1080 * ratio)
    altura_media = max(500, min(1300, altura_media))
    y_sec2       = 250

    if es_video:
        cmd = [
            "ffmpeg", "-y",
            "-i", ruta_media,
            "-i", frame_path,
            "-filter_complex",
            f"[0:v]scale=1080:{altura_media}:force_original_aspect_ratio=decrease,"
            f"pad=1080:{altura_media}:(ow-iw)/2:(oh-ih)/2[vid];"
            f"[1:v]scale=1080:1920[frame];"
            f"[frame][vid]overlay=0:{y_sec2}[out]",
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            salida
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print("STDERR:", result.stderr[-3000:])
        if result.returncode != 0:
            raise Exception(f"FFmpeg failed: {result.stderr[-1000:]}")
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duracion), "-i", frame_path,
            "-c:v", "libx264",
            "-t", str(duracion),
            "-pix_fmt", "yuv420p",
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


# def subir_a_youtube(video_path: str, metadata: dict):
#     youtube = build("youtube", "v3", credentials=creds)
#     body = {
#         "snippet": {
#             "title": metadata["titulo_yt"],
#             "description": metadata["descripcion"],
#             "tags": metadata["tags"],
#             "categoryId": "24"
#         },
#         "status": {
#             "privacyStatus": "public",
#             "selfDeclaredMadeForKids": False
#         }
#     }
#     media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
#     request = youtube.videos().insert(
#         part="snippet,status",
#         body=body,
#         media_body=media
#     )
#     print("Subiendo a YouTube...")
#     response = None
#     while response is None:
#         status, response = request.next_chunk()
#         if status:
#             print(f"Subiendo... {int(status.progress() * 100)}%")
#     print(f"Video subido: https://youtube.com/watch?v={response['id']}")
#     return response["id"]


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

    # subir_a_youtube(video_path, metadata)


if __name__ == "__main__":
    main()