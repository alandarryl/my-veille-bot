import json
import logging
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

SEEN_FILE_PATH = Path("data/seen.json")


def load_seen_ids() -> Set[str]:
    """Charge l'ensemble des IDs d'articles déjà traités."""
    if not SEEN_FILE_PATH.exists():
        SEEN_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEEN_FILE_PATH.write_text("[]", encoding="utf-8")
        return set()

    try:
        with open(SEEN_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception as e:
        logger.error(f"Erreur lors de la lecture de {SEEN_FILE_PATH}: {e}")
        return set()


def save_seen_ids(seen_ids: Set[str]) -> None:
    """Sauvegarde les IDs vus dans le fichier data/seen.json."""
    SEEN_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Garde seulement les 2000 derniers IDs pour éviter d'avoir un fichier trop lourd
        trimmed_ids = list(seen_ids)[-2000:]
        with open(SEEN_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(trimmed_ids, f, indent=2, ensure_ascii=False)
        logger.info(f"{len(trimmed_ids)} IDs sauvegardés dans {SEEN_FILE_PATH}")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde dans {SEEN_FILE_PATH}: {e}")