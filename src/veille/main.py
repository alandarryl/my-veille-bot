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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

load_dotenv()


def is_scheduled_time() -> bool:
    """Vérifie si l'heure actuelle à Paris est 9h ou 18h."""
    if os.getenv("FORCE_RUN", "false").lower() == "true":
        return True

    now_paris = datetime.now(ZoneInfo("Europe/Paris"))
    logger.info(f"Heure actuelle à Paris : {now_paris.strftime('%H:%M %d/%m/%Y')}")
    return now_paris.hour in [9, 18]


async def process_channel(channel_name: str, config: ChannelConfig, seen_ids: set) -> set | None:
    """Retourne les IDs à marquer comme vus, ou None si le salon a échoué."""
    logger.info(f"--- Traitement du canal : {channel_name} ---")

    articles = await fetch_all_channel_feeds(config.feeds, seen_ids)
    if not articles:
        logger.info(f"[{channel_name}] Aucun nouvel article à analyser.")
        return set()

    logger.info(f"[{channel_name}] {len(articles)} nouveau(x) article(s) trouvé(s).")

    scored_results = await score_articles_batch(articles, config.criteria)
    if scored_results is None:
        logger.error(f"[{channel_name}] Échec du scoring LLM, articles non marqués comme vus.")
        return None

    selected = [s for s in scored_results if s.score >= config.min_score]
    selected.sort(key=lambda s: s.score, reverse=True)
    selected = selected[: config.top_n]

    if selected:
        ok = await send_to_discord(channel_name, [(s.article, s) for s in selected])
        if not ok:
            logger.error(f"[{channel_name}] Envoi Discord échoué, articles non marqués comme vus.")
            return None
    else:
        logger.info(f"[{channel_name}] Aucun article n'a atteint le score minimum de {config.min_score}.")

    return {a.id for a in articles}


async def main():
    if not is_scheduled_time():
        logger.info("Ce n'est ni 9h ni 18h à Paris (et pas de FORCE_RUN). Arrêt du script.")
        sys.exit(0)

    try:
        with open("config/channels.yaml", "r", encoding="utf-8") as f:
            channels_raw = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Impossible de lire config/channels.yaml: {e}")
        sys.exit(1)

    seen_ids = load_seen_ids()
    failed: list[str] = []

    for channel_name, raw_config in channels_raw.items():
        if not raw_config.get("feeds"):
            continue
        try:
            config = ChannelConfig(**raw_config)
            processed = await process_channel(channel_name, config, seen_ids)
        except Exception as e:
            logger.error(f"[{channel_name}] Erreur inattendue : {e!r}")
            processed = None

        if processed is None:
            failed.append(channel_name)
            continue

        if processed:
            seen_ids.update(processed)
            save_seen_ids(seen_ids)  # sauvegarde après chaque salon

        await asyncio.sleep(1)  # évite de saturer les quotas

    if failed:
        logger.error("Salons en échec : %s", ", ".join(failed))
        sys.exit(1)

    logger.info("Veille terminée avec succès !")


if __name__ == "__main__":
    asyncio.run(main())