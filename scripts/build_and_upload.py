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
import google.generativeai as genai
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
genai.configure(api_key=GEMINI_API_KEY)

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

def descargar_desde_drive():
    resultados = drive_service.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name, mimeType)"
    ).execute()
    archivos = resultados.get("files", [])
    if not archivos:
        raise Exception("No hay archivos en la carpeta de Drive")
    archivo = archivos[0]
    file_id = archivo["id"]
    nombre = archivo["name"]
    ext = Path(nombre).suffix.lower()
    print(f"Descargando {nombre} de Drive...")
    request = drive_service.files().get_media(fileId=file_id)
    ruta_local = f"/tmp/media{ext}"
    with open(ruta_local, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return ruta_local, ext

def buscar_imagen_gemini(titulo: str) -> str:
    import urllib.request
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(
        f"Give me a direct image URL (jpg or png) related to the movie or TV show '{titulo}'. "
        f"Return only the URL, nothing else."
    )
    url = response.text.strip()
    ruta = "/tmp/gemini_image.jpg"
    urllib.request.urlretrieve(url, ruta)
    return ruta

def construir_video(data: dict, ruta_media: str, es_video: bool) -> str:
    sinopsis  = data["sinopsis"][:200]
    generos   = ", ".join(data["generos"][:3])
    año       = data["año"]
    pais      = data["pais"]
    tipo      = data["tipo"]
    poster_url = data["poster_url"]
    plataformas_pago   = ", ".join(data["plataformas"]["pago"][:3]) or "No disponible"
    plataformas_gratis = ", ".join(data["plataformas"]["gratis"][:3]) or "No disponible"

    # Descargar poster
    poster_path = "/tmp/poster.jpg"
    if poster_url:
        r = requests.get(poster_url)
        with open(poster_path, "wb") as f:
            f.write(r.content)

    salida = "/tmp/output_video.mp4"

    # Duración aleatoria entre 30 y 60 segundos solo para imagen
    duracion = random.randint(30, 60) if not es_video else None

    filtros = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2[bg];"
        f"[1:v]scale=180:270[poster];"
        f"[bg][poster]overlay=20:1600[v];"
        f"[v]drawtext=text='{sinopsis}':fontsize=28:fontcolor=white:x=20:y=20:w=1040:line_spacing=8[v2];"
        f"[v2]drawtext=text='Géneros\\: {generos}':fontsize=24:fontcolor=white:x=220:y=1610[v3];"
        f"[v3]drawtext=text='País\\: {pais} | Tipo\\: {tipo} | Año\\: {año}':fontsize=22:fontcolor=white:x=220:y=1650[v4];"
        f"[v4]drawtext=text='Ver en\\: {plataformas_pago}':fontsize=20:fontcolor=yellow:x=220:y=1700[v5];"
        f"[v5]drawtext=text='Gratis en\\: {plataformas_gratis}':fontsize=20:fontcolor=lightgreen:x=220:y=1730[out]"
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
        "animation": "animacion", "fantasy": "fantasia"
    }
    categoria = mapeo.get(genero_principal, "accion")
    audio_dir = Path("audio")
    audios = list(audio_dir.glob(f"{categoria}*.mp3"))
    if not audios:
        audios = list(audio_dir.glob("*.mp3"))
    if not audios:
        print("No se encontró audio, continuando sin música")
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
    titulo   = data["titulo"]
    sinopsis = data["sinopsis"]
    generos  = ", ".join(data["generos"])
    plataformas_pago   = ", ".join(data["plataformas"]["pago"][:3])
    plataformas_gratis = ", ".join(data["plataformas"]["gratis"][:3])
    msg = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Genera metadata para un YouTube Short recomendando esta película/serie.
Título: {titulo}
Sinopsis: {sinopsis}
Géneros: {generos}
Disponible en: {plataformas_pago}
Gratis en: {plataformas_gratis}

Responde SOLO en este formato JSON exacto:
{{
  "titulo_yt": "título llamativo para YouTube Shorts máximo 60 caracteres incluyendo emoji",
  "descripcion": "descripción corta máximo 200 caracteres recomendando la peli mencionando plataformas",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "Shorts", "peliculas", "series"]
}}"""
        }]
    )
    texto = msg.content[0].text.strip()
    texto = texto.replace("```json", "").replace("```", "").strip()
    return json.loads(texto)

def mandar_a_telegram(video_path: str, data: dict, metadata: dict):
    """Manda el video final a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    caption = (
        f"✅ *{data['titulo']}* ({data['año']})\n\n"
        f"📝 {metadata['titulo_yt']}\n\n"
        f"🎬 Listo para subir a YouTube"
    )
    with open(video_path, "rb") as video:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "Markdown"
        }, files={"video": video})
    print("✅ Video enviado a Telegram")

# def subir_a_youtube(video_path: str, metadata: dict):
#     """Sube el video a YouTube."""
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
#     print(f"✅ Video subido: https://youtube.com/watch?v={response['id']}")
#     return response["id"]

def main():
    with open("movie_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    modo = int(data["modo"])
    generos = data["generos"]
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
    print(f"Título YT: {metadata['titulo_yt']}")

    mandar_a_telegram(video_path, data, metadata)

    # subir_a_youtube(video_path, metadata)

if __name__ == "__main__":
    main()