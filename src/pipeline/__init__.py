"""Article processing pipeline — 6-stage ingestion, dedup, classification, scoring, summarization, and routing."""

from src.pipeline.orchestrator import ArticlePipeline

__all__ = ["ArticlePipeline"]
