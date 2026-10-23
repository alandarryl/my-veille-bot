import asyncio
import logging
import hashlib
import re
from typing import List, Set, Optional
import feedparser
import httpx

from veille.models import Article

logger = logging.getLogger(__name__)

# Expression régulière pour détecter les balises <img> dans le HTML brut
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


def generate_article_id(link: str) -> str:
    """Génère un ID unique à partir de l'URL."""
    return hashlib.sha256(link.strip().encode("utf-8")).hexdigest()[:16]


def extract_image(entry) -> Optional[str]:
    """Extrait l'URL d'une image depuis l'entrée du flux RSS (MediaRSS, enclosures ou HTML)."""
    # 1. Recherche dans les balises MediaRSS (ex: <media:thumbnail>, <media:content>)
    for key in ("media_thumbnail", "media_content"):
        for m in entry.get(key) or []:
            url = m.get("url")
            if url and url.startswith("http"):
                return url

    # 2. Recherche dans les fichiers joints (enclosures)
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href")

    # 3. Recherche de balises <img> dans le HTML du contenu ou du résumé brut
    html = entry.get("summary", "") or entry.get("description", "") or ""
    for c in entry.get("content") or []:
        html += c.get("value", "")
    
    match = IMG_RE.search(html)
    if match and match.group(1).startswith("http"):
        return match.group(1)

    return None


def clean_html(raw_html: str) -> str:
    """Nettoie le HTML du résumé pour ne garder que le texte brut."""
    if not raw_html:
        return ""
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    return clean_text.strip()


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

            # Extraction de l'image AVANT de nettoyer ou tronquer le résumé
            image_url = extract_image(entry)

            raw_summary = entry.get("summary") or entry.get("description") or ""
            summary = clean_html(raw_summary)
            published = entry.get("published") or entry.get("updated") or ""

            article_id = generate_article_id(link)

            articles.append(
                Article(
                    id=article_id,
                    title=title.strip(),
                    link=link.strip(),
                    summary=summary[:500],  # Limite la taille pour le LLM
                    published=published,
                    source_name=source_title,
                    image=image_url,
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