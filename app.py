"""Entry point: ``python app.py`` locally, ``app:app`` for gunicorn / Vercel."""
import os

from saans import create_app
from saans.config import settings

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    mode = "ON" if settings.groq_enabled else "OFF - fallback mode"
    print(f"Saans running on http://localhost:{port}  (Groq {mode}, model {settings.groq_model})")
    app.run(host="0.0.0.0", port=port, debug=False)
