"""Tokens de unsubscribe (stateless, HMAC) e montagem da URL de descadastro."""

import hashlib
import hmac
import os
from urllib.parse import quote


def _secret():
    return (os.environ.get("SESSION_SECRET") or "dev-secret-change-me").encode("utf-8")


def make_token(email):
    email = (email or "").strip().lower()
    return hmac.new(_secret(), email.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def verify_token(email, token):
    expected = make_token(email)
    return hmac.compare_digest(expected, (token or "").strip())


def base_url():
    """URL pública usada nos links de email. Configure BASE_URL no .env em produção."""
    return (os.environ.get("BASE_URL") or f"http://localhost:{os.environ.get('PORT', '3000')}").rstrip("/")


def unsubscribe_url(email):
    email = (email or "").strip().lower()
    if not email:
        return ""
    return f"{base_url()}/unsubscribe?e={quote(email)}&t={make_token(email)}"
