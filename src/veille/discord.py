import json
import os
import logging
from typing import List, Dict
from urllib.parse import urlparse
import httpx

from veille.models import Article, ScoredArticle

logger = logging.getLogger(__name__)


def get_webhooks_mapping() -> Dict[str, str]:
    """Extrait le mapping JSON des webhooks depuis les variables d'environnement."""
    raw_env = os.getenv("DISCORD_WEBHOOKS", "{}")
    try:
        return json.loads(raw_env)
    except Exception as e:
        logger.error(f"Erreur de lecture du JSON DISCORD_WEBHOOKS : {e}")
        return {}


def _score_bar(score: int) -> str:
    """Génère une barre de score visuelle avec des carrés."""
    score = max(0, min(10, score))
    return "🟩" * score + "⬜" * (10 - score) + f" **{score}/10**"


def _favicon(link: str) -> str | None:
    """Récupère le favicon du site web source."""
    domain = urlparse(link).netloc
    if not domain:
        return None
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


def _valid_image(url: str | None) -> bool:
    """Vérifie si l'URL de l'image est valide pour Discord."""
    return bool(url) and url.startswith(("http://", "https://")) and len(url) < 2000


async def send_to_discord(
    channel_name: str,
    scored_items: List[tuple[Article, ScoredArticle]]
) -> bool:
    """Envoie un lot d'articles sélectionnés sur le Webhook Discord correspondant avec gestion des images."""
    webhooks = get_webhooks_mapping()
    webhook_url = webhooks.get(channel_name)

    if not webhook_url:
        logger.warning(f"Aucun Webhook configuré pour le canal : {channel_name}")
        return False

    async with httpx.AsyncClient() as client:
        # On tente d'abord avec les images, puis sans si Discord refuse (erreur 400)
        for with_images in (True, False):
            embeds = []
            
            for i, (article, scored) in enumerate(scored_items):
                color = 0x3498DB  # Bleu par défaut
                if scored.score >= 9:
                    color = 0x2ECC71  # Vert pour les meilleures news
                elif scored.score >= 8:
                    color = 0xF1C40F  # Jaune

                embed = {
                    "title": article.title[:256],
                    "url": article.link,
                    "description": scored.summary[:4096],
                    "color": color,
                    "fields": [
                        {"name": "⭐ Score", "value": _score_bar(scored.score), "inline": False},
                        {"name": "💡 Pertinence", "value": scored.reason[:1024], "inline": False},
                    ],
                    "footer": {
                        "text": f"Source: {article.source_name or 'Veille Bot'}"
                    }
                }

                # Ajout du favicon de la source dans l'auteur si possible
                favicon = _favicon(article.link)
                if favicon:
                    embed["author"] = {
                        "name": article.source_name or "Veille Bot",
                        "icon_url": favicon
                    }

                # Gestion dynamique des images
                if with_images and _valid_image(article.image):
                    # Le 1er article (le meilleur) a une grande image, les suivants une miniature à droite
                    embed["image" if i == 0 else "thumbnail"] = {"url": article.image}

                embeds.append(embed)

            payload = {
                "username": "Bot de Veille",
                "avatar_url": "https://cdn-icons-png.flaticon.com/512/3254/3254933.png",
                "embeds": embeds[:10]
            }

            try:
                response = await client.post(webhook_url, json=payload, timeout=15.0)
                if response.status_code in [200, 204]:
                    logger.info(f"[{channel_name}] {len(scored_items)} article(s) envoyé(s) avec succès !")
                    return True
                
                # Si Discord renvoie une erreur 400 à cause d'une image corrompue, on réessaie sans images
                if response.status_code == 400 and with_images:
                    logger.warning(f"[{channel_name}] Discord refuse l'image (400), nouvel essai sans images...")
                    continue

                logger.error(f"[{channel_name}] Erreur Discord {response.status_code}: {response.text}")
                return False

            except Exception as e:
                logger.error(f"Erreur d'envoi Webhook Discord ({channel_name}): {e}")
                return False

    return False