import json
import os
import logging
from typing import List, Dict
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


async def send_to_discord(
    channel_name: str,
    scored_items: List[tuple[Article, ScoredArticle]]
) -> bool:
    """Envoie un lot d'articles sélectionnés sur le Webhook Discord correspondant."""
    webhooks = get_webhooks_mapping()
    webhook_url = webhooks.get(channel_name)

    if not webhook_url:
        logger.warning(f"Aucun Webhook configuré pour le canal : {channel_name}")
        return False

    embeds = []
    for article, scored in scored_items:
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
                {"name": "⭐ Score", "value": f"{scored.score}/10", "inline": True},
                {"name": "💡 Pertinence", "value": scored.reason[:1024], "inline": True},
            ],
            "footer": {
                "text": f"Source: {article.source_name or 'Veille Bot'}"
            }
        }
        embeds.append(embed)

    # Discord autorise max 10 embeds par message
    payload = {
        "username": "Bot de Veille",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/3254/3254933.png",
        "embeds": embeds[:10]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(webhook_url, json=payload, timeout=10.0)
            if response.status_code in [200, 204]:
                logger.info(f"[{channel_name}] {len(scored_items)} article(s) envoyé(s) avec succès !")
                return True
            else:
                logger.error(f"[{channel_name}] Erreur Discord {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Erreur d'envoi Webhook Discord ({channel_name}): {e}")
            return False