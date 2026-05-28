"""Search presets — listas salvas de term + cidades pra rodar scrape com 1 clique.

Cada preset é (label, term, lista de cidades, max_results), escopado por cliente.
Rodar um preset com 1 cidade equivale a um scrape normal; com N cidades, o
runner itera e acumula resultados (dedup por website).
"""

import json

from state import connection

SEEDS = {
    "upstat": [
        {
            "label": "agências de marketing — sul/sudeste",
            "term": "agência de marketing",
            "cities": ["São Paulo", "Rio de Janeiro", "Curitiba", "Belo Horizonte", "Porto Alegre"],
            "max_results": 30,
        },
        {
            "label": "estúdios de design — capitais",
            "term": "estúdio de design",
            "cities": ["São Paulo", "Curitiba", "Florianópolis"],
            "max_results": 30,
        },
        {
            "label": "criação de sites",
            "term": "criação de sites",
            "cities": ["São Paulo", "Belo Horizonte"],
            "max_results": 30,
        },
    ],
    "martinsadviser": [
        {
            "label": "trucking — TX hub",
            "term": "trucking company",
            "cities": ["Houston, TX", "Dallas, TX", "San Antonio, TX", "El Paso, TX"],
            "max_results": 30,
        },
        {
            "label": "trucking — midwest",
            "term": "trucking company",
            "cities": ["Chicago, IL", "Indianapolis, IN", "Columbus, OH", "Kansas City, MO"],
            "max_results": 30,
        },
        {
            "label": "permit services — southeast",
            "term": "permit service",
            "cities": ["Atlanta, GA", "Miami, FL", "Charlotte, NC", "Nashville, TN"],
            "max_results": 30,
        },
        {
            "label": "motor carriers — west",
            "term": "motor carrier",
            "cities": ["Phoenix, AZ", "Los Angeles, CA", "Denver, CO", "Salt Lake City, UT"],
            "max_results": 30,
        },
    ],
}


def _row_to_dict(row):
    if row is None:
        return None
    try:
        cities = json.loads(row["cities"])
    except Exception:
        cities = []
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "label": row["label"],
        "term": row["term"],
        "cities": cities,
        "max_results": row["max_results"],
        "created_at": row["created_at"],
    }


def list_presets(client_id):
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM search_presets WHERE client_id = ? ORDER BY created_at ASC",
            (client_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_preset(preset_id):
    with connection() as db:
        row = db.execute(
            "SELECT * FROM search_presets WHERE id = ?", (preset_id,)
        ).fetchone()
    return _row_to_dict(row)


def create_preset(client_id, label, term, cities, max_results=30):
    if not client_id or not label or not term:
        raise ValueError("client_id, label e term são obrigatórios")
    if isinstance(cities, str):
        cities = parse_cities(cities)
    cities = [c.strip() for c in cities if c and c.strip()]
    if not cities:
        raise ValueError("informe ao menos uma cidade")
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 30
    max_results = max(1, min(100, max_results))
    with connection() as db:
        cur = db.execute(
            """
            INSERT INTO search_presets (client_id, label, term, cities, max_results)
            VALUES (?, ?, ?, ?, ?)
            """,
            (client_id, label.strip(), term.strip(), json.dumps(cities, ensure_ascii=False), max_results),
        )
        db.commit()
        return get_preset(cur.lastrowid)


def delete_preset(preset_id):
    with connection() as db:
        db.execute("DELETE FROM search_presets WHERE id = ?", (preset_id,))
        db.commit()


def parse_cities(raw):
    """Aceita 'São Paulo, Curitiba' ou linhas separadas por newline. Retorna lista limpa."""
    if not raw:
        return []
    parts = []
    for line in str(raw).splitlines():
        for c in line.split(","):
            c = c.strip()
            if c:
                parts.append(c)
    return parts


def seed_defaults():
    """Insere presets default se o cliente ainda não tiver nenhum. Idempotente."""
    for client_id, items in SEEDS.items():
        existing = list_presets(client_id)
        if existing:
            continue
        for it in items:
            try:
                create_preset(client_id, it["label"], it["term"], it["cities"], it["max_results"])
            except Exception:
                pass
