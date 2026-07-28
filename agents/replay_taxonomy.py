"""Maps raw LLM ``caller`` strings onto the pipeline's cast of agents.

Every async LLM call already carries a legible caller tag (``news_analyzer.batch_3``,
``orchestrator.topics``, ``link_enricher.topic: Foo``). That string is the only
attribution the pipeline records, so this module is the single place that turns it into
the identity the replay UI renders: which agent, which phase, what kind of work.

Resolution always succeeds. An unrecognised caller falls back to a sensible guess rather
than being dropped -- a replay missing a call is worse than one with a generic label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

# Roles describe the *shape* of the work, which is what the visualisation keys off:
# map = one of many parallel workers, reduce = the join that follows, synthesize = the
# big single-shot reasoning calls, and so on.
ROLE_MAP = "map"
ROLE_REDUCE = "reduce"
ROLE_FILTER = "filter"
ROLE_SYNTHESIZE = "synthesize"
ROLE_ENRICH = "enrich"
ROLE_MATCH = "match"
ROLE_CURATE = "curate"
ROLE_CHECK = "check"
ROLE_IMAGE = "image"

# Roles whose streams are always kept in full when the size cap forces a choice.
MARQUEE_ROLES = frozenset({ROLE_SYNTHESIZE, ROLE_REDUCE, ROLE_CURATE})

CATEGORIES = ("news", "research", "social", "reddit")


@dataclass(frozen=True)
class AgentIdentity:
    """Static description of one member of the cast."""

    id: str
    label: str
    kind: str  # gatherer | analyzer | synthesizer | enricher | imagegen
    category: Optional[str] = None
    blurb: str = ""


@dataclass(frozen=True)
class CallIdentity:
    """Resolved attribution for a single LLM call."""

    agent_id: str
    task: str
    role: str
    worker: Optional[int] = None


_CATEGORY_LABELS = {
    "news": "News",
    "research": "Research",
    "social": "Social",
    "reddit": "Reddit",
}

_ANALYST_BLURBS = {
    "news": "Reads product launches and company announcements off the RSS wire.",
    "research": "Works through arXiv preprints and alignment blogs.",
    "social": "Follows what practitioners are saying on Twitter, Bluesky and Mastodon.",
    "reddit": "Digests community threads and the arguments underneath them.",
}

_GATHERER_BLURBS = {
    "news": "Pulls RSS feeds and chases links surfaced by social posts.",
    "research": "Queries the arXiv API and research blogs.",
    "social": "Collects posts from Twitter, Bluesky and Mastodon.",
    "reddit": "Fetches subreddit listings and top comment threads.",
}

# The full cast, declared up front so the stage can be drawn before any call resolves
# and so agents that did no LLM work (gatherers) still appear.
AGENTS: Dict[str, AgentIdentity] = {}

for _cat in CATEGORIES:
    AGENTS[f"{_cat}_gatherer"] = AgentIdentity(
        id=f"{_cat}_gatherer",
        label=f"{_CATEGORY_LABELS[_cat]} Scout",
        kind="gatherer",
        category=_cat,
        blurb=_GATHERER_BLURBS[_cat],
    )
    AGENTS[f"{_cat}_analyzer"] = AgentIdentity(
        id=f"{_cat}_analyzer",
        label=f"{_CATEGORY_LABELS[_cat]} Analyst",
        kind="analyzer",
        category=_cat,
        blurb=_ANALYST_BLURBS[_cat],
    )

AGENTS.update(
    {
        "continuity": AgentIdentity(
            id="continuity",
            label="Continuity Editor",
            kind="synthesizer",
            blurb="Links today's stories to the days before them.",
        ),
        "freshness": AgentIdentity(
            id="freshness",
            label="Fact Checker",
            kind="synthesizer",
            blurb="Checks whether older anchor stories have gone stale.",
        ),
        "orchestrator": AgentIdentity(
            id="orchestrator",
            label="Editor in Chief",
            kind="synthesizer",
            blurb="Finds the threads running across categories and writes the brief.",
        ),
        "link_enricher": AgentIdentity(
            id="link_enricher",
            label="Copy Editor",
            kind="enricher",
            blurb="Wires every claim in the prose back to the item it came from.",
        ),
        "ecosystem": AgentIdentity(
            id="ecosystem",
            label="Archivist",
            kind="enricher",
            blurb="Watches for model releases worth recording.",
        ),
        "hero": AgentIdentity(
            id="hero",
            label="Illustrator",
            kind="imagegen",
            blurb="Paints the day's scene around the AATF skunk.",
        ),
    }
)


def agent_ids() -> List[str]:
    """Cast list in a stable, stage-left-to-right order."""
    ordered = [f"{c}_gatherer" for c in CATEGORIES]
    ordered += [f"{c}_analyzer" for c in CATEGORIES]
    ordered += ["continuity", "freshness", "orchestrator", "link_enricher", "ecosystem", "hero"]
    return ordered


_BATCH_RE = re.compile(r"^(?P<cat>\w+)_analyzer\.batch_(?P<n>\d+)(?P<split>[a-z])?(?P<retry>_retry)?$")
_MATCHER_RE = re.compile(r"^continuity\.matcher\.(?P<cat>\w+)$")
_ENRICH_RE = re.compile(r"^link_enricher\.(?P<ctx>.+)$")


def _titlecase_context(ctx: str) -> str:
    """`topic: Opus 5 Benchmarks` -> `Link topic "Opus 5 Benchmarks"`."""
    ctx = ctx.strip()
    if ctx.startswith("topic:"):
        return f'Link topic "{ctx.split(":", 1)[1].strip()}"'
    return f"Link {ctx}"


def resolve_call(caller: str) -> CallIdentity:
    """Turn a raw caller tag into a rendered identity. Never raises."""
    if not caller:
        return CallIdentity(agent_id="orchestrator", task="Unattributed call", role=ROLE_MAP)

    caller = caller.strip()

    match = _BATCH_RE.match(caller)
    if match:
        cat = match.group("cat")
        n = int(match.group("n"))
        split = match.group("split")
        retry = bool(match.group("retry"))
        verb = "Retry" if retry else "Analyze"
        label = f"{verb} batch {n}"
        if split:
            label += f" (split {split})"
        return CallIdentity(agent_id=f"{cat}_analyzer", task=label, role=ROLE_MAP, worker=n)

    if caller.endswith("_analyzer.reduce_rank"):
        cat = caller.split("_analyzer.", 1)[0]
        return CallIdentity(agent_id=f"{cat}_analyzer", task="Rank and select", role=ROLE_REDUCE)

    if caller == "news_analyzer.filter":
        return CallIdentity(agent_id="news_analyzer", task="Pre-filter articles", role=ROLE_FILTER)

    if caller == "news_analyzer.small_batch":
        return CallIdentity(
            agent_id="news_analyzer", task="Analyze (small batch)", role=ROLE_MAP, worker=0
        )

    match = _MATCHER_RE.match(caller)
    if match:
        cat = match.group("cat")
        return CallIdentity(
            agent_id="continuity", task=f"Match {cat} to prior days", role=ROLE_MATCH
        )

    if caller == "continuity.curator":
        return CallIdentity(agent_id="continuity", task="Curate storylines", role=ROLE_CURATE)

    if caller.startswith("freshness."):
        return CallIdentity(agent_id="freshness", task="Check anchor freshness", role=ROLE_CHECK)

    if caller == "orchestrator.topics":
        return CallIdentity(
            agent_id="orchestrator", task="Detect cross-category topics", role=ROLE_SYNTHESIZE
        )

    if caller == "orchestrator.summary":
        return CallIdentity(
            agent_id="orchestrator", task="Write executive summary", role=ROLE_SYNTHESIZE
        )

    match = _ENRICH_RE.match(caller)
    if match:
        return CallIdentity(
            agent_id="link_enricher", task=_titlecase_context(match.group("ctx")), role=ROLE_ENRICH
        )

    if caller.startswith("ecosystem_context."):
        return CallIdentity(agent_id="ecosystem", task="Detect model releases", role=ROLE_ENRICH)

    # Fail open: keep the call, guess the owner from the prefix.
    guessed = caller.split(".", 1)[0] or "orchestrator"
    return CallIdentity(agent_id=guessed, task=caller, role=ROLE_MAP)


def agent_for(agent_id: str) -> AgentIdentity:
    """Look up cast metadata, synthesising an entry for unknown ids."""
    known = AGENTS.get(agent_id)
    if known is not None:
        return known
    label = agent_id.replace("_", " ").title()
    return AgentIdentity(id=agent_id, label=label, kind="synthesizer")
