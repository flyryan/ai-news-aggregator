"""
Gatherer Agents

Each gatherer is responsible for collecting items from a specific source category.
"""

from .news_gatherer import NewsGatherer
from .research_gatherer import ResearchGatherer
from .social_gatherer import SocialGatherer
from .reddit_gatherer import RedditGatherer
from .link_follower import LinkFollower

__all__ = [
    'NewsGatherer',
    'ResearchGatherer',
    'SocialGatherer',
    'RedditGatherer',
    'LinkFollower',
]
