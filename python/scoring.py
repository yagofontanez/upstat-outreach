"""Lead scoring por sinais de dor.

Transforma os painSignals coletados pelo site_insights em um score 0-100.
A pontuação é enviesada para os problemas que o UpStat resolve: site fora do ar,
erros 5xx, lentidão e ausência de status page valem mais.
"""

from datetime import datetime, timezone

# Peso por sinal específico (quanto maior, mais "quente" o prospect para uptime).
KEY_WEIGHTS = {
    "home_unavailable": 40,
    "server_error": 30,
    "very_slow_home": 20,
    "no_status_page": 15,
    "slow_home": 10,
    "no_https": 10,
    "client_error": 8,
    "heavy_home": 6,
    "redirects": 3,
}

# Fallback por severidade, caso apareça um sinal novo sem peso definido.
SEVERITY_WEIGHTS = {"high": 20, "medium": 10, "low": 4}


def score_insights(insights):
    """Retorna (score:int 0-100, reasons:list[str]) a partir de um siteInsights."""
    if not insights:
        return 0, []

    signals = insights.get("painSignals") or []
    contributions = []
    for sig in signals:
        weight = KEY_WEIGHTS.get(sig.get("key"))
        if weight is None:
            weight = SEVERITY_WEIGHTS.get(sig.get("severity"), 4)
        contributions.append((weight, sig.get("label", sig.get("key", "?"))))

    total = min(100, sum(w for w, _ in contributions))
    reasons = [label for _, label in sorted(contributions, key=lambda c: -c[0])]
    return total, reasons


def apply_score(lead):
    """Calcula e grava score/scoreReasons/scoredAt no lead (mutando o dict). Retorna o score."""
    score, reasons = score_insights(lead.get("siteInsights"))
    lead["score"] = score
    lead["scoreReasons"] = reasons
    lead["scoredAt"] = datetime.now(timezone.utc).isoformat()
    return score


def score_label(score):
    """Rótulo curto pra UI."""
    if score >= 60:
        return "hot"
    if score >= 30:
        return "warm"
    if score > 0:
        return "cold"
    return "—"
