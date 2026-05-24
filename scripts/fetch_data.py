import os
import json
import requests
import anthropic

# ─── Variables de entorno ────────────────────────────────────────
TMDB_API_KEY   = os.environ["TMDB_API_KEY"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
MOVIE_ID       = os.environ["MOVIE_ID"]
MEDIA_TYPE     = os.environ["MEDIA_TYPE"]
MODO           = os.environ["MODO"]

print(f"Iniciando fetch_data.py...")
print(f"MOVIE_ID: {MOVIE_ID}")
print(f"MEDIA_TYPE: {MEDIA_TYPE}")
print(f"MODO: {MODO}")

# ─── Cliente Claude ──────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

def traducir(texto: str) -> str:
    print("Traduciendo sinopsis...")
    msg = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"Traduce este texto al español. Si ya está en español devuélvelo tal cual sin comentarios ni explicaciones. Solo devuelve el texto final:\n\n{texto}"
        }]
    )
    return msg.content[0].text.strip()

def obtener_datos_pelicula():
    print("Consultando TheMovieDB...")
    endpoint = "movie" if MEDIA_TYPE == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{MOVIE_ID}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "watch/providers,genres"
    }
    r = requests.get(url, params=params)
    print(f"Status TMDB: {r.status_code}")
    return r.json()

def obtener_plataformas(data: dict) -> dict:
    providers = data.get("watch/providers", {}).get("results", {})
    pais = providers.get("US") or next(iter(providers.values()), {})
    plataformas_pago = []
    plataformas_gratis = []
    for p in pais.get("flatrate", []):
        plataformas_pago.append(p["provider_name"])
    for p in pais.get("free", []):
        plataformas_gratis.append(p["provider_name"])
    for p in pais.get("ads", []):
        plataformas_gratis.append(p["provider_name"])
    return {"pago": plataformas_pago, "gratis": plataformas_gratis}

def main():
    data = obtener_datos_pelicula()

    titulo = data.get("title") or data.get("name", "Sin título")
    sinopsis_en = data.get("overview", "")
    sinopsis_es = traducir(sinopsis_en) if sinopsis_en else "Sin sinopsis disponible"
    año = (data.get("release_date") or data.get("first_air_date", ""))[:4]
    generos = [g["name"] for g in data.get("genres", [])]
    paises = data.get("production_countries") or data.get("origin_country", [])
    pais_origen = ""
    if paises:
        pais_origen = paises[0] if isinstance(paises[0], str) else paises[0].get("name", "")
    tipo = "Película" if MEDIA_TYPE == "movie" else "Serie"
    poster = data.get("poster_path", "")
    poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else ""
    plataformas = obtener_plataformas(data)

    resultado = {
        "movie_id": MOVIE_ID,
        "media_type": MEDIA_TYPE,
        "modo": MODO,
        "titulo": titulo,
        "sinopsis": sinopsis_es,
        "año": año,
        "generos": generos,
        "pais": pais_origen,
        "tipo": tipo,
        "poster_url": poster_url,
        "plataformas": plataformas
    }

    with open("movie_data.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(f"✅ Datos guardados: {titulo} ({año})")
    print(f"   Géneros: {', '.join(generos)}")
    print(f"   Plataformas pago: {', '.join(plataformas['pago'])}")
    print(f"   Plataformas gratis: {', '.join(plataformas['gratis'])}")

if __name__ == "__main__":
    main()