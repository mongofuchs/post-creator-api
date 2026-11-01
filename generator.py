import logging
import os
from typing import Dict, Any

import requests

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    """Eigene Exception für Fehler während der Post-Generierung."""
    pass


def get_text_provider() -> str:
    """Return the configured text-generation provider."""
    return os.environ.get("TEXT_PROVIDER", "ollama").strip().lower()


def get_default_model() -> str:
    """Return the model configured for the active text provider."""
    if get_text_provider() == "groq":
        return os.environ.get("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"
    return "qwen3.5:4b"


def generate_social_post(
    topic: str,
    platform: str = "linkedin",
    age_days: int = 14,
    extra_wish: str = "",
    model: str = "",
) -> Dict[str, Any]:
    """
    Generiert einen plattformspezifischen Social-Media-Post.
    Unterstützt: 'linkedin', 'instagram', 'twitter'
    """
    provider = get_text_provider()
    model = model or get_default_model()

    platform_instructions = {
        "linkedin": "Schreibe im professionellen, strukturierten Ton mit Absätzen und passenden Emojis. Füge am Ende 3-5 relevante Hashtags hinzu.",
        "instagram": "Schreibe locker und ansprechend. Nutze reichlich Emojis, gliedere mit Aufzählungspunkten und füge am Ende 10 relevante Hashtags an.",
        "twitter": "Schreibe einen prägnanten Tweet mit maximal 280 Zeichen, auf den Punkt gebracht, mit 2 passenden Hashtags."
    }

    instruction = platform_instructions.get(platform.lower(), platform_instructions["linkedin"])

    prompt = (
        f"Erstelle einen ansprechenden Social-Media-Beitrag für die Plattform '{platform.upper()}' "
        f"zum Thema: '{topic}'.\n"
        f"Vorgaben für den Schreibstil: {instruction}\n"
        f"Zusätzliche Wünsche: {extra_wish or 'keine'}\n"
        f"Schreibe den Beitrag direkt, ohne Erklärungen."
    )

    try:
        if provider == "groq":
            api_key = os.environ.get("GROQ_API_KEY", "").strip()
            if not api_key:
                raise GenerationError("Der Groq-API-Schlüssel ist auf dem Server nicht eingerichtet.")
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Du schreibst hochwertige Social-Media-Beiträge auf Deutsch."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_completion_tokens": 500,
                    "include_reasoning": False,
                    "stream": False,
                },
                timeout=60,
            )
        elif provider == "ollama":
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "think": False,
                    "options": {"temperature": 0.7, "num_predict": 500},
                    "stream": False,
                },
                timeout=300,
            )
        else:
            raise GenerationError("Der Textanbieter auf dem Server ist nicht unterstützt.")

        response.raise_for_status()
        data = response.json()

        if provider == "groq":
            generated_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        else:
            generated_text = data.get("response", "").strip()

        if not generated_text:
            raise GenerationError("Der Textanbieter hat eine leere Antwort zurückgegeben.")

        logger.info("Beitrag über %s mit Modell %s generiert.", provider, model)
        return {
            "success": True,
            "platform": platform,
            "topic": topic,
            "age_days": age_days,
            "model": model,
            "content": generated_text
        }

    except GenerationError:
        raise
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response is not None else 0
        logger.error("%s API HTTP-Fehler (Status %s).", provider, status_code)
        if provider == "groq" and status_code == 401:
            raise GenerationError("Der Groq-API-Schlüssel ist ungültig.") from http_err
        if provider == "groq" and status_code == 429:
            raise GenerationError("Das Groq-Anfragelimit wurde erreicht. Bitte später erneut versuchen.") from http_err
        raise GenerationError(f"Der Textanbieter antwortete mit HTTP {status_code}.") from http_err
    except requests.exceptions.RequestException as req_err:
        logger.error("%s Verbindungsfehler: %s", provider, req_err)
        if provider == "groq":
            raise GenerationError("Groq ist nicht erreichbar oder die Anfrage hat das Zeitlimit überschritten.") from req_err
        raise GenerationError("Lokaler Ollama-Server nicht erreichbar oder Timeout.") from req_err
    except Exception as exc:
        logger.exception("Unerwarteter Fehler bei der Generierung über %s.", provider)
        raise GenerationError("Bei der Textgenerierung ist ein unerwarteter Fehler aufgetreten.") from exc