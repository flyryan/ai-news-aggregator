"""
AI News Aggregator - Multi-Agent System

This module contains the agent-based architecture for collecting and analyzing
AI/ML news from multiple sources.
"""

from .llm_client import AnthropicClient, AsyncAnthropicClient, ThinkingLevel, LLMResponse
from .base import (
    BaseGatherer, BaseAnalyzer,
    CollectedItem, AnalyzedItem, CategoryReport, CategoryTheme,
    deduplicate_items
)
from .orchestrator import MainOrchestrator, TopTopic, OrchestratorResult
from .gatherers import NewsGatherer, ResearchGatherer, SocialGatherer, RedditGatherer, LinkFollower
from .analyzers import NewsAnalyzer, ResearchAnalyzer, SocialAnalyzer, RedditAnalyzer

__all__ = [
    # LLM Client
    'AnthropicClient',
    'AsyncAnthropicClient',
    'ThinkingLevel',
    'LLMResponse',
    # Base classes
    'BaseGatherer',
    'BaseAnalyzer',
    'CollectedItem',
    'AnalyzedItem',
    'CategoryReport',
    'CategoryTheme',
    'deduplicate_items',
    # Orchestrator
    'MainOrchestrator',
    'TopTopic',
    'OrchestratorResult',
    # Gatherers
    'NewsGatherer',
    'ResearchGatherer',
    'SocialGatherer',
    'RedditGatherer',
    'LinkFollower',
    # Analyzers
    'NewsAnalyzer',
    'ResearchAnalyzer',
    'SocialAnalyzer',
    'RedditAnalyzer',
]
