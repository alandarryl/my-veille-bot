import sys
import os
import asyncio
import logging
import yaml
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from veille.models import ChannelConfig
from veille.dedupe import load_seen_ids, save_seen_ids
from veille.fetch import fetch_all_channel_feeds
from veille.scorer import score_articles_batch
from veille.discord import send_to_discord

# Configuration des logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

load_dotenv()  # Charge le .env local si présent


def is_scheduled_time() -> bool:
    """Vérifie si l'heure actuelle à Paris est 9h ou 18h (utilisé pour GitHub Actions)."""
    if os.getenv("FORCE_RUN", "false").lower() == "true":
        return True

    now_paris = datetime.now(ZoneInfo("Europe/Paris"))
    logger.info(f"Heure actuelle à Paris : {now_paris.strftime('%H:%M %d/%m/%Y')}")

    return now_paris.hour in [9, 18]


async def process_channel(channel_name: str, config: ChannelConfig, seen_ids: set) -> set:
    """Traite un canal : Fetch -> Scoring -> Discord -> Mise à jour des IDs vus."""
    logger.info(f"--- Traitement du canal : {channel_name} ---")

    # 1. Récupération des articles
    articles = await fetch_all_channel_feeds(config.feeds, seen_ids)
    if not articles:
        logger.info(f"[{channel_name}] Aucun nouvel article à analyser.")
        return set()

    logger.info(f"[{channel_name}] {len(articles)} nouveau(x) article(s) trouvé(s).")

    # 2. Notation par le LLM
    scored_results = await score_articles_batch(articles, config.criteria)
    
    # SÉCURITÉ : Si le LLM a totalement échoué, on retourne un set vide.
    # Les articles ne seront PAS marqués comme vus et pourront être retentés plus tard.
    if scored_results is None:
        logger.error(f"[{channel_name}] Échec du scoring LLM. Les articles ne sont pas marqués comme vus.")
        return set()

    scored_map = {res.id: res for res in scored_results}

    # 3. Filtrage selon les règles (min_score et top_n)
    selected_items = []
    new_seen_ids = set()

    for article in articles:
        new_seen_ids.add(article.id)  # Marqués comme vus uniquement si le scoring a fonctionné
        scored = scored_map.get(article.id)
        if scored and scored.score >= config.min_score:
            selected_items.append((article, scored))

    # Tri par score décroissant et conservation du top_n
    selected_items.sort(key=lambda x: x[1].score, reverse=True)
    selected_items = selected_items[:config.top_n]

    # 4. Envoi sur Discord
    if selected_items:
        await send_to_discord(channel_name, selected_items)
    else:
        logger.info(f"[{channel_name}] Aucun article n'a atteint le score minimum de {config.min_score}.")

    return new_seen_ids


async def main():
    if not is_scheduled_time():
        logger.info("Ce n'est ni 9h ni 18h à Paris (et pas de FORCE_RUN). Arrêt du script.")
        sys.exit(0)

    # Chargement de la configuration des canaux
    try:
        with open("config/channels.yaml", "r", encoding="utf-8") as f:
            channels_raw = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Impossible de lire config/channels.yaml: {e}")
        sys.exit(1)

    seen_ids = load_seen_ids()
    all_new_seen_ids = set()

    for channel_name, raw_config in channels_raw.items():
        if not raw_config.get("feeds"):
            continue

        config = ChannelConfig(**raw_config)
        processed_ids = await process_channel(channel_name, config, seen_ids.union(all_new_seen_ids))
        all_new_seen_ids.update(processed_ids)

    # Sauvegarde des nouveaux IDs
    if all_new_seen_ids:
        seen_ids.update(all_new_seen_ids)
        save_seen_ids(seen_ids)

    logger.info("Veille terminée avec succès !")


if __name__ == "__main__":
    asyncio.main() if hasattr(asyncio, "main") else asyncio.run(main())