import os
import re
import json
import requests
import anthropic

# ─── Variables de entorno ────────────────────────────────────────
TMDB_API_KEY   = os.environ["TMDB_API_KEY"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
MEDIA_TYPE     = os.environ.get("MEDIA_TYPE", "movie")   # movie | tv
MODO           = os.environ.get("MODO", "resena")

# MOVIE_ID ahora es opcional: puede ser un ID, un enlace de TMDB o un nombre
MOVIE_INPUT    = os.environ.get("MOVIE_ID", "")          # renombramos internamente

print(f"Iniciando fetch_data.py...")
print(f"MOVIE_INPUT : {MOVIE_INPUT}")
print(f"MEDIA_TYPE  : {MEDIA_TYPE}")
print(f"MODO        : {MODO}")

# ─── Cliente Claude ──────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

# ════════════════════════════════════════════════════════════════
#  RESOLUCIÓN DEL INPUT → MOVIE_ID numérico
# ════════════════════════════════════════════════════════════════

def extraer_id_de_url(url: str) -> tuple[str | None, str | None]:
    """
    Soporta formatos:
      https://www.themoviedb.org/movie/660120-verdens-verste-menneske
      https://www.themoviedb.org/tv/1396-breaking-bad
    Devuelve (movie_id, media_type) o (None, None)
    """
    patron = r"themoviedb\.org/(movie|tv)/(\d+)"
    m = re.search(patron, url)
    if m:
        return m.group(2), m.group(1)
    return None, None


def buscar_por_nombre(nombre: str, media_type: str) -> str | None:
    """Busca en TMDB por nombre y devuelve el primer ID encontrado."""
    endpoint = "movie" if media_type == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/search/{endpoint}"
    params = {
        "api_key": TMDB_API_KEY,
        "query": nombre,
        "language": "en-US",
        "page": 1
    }
    r = requests.get(url, params=params)
    print(f"Status búsqueda por nombre: {r.status_code}")
    results = r.json().get("results", [])
    if results:
        encontrado = results[0]
        titulo = encontrado.get("title") or encontrado.get("name", "?")
        print(f"   Encontrado: {titulo} (ID: {encontrado['id']})")
        return str(encontrado["id"])
    print("   No se encontró ningún resultado.")
    return None


def resolver_movie_id(raw_input: str, media_type: str) -> tuple[str, str]:
    """
    Acepta:
      1. ID numérico puro              → "660120"
      2. Enlace de TMDB               → "https://www.themoviedb.org/movie/660120-..."
      3. Nombre / título de búsqueda  → "Verdens verste menneske"

    Devuelve (movie_id, media_type_resuelto)
    """
    raw = raw_input.strip()

    # ── Caso 1: ID numérico puro ──────────────────────────────────
    if raw.isdigit():
        print(f"Input reconocido como ID numérico: {raw}")
        return raw, media_type

    # ── Caso 2: URL de TMDB ───────────────────────────────────────
    if "themoviedb.org" in raw:
        movie_id, tipo = extraer_id_de_url(raw)
        if movie_id:
            print(f"Input reconocido como URL TMDB → ID: {movie_id}, tipo: {tipo}")
            return movie_id, tipo or media_type
        print("URL detectada pero no se pudo extraer el ID, intentando como nombre...")

    # ── Caso 3: Nombre / búsqueda ─────────────────────────────────
    print(f"Input reconocido como nombre de búsqueda: '{raw}'")
    movie_id = buscar_por_nombre(raw, media_type)
    if movie_id:
        return movie_id, media_type

    raise ValueError(f"No se pudo resolver el input '{raw_input}' a un ID de TMDB.")


# ════════════════════════════════════════════════════════════════
#  TRADUCCIÓN CON CLAUDE
# ════════════════════════════════════════════════════════════════

def traducir(texto: str) -> str:
    print("Traduciendo sinopsis...")
    msg = claude.messages.create(
        model="claude-sonnet-4-6",
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


# ════════════════════════════════════════════════════════════════
#  OBTENCIÓN DE DATOS TMDB
# ════════════════════════════════════════════════════════════════

def obtener_datos_pelicula(movie_id: str, media_type: str) -> dict:
    print("Consultando TheMovieDB...")
    endpoint = "movie" if media_type == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{movie_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "watch/providers,genres,translations"
    }
    r = requests.get(url, params=params)
    print(f"Status TMDB: {r.status_code}")
    data = r.json()

    # ── Fallback de sinopsis si el inglés viene vacío ─────────────
    if not data.get("overview", "").strip():
        print("Overview en inglés vacío, buscando en traducciones...")
        traducciones = data.get("translations", {}).get("translations", [])
        for lang in ["es", "nb", "no", "fr", "de", "pt"]:
            for t in traducciones:
                if t.get("iso_639_1") == lang and t.get("data", {}).get("overview"):
                    data["overview"] = t["data"]["overview"]
                    print(f"   Overview encontrado en idioma: {lang}")
                    break
            if data.get("overview", "").strip():
                break

    return data


def obtener_plataformas(data: dict) -> dict:
    providers = data.get("watch/providers", {}).get("results", {})
    pais = providers.get("US") or next(iter(providers.values()), {})
    plataformas_pago   = [p["provider_name"] for p in pais.get("flatrate", [])]
    plataformas_gratis = [p["provider_name"] for p in pais.get("free", [])]
    plataformas_gratis += [p["provider_name"] for p in pais.get("ads", [])]
    return {"pago": plataformas_pago, "gratis": plataformas_gratis}


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main():
    # 1. Resolver el input a un ID numérico
    movie_id, media_type = resolver_movie_id(MOVIE_INPUT, MEDIA_TYPE)

    # 2. Obtener datos de TMDB
    data = obtener_datos_pelicula(movie_id, media_type)

    # 3. Construir resultado
    titulo      = data.get("title") or data.get("name", "Sin título")
    sinopsis_en = data.get("overview", "")
    sinopsis_es = traducir(sinopsis_en) if sinopsis_en else "Sin sinopsis disponible"
    año         = (data.get("release_date") or data.get("first_air_date", ""))[:4]
    generos     = [g["name"] for g in data.get("genres", [])]
    paises      = data.get("production_countries") or data.get("origin_country", [])
    pais_origen = ""
    if paises:
        pais_origen = paises[0] if isinstance(paises[0], str) else paises[0].get("name", "")
    tipo        = "Película" if media_type == "movie" else "Serie"
    poster      = data.get("poster_path", "")
    poster_url  = f"https://image.tmdb.org/t/p/w500{poster}" if poster else ""
    plataformas = obtener_plataformas(data)

    resultado = {
        "movie_id"   : movie_id,
        "media_type" : media_type,
        "modo"       : MODO,
        "titulo"     : titulo,
        "sinopsis"   : sinopsis_es,
        "año"        : año,
        "generos"    : generos,
        "pais"       : pais_origen,
        "tipo"       : tipo,
        "poster_url" : poster_url,
        "plataformas": plataformas
    }

    with open("movie_data.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Datos guardados: {titulo} ({año})")
    print(f"   Géneros        : {', '.join(generos)}")
    print(f"   Plataformas pago  : {', '.join(plataformas['pago']) or 'ninguna'}")
    print(f"   Plataformas gratis: {', '.join(plataformas['gratis']) or 'ninguna'}")


if __name__ == "__main__":
    main()