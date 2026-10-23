import asyncio
import logging
import hashlib
from typing import List, Set
import feedparser
import httpx

from veille.models import Article

logger = logging.getLogger(__name__)


def generate_article_id(link: str) -> str:
    """Génère un ID unique à partir de l'URL."""
    return hashlib.sha256(link.strip().encode("utf-8")).hexdigest()[:16]


async def fetch_single_feed(client: httpx.AsyncClient, feed_url: str) -> List[Article]:
    """Télécharge et parse un seul flux RSS/Atom."""
    articles = []
    try:
        response = await client.get(feed_url, timeout=10.0, follow_redirects=True)
        if response.status_code != 200:
            logger.warning(f"Échec HTTP {response.status_code} pour la source : {feed_url}")
            return []

        parsed = feedparser.parse(response.content)
        source_title = parsed.feed.get("title", feed_url)

        for entry in parsed.entries[:15]:  # Prend max 15 articles récents par flux
            link = entry.get("link", "")
            title = entry.get("title", "Sans titre")

            if not link or not title:
                continue

            summary = entry.get("summary") or entry.get("description") or ""
            published = entry.get("published") or entry.get("updated") or ""

            article_id = generate_article_id(link)

            articles.append(
                Article(
                    id=article_id,
                    title=title.strip(),
                    link=link.strip(),
                    summary=summary.strip()[:500],  # Limite la taille
                    published=published,
                    source_name=source_title,
                )
            )

    except Exception as e:
        logger.error(f"Erreur de récupération du flux {feed_url}: {e}")

    return articles


async def fetch_all_channel_feeds(feed_urls: List[str], seen_ids: Set[str]) -> List[Article]:
    """Récupère les articles de tous les flux d'un canal et filtre les déjà vus."""
    if not feed_urls:
        return []

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (VeilleBot/1.0)"}
    ) as client:
        tasks = [fetch_single_feed(client, url) for url in feed_urls]
        results = await asyncio.gather(*tasks)

    # Aplatit la liste d'articles
    all_articles = [article for sublist in results for article in sublist]

    # Filtrage contre les doublons vus précédemment ou doublons au sein du même batch
    unique_articles = []
    current_batch_ids = set()

    for art in all_articles:
        if art.id not in seen_ids and art.id not in current_batch_ids:
            unique_articles.append(art)
            current_batch_ids.add(art.id)

    return unique_articles