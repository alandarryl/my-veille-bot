import json
import os
import logging
from typing import List
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from veille.models import Article, ScoredArticle

logger = logging.getLogger(__name__)


class BatchScoreResult(BaseModel):
    results: List[ScoredArticle] = Field(..., description="Liste des articles notés")


def get_llm_provider() -> str:
    return os.getenv("DEFAULT_LLM_PROVIDER", "gemini").lower()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def score_articles_gemini(articles: List[Article], criteria: str) -> List[ScoredArticle]:
    """Score un lot d'articles en utilisant la bibliothèque google-genai."""
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY absente.")
        return []

    client = genai.Client(api_key=api_key)

    articles_payload = [
        {"id": a.id, "title": a.title, "summary": a.summary, "link": a.link}
        for a in articles
    ]

    prompt = f"""Tu es un expert en veille d'actualité.
Évalue la liste d'articles ci-dessous selon les critères suivants : "{criteria}".

Instructions :
1. Donne une note de 0 à 10 pour chaque article selon sa pertinence vis-à-vis des critères.
2. Rédige un résumé très concis (2-3 phrases) en français.
3. Explique en une phrase courte la raison de la note.

Articles à évaluer :
{json.dumps(articles_payload, ensure_ascii=False, indent=2)}
"""

    # Utilisation du mode JSON structuré via le SDK officiel google-genai
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BatchScoreResult,
            temperature=0.2,
        ),
    )

    result = BatchScoreResult.model_validate_json(response.text)
    return result.results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def score_articles_groq(articles: List[Article], criteria: str) -> List[ScoredArticle]:
    """Fallback en utilisant l'API Groq (ex: Llama-3)."""
    from groq import AsyncGroq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY absente.")
        return []

    client = AsyncGroq(api_key=api_key)

    articles_payload = [
        {"id": a.id, "title": a.title, "summary": a.summary, "link": a.link}
        for a in articles
    ]

    prompt = f"""Tu es un expert en veille d'actualité.
Évalue la liste d'articles ci-dessous selon les critères suivants : "{criteria}".
Réponds STRICTEMENT sous la forme d'un objet JSON au format :
{{"results": [{{"id": "string", "score": int, "summary": "string", "reason": "string"}}]}}

Articles à évaluer :
{json.dumps(articles_payload, ensure_ascii=False, indent=2)}
"""

    response = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    text = response.choices[0].message.content
    result = BatchScoreResult.model_validate_json(text)
    return result.results


async def score_articles_batch(articles: List[Article], criteria: str) -> List[ScoredArticle]:
    """Point d'entrée principal pour le scoring avec gestion de fallback."""
    if not articles:
        return []

    provider = get_llm_provider()

    try:
        if provider == "gemini":
            return await score_articles_gemini(articles, criteria)
        elif provider == "groq":
            return await score_articles_groq(articles, criteria)
        else:
            logger.error(f"Fournisseur LLM inconnu : {provider}")
            return []
    except Exception as e:
        logger.warning(f"Échec avec le LLM principal ({provider}): {e}. Tentative de fallback...")
        # Bascule automatique vers le second fournisseur si l'autre clé est dispo
        try:
            if provider == "gemini" and os.getenv("GROQ_API_KEY"):
                return await score_articles_groq(articles, criteria)
            elif provider == "groq" and os.getenv("GEMINI_API_KEY"):
                return await score_articles_gemini(articles, criteria)
        except Exception as fallback_err:
            logger.error(f"Échec du fallback LLM : {fallback_err}")
        
        return []