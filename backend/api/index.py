import sys
from pathlib import Path

# server.py y pg_compat.py viven en backend/, un nivel arriba de este archivo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import InnovatePitchHandler, initialize_database

# Vercel's Python runtime invokes BaseHTTPRequestHandler directly.
# Initialization is idempotent: it never deletes existing production data.
initialize_database()

class handler(InnovatePitchHandler):
    pass
