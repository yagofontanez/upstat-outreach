"""Auto-aprovação com filtros de qualidade — substitui o passo manual de `review`
quando o pipeline roda sem humano no loop.

Um lead pendente só vira 'approved' se passar em TODAS as travas:
  - tem email com formato plausível (contém '@' e domínio com ponto)
  - NÃO está na lista de supressão do cliente (bounce/unsub/complaint)
  - tem hook personalizado (require_hook, default True) — garante que passou pelo Groq
  - score mínimo, se exigido (min_score) — usa o pain score do site_insights

Os de maior score são aprovados primeiro, respeitando o teto `limit`.
"""

from datetime import datetime, timezone

from state import is_suppressed


def _now():
    return datetime.now(timezone.utc).isoformat()


def _valid_email(email):
    email = (email or "").strip()
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1]
    return "." in domain and len(domain) >= 3


def eligible(client, lead, require_hook=True, min_score=None):
    """True se o lead pendente passa em todas as travas de auto-aprovação."""
    if lead.get("status") != "pending":
        return False
    if not _valid_email(lead.get("email")):
        return False
    if is_suppressed(client["id"], lead.get("email")):
        return False
    if require_hook and not lead.get("personalizedHook"):
        return False
    if min_score is not None and (lead.get("score") or 0) < min_score:
        return False
    return True


def auto_approve(client, leads, limit=None, require_hook=True, min_score=None, on_progress=None):
    """Marca pending → approved nos leads elegíveis (mutando os dicts in-place).

    Aprova os de maior score primeiro. Retorna a lista de leads aprovados nesta passada.
    NÃO persiste — quem chama deve dar state.save() depois.
    """
    candidates = [l for l in leads if eligible(client, l, require_hook, min_score)]
    candidates.sort(key=lambda l: l.get("score") or 0, reverse=True)
    if limit and limit > 0:
        candidates = candidates[:limit]
    for l in candidates:
        l["status"] = "approved"
        l["approvedAt"] = _now()
        l["approvedBy"] = "auto"
    if on_progress:
        on_progress(
            {
                "type": "log",
                "message": f"[{client['name']}] auto-aprovados: {len(candidates)}",
            }
        )
    return candidates
