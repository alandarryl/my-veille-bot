from typing import List, Optional
from pydantic import BaseModel, Field


class Article(BaseModel):
    id: str
    title: str
    link: str
    summary: str
    published: str
    source_name: Optional[str] = None
    image: Optional[str] = None


class ScoredArticle(BaseModel):
    article: Article
    score: int
    summary: str
    reason: str


class ChannelConfig(BaseModel):
    description: str
    top_n: int = 3
    min_score: int = 7
    criteria: str
    feeds: List[str] = []