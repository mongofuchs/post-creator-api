import hmac
import logging
import os
import re
from typing import Any

from flask import Flask, jsonify, request, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from generator import (
    GenerationError,
    generate_social_post,
    get_default_model,
    get_text_provider,
)
from image_service import register_image_routes
from media import search_batch
from research import fetch_google_trends

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)

# Flask-App initialisieren
app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120 per hour"],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)


@app.before_request
def protect_api():
    if not request.path.startswith("/api/") or request.method == "OPTIONS":
        return None

    access_code = os.environ.get("APP_ACCESS_CODE", "").strip()
    if not access_code:
        if get_text_provider() == "groq":
            return jsonify({"error": "Der Server-Zugangscode ist nicht eingerichtet."}), 503
        return None
    if get_text_provider() == "groq" and len(access_code) < 32:
        return jsonify({"error": "Der Server-Zugangscode muss mindestens 32 Zeichen lang sein."}), 503

    provided_code = request.headers.get("X-App-Access-Code", "")
    if not hmac.compare_digest(provided_code.encode("utf-8"), access_code.encode("utf-8")):
        return jsonify({"error": "Zugangscode fehlt oder ist falsch."}), 401
    return None


@app.errorhandler(429)
def handle_rate_limit(error: Any):
    return jsonify({"error": "Zu viele Anfragen. Bitte später erneut versuchen."}), 429


@app.after_request
def add_mobile_cors_headers(response: Any) -> Any:
    origin = request.headers.get("Origin", "")
    if origin in {"http://localhost", "https://localhost", "capacitor://localhost"}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-App-Access-Code"
        response.headers["Vary"] = "Origin"
    return response


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})


# Bild-Routen aus dem image_service registrieren
register_image_routes(app)

@app.route("/", methods=["GET"])
def index():
    """Lädt das Web-Frontend."""
    return render_template("index.html")

@app.route("/api/status", methods=["GET"])
def api_status():
    model = get_default_model()
    provider = get_text_provider()
    if provider == "groq" and not os.environ.get("GROQ_API_KEY", "").strip():
        return jsonify({"error": "Der Groq-API-Schlüssel ist auf dem Server nicht eingerichtet."}), 503
    if provider not in {"groq", "ollama"}:
        return jsonify({"error": "Der Textanbieter auf dem Server ist nicht unterstützt."}), 503
    return jsonify(
        {
            "status": "running",
            "model": model,
            "text_model": model,
            "models": [model],
            "provider": provider,
            "requires_access_code": bool(os.environ.get("APP_ACCESS_CODE", "").strip()),
        }
    )


@app.route("/api/trends", methods=["GET"])
def api_trends():
    try:
        trends = fetch_google_trends()
    except Exception as exc:
        app.logger.warning("Google Trends konnten nicht geladen werden: %s", exc)
        return jsonify({"error": "Trends konnten nicht geladen werden."}), 502
    return jsonify([item["title"] for item in trends])


def _format_generated_topic(topic: str, result: dict[str, Any], model: str) -> dict[str, Any]:
    content = result["content"]
    hashtags = re.findall(r"(?<!\w)#([\wäöüÄÖÜß]+)", content)
    return {
        "topic": topic,
        "model": model,
        "ideas": [
            {
                "title": topic,
                "platform": result["platform"],
                "angle": "Social-Media-Post",
                "hook": "",
                "text": content,
                "hashtags": hashtags,
                "facts": [],
                "source_url": [],
                "media_keywords": [topic],
            }
        ],
        "research": {"items": []},
    }

@app.route("/api/generate", methods=["POST"])
@limiter.limit("10 per hour")
def api_generate():
    if not request.is_json:
        return jsonify({"error": "Content-Type muss application/json sein"}), 400

    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Die Anfrage muss ein JSON-Objekt enthalten."}), 400

    platform = data.get("platform", "linkedin")
    extra_wish = data.get("extra_wish", "")
    model = data.get("model") or get_default_model()
    if not isinstance(extra_wish, str) or not isinstance(model, str):
        return jsonify({"error": "Ungültige Modell- oder Wunschangabe."}), 400
    if not isinstance(platform, str) or platform not in {"linkedin", "instagram", "twitter"}:
        return jsonify({"error": "Die Plattform wird nicht unterstützt."}), 400

    try:
        age_days = int(data.get("age_days", 14))
    except (ValueError, TypeError):
        age_days = 14

    topics = data.get("topics")
    if topics is None:
        topic = data.get("topic", "Lokale KI-Entwicklung")
        if not isinstance(topic, str) or not topic.strip():
            return jsonify({"error": "Bitte ein Thema angeben."}), 400
        topics = [topic.strip()]
        legacy_response = True
    else:
        if not isinstance(topics, list) or not topics or any(
            not isinstance(topic, str) or not topic.strip() for topic in topics
        ):
            return jsonify({"error": "Die Themen müssen als nicht leere Textliste gesendet werden."}), 400
        topics = [topic.strip() for topic in topics]
        legacy_response = False
    if len(topics) > 5 or any(len(topic) > 300 for topic in topics) or len(extra_wish) > 1000:
        return jsonify({"error": "Maximal 5 Themen mit je 300 Zeichen und 1000 Zeichen Zusatzwunsch."}), 400
    if model != get_default_model():
        return jsonify({"error": "Das angeforderte Modell ist auf diesem Server nicht freigeschaltet."}), 400

    try:
        results = []
        for topic in topics:
            app.logger.info("Generiere %s-Post für '%s'...", platform.upper(), topic)
            result = generate_social_post(
                topic=topic,
                platform=platform,
                age_days=age_days,
                extra_wish=extra_wish,
                model=model,
            )
            if legacy_response:
                return jsonify(result)
            results.append(_format_generated_topic(topic, result, model))
        return jsonify(results)

    except GenerationError as gen_exc:
        app.logger.error(f"Generierungsfehler: {gen_exc}")
        return jsonify({"error": str(gen_exc)}), 503
        
    except Exception as exc:
        app.logger.error(f"Serverfehler: {exc}")
        return jsonify({"error": "Ein interner Fehler ist aufgetreten."}), 500


@app.route("/api/media", methods=["POST"])
def api_media():
    if not request.is_json:
        return jsonify({"error": "Content-Type muss application/json sein."}), 400
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Die Anfrage muss ein JSON-Objekt enthalten."}), 400
    keywords = data.get("keywords", [])
    topic = data.get("topic", "")
    if not isinstance(keywords, list) or any(not isinstance(item, str) for item in keywords):
        return jsonify({"error": "Suchbegriffe müssen als Textliste gesendet werden."}), 400
    if not isinstance(topic, str):
        return jsonify({"error": "Das Thema muss Text sein."}), 400
    try:
        media = search_batch(
            keywords,
            image_queries=keywords,
            video_queries=keywords,
            topic=topic,
        )
    except Exception as exc:
        app.logger.exception("Medien-Suche fehlgeschlagen: %s", exc)
        return jsonify({"error": "Medien konnten nicht gesucht werden."}), 502
    return jsonify({"media": media})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)