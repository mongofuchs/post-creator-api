import os
from flask import jsonify, request
from openai import OpenAI, OpenAIError

def register_image_routes(app):
    """Registriert den Endpunkt für die Bildgenerierung in deiner Flask-App."""
    
    @app.route("/api/generate-image", methods=["POST"])
    def generate_image_for_post():
        # Prüfen, ob der API-Key gesetzt ist
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return jsonify({
                "success": False, 
                "error": "OpenAI API Key ist nicht in den Umgebungsvariablen gesetzt ($env:OPENAI_API_KEY)."
            }), 500

        data = request.get_json()
        if not data:
            return jsonify({"error": "Ungültige oder fehlende JSON-Daten."}), 400
            
        post_text = data.get("text", "")
        
        if not post_text:
            return jsonify({"error": "Kein Post-Text übergeben."}), 400
            
        try:
            # Client innerhalb der Funktion initialisieren
            client = OpenAI(api_key=api_key)
            
            # 1. Prompt für den Bildgenerator aufbereiten
            prompt = (
                "Create a professional, modern, high-quality digital illustration "
                f"or social media graphic representing this topic: {post_text[:400]}"
            )
            
            # 2. Bild über DALL-E 3 generieren lassen
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            
            # 3. URL des generierten Bildes zurückgeben
            image_url = response.data[0].url
            return jsonify({
                "success": True, 
                "image_url": image_url,
                "prompt_used": prompt
            })
            
        except OpenAIError as oe:
            return jsonify({"success": False, "error": f"OpenAI Fehler: {str(oe)}"}), 500
        except Exception as e:
            return jsonify({"success": False, "error": f"Serverfehler: {str(e)}"}), 500