"""Caminhos compartilhados. O app Python usa o MESMO banco e assets do projeto Node."""

from pathlib import Path

# python/ fica dentro da raiz do projeto; sobe um nível para achar o DB e o public/.
ROOT = Path(__file__).resolve().parent.parent
DB_FILE = ROOT / "outreach.sqlite"
LEGACY_FILE = ROOT / "leads.json"
PUBLIC_DIR = ROOT / "public"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
