# api package init
# Fallback export so Vercel function discovery finds an ASGI app if scanned
try:
    from api.main import app
except Exception:
    pass
