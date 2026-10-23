from typing import List, Optional
from pydantic import BaseModel, Field


class Article(BaseModel):
    id: str  # ID unique (souvent l'URL normalisée)
    title: str
    link: str
    summary: str
    published: str
    source_name: Optional[str] = None


class ScoredArticle(BaseModel):
    id: str
    score: int = Field(..., description="Note globale de 0 à 10")
    summary: str = Field(..., description="Résumé concis de 2-3 lignes max")
    reason: str = Field(..., description="Pourquoi cet article a obtenu ce score")


class ChannelConfig(BaseModel):
    description: str
    top_n: int = 3
    min_score: int = 7
    criteria: str
    feeds: List[str] = []