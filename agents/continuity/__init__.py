"""
Continuity Detection Package

Two-stage architecture for detecting story continuations across days:
  Stage A: Parallel story matching (4 matchers, one per category)
  Stage B: Editorial curation (1 curator for nuanced decisions)
"""

from .coordinator import ContinuityCoordinator
from .matcher import StoryMatcher
from .curator import EditorialCurator

__all__ = ['ContinuityCoordinator', 'StoryMatcher', 'EditorialCurator']
