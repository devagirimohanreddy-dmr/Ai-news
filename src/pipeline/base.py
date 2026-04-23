"""Base class for all pipeline stages."""

from abc import ABC, abstractmethod

from src.models.article import Article


class PipelineStage(ABC):
    """Abstract base for a single processing stage in the article pipeline.

    Each stage receives an Article ORM instance, performs its work (possibly
    mutating the article or creating related rows), and returns the article
    to pass it to the next stage.  Returning ``None`` signals that the
    article should be dropped from the pipeline (e.g. duplicate detected).
    """

    @abstractmethod
    async def process(self, article: Article) -> Article | None:
        """Process article. Return article to continue, None to stop pipeline."""
        ...
