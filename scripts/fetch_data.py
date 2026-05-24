import os
import json
import requests
import anthropic

# ─── Variables de entorno ────────────────────────────────────────
TMDB_API_KEY  = os.environ["TMDB_API_KEY"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
MOVIE_ID      = os.environ["MOVIE_ID"]
MEDIA_TYPE    = os.environ["MEDIA_TYPE"]  # movie o tv
MODO          = os.environ["MODO"]        # 1, 2 o 3

# ─── Cliente Claude ──────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

def traducir(texto: str) -> str:
    msg = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"Traduce este texto al español de forma natural. Solo devuelve la traducción, sin explicaciones:\n\n{texto}"
        }]
    )
    return msg.content[0].text.strip()

def obtener_datos_pelicula():
    endpoint = "movie" if MEDIA_TYPE == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{MOVIE_ID}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "watch/providers,genres"
    }
    r = requests.get(url, params=params)
    return r.json()

def obtener_plataformas(data: dict) -> dict:
    providers = data.get("watch/providers", {}).get("results", {})
    # Intentar US primero, luego cualquier país disponible
    pais = providers.get("US") or next(iter(providers.values()), {})
    
    plataformas_pago = []
    plataformas_gr