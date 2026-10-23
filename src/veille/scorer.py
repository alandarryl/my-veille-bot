import json
import logging
import os

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from veille.models import Article, ScoredArticle

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "")
# "0" = pas de réflexion, "" = ne pas envoyer le paramètre
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "0")


class LLMScore(BaseModel):
    id: str
    score: int
    summary: str
    reason: str


class BatchScoreResult(BaseModel):
    results: list[LLMScore]


def _transient(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in (429, 500, 502, 503, 504)


_retry = retry(
    retry=retry_if_exception(_transient),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)


def _payload(articles: list[Article]) -> str:
    return json.dumps(
        [{"id": a.id, "title": a.title, "summary": a.summary[:500]} for a in articles],
        ensure_ascii=False,
    )


def _prompt(articles: list[Article], criteria: str) -> str:
    return f"""Tu es un expert en veille d'actualité.
Critères du salon : "{criteria}"

Pour chaque article :
1. score entier de 0 à 10 selon la pertinence vis-à-vis des critères ;
2. summary : 2 phrases maximum, en français ;
3. reason : une phrase courte justifiant la note.
Réponds en JSON : {{"results": [{{"id": "...", "score": 0, "summary": "...", "reason": "..."}}]}}
Reprends exactement l'id fourni.

Articles :
{_payload(articles)}
"""


def _join(articles: list[Article], scores: list[LLMScore]) -> list[ScoredArticle]:
    by_id = {a.id: a for a in articles}
    out = []
    for s in scores:
        article = by_id.get(s.id)
        if article is None:
            logger.warning("ID inconnu renvoyé par le LLM : %s", s.id)
            continue
        out.append(
            ScoredArticle(
                article=article,
                score=max(0, min(10, s.score)),
                summary=s.summary,
                reason=s.reason,
            )
        )
    return out


def _gemini_config(with_thinking: bool) -> types.GenerateContentConfig:
    kwargs = dict(
        response_mime_type="application/json",
        response_schema=BatchScoreResult,
        temperature=0.2,
    )
    if with_thinking and GEMINI_THINKING_BUDGET != "":
        kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_budget=int(GEMINI_THINKING_BUDGET)
        )
    return types.GenerateContentConfig(**kwargs)


@_retry
async def score_gemini(articles: list[Article], criteria: str) -> list[ScoredArticle]:
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=60_000),  # 60 s
    )
    contents = _prompt(articles, criteria)

    try:
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL, contents=contents, config=_gemini_config(True)
        )
    except Exception as e:
        # Si le modèle refuse le paramètre de réflexion (400), on réessaie sans
        if getattr(e, "code", None) == 400 and GEMINI_THINKING_BUDGET != "":
            logger.warning("Paramètre thinking refusé, nouvel essai sans.")
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL, contents=contents, config=_gemini_config(False)
            )
        else:
            raise

    if not response.text:
        raise ValueError("Réponse Gemini vide")
    result = BatchScoreResult.model_validate_json(response.text)
    return _join(articles, result.results)


@_retry
async def score_groq(articles: list[Article], criteria: str) -> list[ScoredArticle]:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"], timeout=60.0)
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": _prompt(articles, criteria)}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    result = BatchScoreResult.model_validate_json(response.choices[0].message.content)
    return _join(articles, result.results)


async def score_articles_batch(
    articles: list[Article], criteria: str
) -> list[ScoredArticle] | None:
    """Retourne None si TOUS les LLM ont échoué (pour ne pas marquer les articles comme vus)."""
    if not articles:
        return []

    providers = {"gemini": score_gemini, "groq": score_groq}
    keys = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}
    first = os.getenv("DEFAULT_LLM_PROVIDER", "gemini").lower()
    order = [first] + [p for p in providers if p != first]

    for name in order:
        if not os.getenv(keys[name]):
            continue
        if name == "groq" and not GROQ_MODEL:
            logger.info("GROQ_MODEL non défini, fallback Groq ignoré.")
            continue
        try:
            return await providers[name](articles, criteria)
        except Exception as e:
            logger.warning("Échec du LLM %s : %r", name, e)
    return None