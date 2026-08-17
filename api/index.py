from server import InnovatePitchHandler, initialize_database

# Vercel's Python runtime invokes BaseHTTPRequestHandler directly.
# Initialization is idempotent: it never deletes existing production data.
initialize_database()

class handler(InnovatePitchHandler):
    pass
