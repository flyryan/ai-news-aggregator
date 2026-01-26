# Phase 4: Prompt Abstraction - Research

**Researched:** 2026-01-25
**Domain:** LLM prompt externalization and YAML configuration
**Confidence:** HIGH

## Summary

This phase extracts all LLM prompts from Python code to external YAML configuration. The codebase already has a proven YAML configuration pattern with environment variable interpolation (in `agents/config/loader.py`) and Pydantic validation (in `agents/config/schema.py`). The prompt extraction task is straightforward: identify all prompts (class-level constants in analyzers, inline strings in orchestrator and utilities), move them to a single `prompts.yaml` file organized by pipeline phase, and update the code to load prompts from config rather than hardcoding.

The existing loader.py `${VAR}` pattern will be extended to support runtime variables (not just env vars). YAML's literal block scalar (`|`) preserves multiline prompts exactly as written, making it ideal for LLM prompts where formatting matters.

**Primary recommendation:** Extend the existing `load_yaml_with_env()` function to support runtime variable injection via a new `resolve_variables(data, context)` helper. Create a `PromptConfig` Pydantic model for validation. Keep the single-file approach per user decision.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyYAML | 6.0.2+ | YAML parsing | Already used in project; safe_load is secure |
| Pydantic | 2.x | Schema validation | Already used for providers.yaml validation |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| re (stdlib) | - | Variable pattern matching | Already used for `${VAR}` env var interpolation |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Single prompts.yaml | Split by agent type | User decided single file; simpler to manage |
| Jinja2 templates | Simple ${var} substitution | User decided no composition; simpler is better |
| TOML | YAML | YAML already used in project; better multiline support |

**Installation:** No new dependencies required.

## Architecture Patterns

### Recommended File Structure
```
config/
├── providers.yaml          # Existing - LLM/Image provider config
├── prompts.yaml            # NEW - All LLM prompts
├── model_releases.yaml     # Existing - Curated model releases
└── ecosystem_context.yaml  # Existing - Auto-generated cache

agents/
├── config/
│   ├── __init__.py
│   ├── loader.py           # MODIFIED - Add prompt loading
│   ├── schema.py           # MODIFIED - Add PromptConfig model
│   └── prompts.py          # NEW - Prompt accessor class
```

### Pattern 1: YAML Structure for Prompts
**What:** Organize prompts by pipeline phase with nested structure
**When to use:** All prompt configuration
**Example:**
```yaml
# config/prompts.yaml
# Source: Derived from existing codebase analysis

# =============================================================================
# Gathering Phase Prompts
# =============================================================================
gathering:
  # Link follower decides which URLs to fetch from social posts
  link_relevance:
    # Available variables: ${url}, ${post_context}
    prompt: |
      Evaluate if this URL is likely to contain a relevant AI/ML news article.

      URL: ${url}
      Post context: ${post_context}

      Reply with just "YES" or "NO".

# =============================================================================
# Analysis Phase Prompts
# =============================================================================
analysis:
  news:
    # Available variables: ${batch_index}, ${total_batches}, ${items_context}
    batch_analysis: |
      You are an AI news analyst covering the frontier of artificial intelligence.
      Analyze these AI news articles (batch ${batch_index} of ${total_batches}).
      ...

    # Available variables: ${items_context}, ${example_id}
    filter: |
      You are filtering news articles for a FRONTIER AI newsletter.
      ...

    # Available variables: ${analysis_summary}
    ranking: |
      Rank the top 10 most important AI news stories.
      ...

# =============================================================================
# Orchestration Phase Prompts
# =============================================================================
orchestration:
  topic_detection:
    # Available variables: ${context}
    prompt: |
      Analyze the following category reports...

  executive_summary:
    # Available variables: ${context}
    prompt: |
      Write a structured executive summary...

# =============================================================================
# Post-Processing Phase Prompts
# =============================================================================
post_processing:
  link_enrichment:
    # Available variables: ${date}, ${items_json}, ${text}
    prompt: |
      You are a link enrichment agent...

  ecosystem_enrichment:
    # Available variables: ${report_date}, ${coverage_date}, ${existing_models}, ${news_items}
    prompt: |
      You are an AI model release analyst...
```

### Pattern 2: Variable Resolution
**What:** Extend env var interpolation to support runtime context
**When to use:** When loading prompts with runtime variables
**Example:**
```python
# Source: Extending existing loader.py pattern

# Extended pattern to match both ${VAR} and ${env:VAR}
# ${VAR} = runtime variable from context dict
# ${env:VAR} = environment variable (explicit)
VAR_PATTERN = re.compile(r'\$\{(env:)?([A-Za-z_][A-Za-z0-9_]*)\}')

def resolve_variables(
    value: Any,
    context: Dict[str, str],
    allow_missing: bool = False,
    path: str = ""
) -> Any:
    """Resolve ${var} patterns in YAML values.

    Args:
        value: The value to process
        context: Runtime context dict (e.g., {"date": "2026-01-25"})
        allow_missing: If True, leave unresolved vars as-is
        path: Current path for error messages
    """
    if isinstance(value, str):
        def replacer(match):
            is_env = match.group(1) is not None
            var_name = match.group(2)

            if is_env:
                # Environment variable
                env_val = os.environ.get(var_name)
                if env_val is None and not allow_missing:
                    raise ValueError(f"Environment variable '{var_name}' not set (at {path})")
                return env_val if env_val else match.group(0)
            else:
                # Runtime context variable
                if var_name in context:
                    return str(context[var_name])
                elif not allow_missing:
                    raise ValueError(f"Variable '{var_name}' not in context (at {path})")
                return match.group(0)

        return VAR_PATTERN.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: resolve_variables(v, context, allow_missing, f"{path}.{k}") for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_variables(item, context, allow_missing, f"{path}[{i}]") for i, item in enumerate(value)]
    return value
```

### Pattern 3: Pydantic Schema for Prompts
**What:** Validate prompt structure at load time
**When to use:** Config validation during pipeline startup
**Example:**
```python
# Source: Derived from existing schema.py pattern

from pydantic import BaseModel, Field
from typing import Dict, Optional

class PromptEntry(BaseModel):
    """A single prompt with optional metadata."""
    prompt: str = Field(..., min_length=10)
    allow_missing: bool = Field(default=False, description="Skip variable validation")

    # Alternative: prompts can be bare strings
    @classmethod
    def from_string(cls, s: str) -> 'PromptEntry':
        return cls(prompt=s)

class AnalyzerPrompts(BaseModel):
    """Prompts for a category analyzer."""
    batch_analysis: str
    ranking: str
    filter: Optional[str] = None  # Only news analyzer has filter
    combined_analysis: Optional[str] = None  # For small batches

class PromptConfig(BaseModel):
    """Root schema for prompts.yaml."""
    gathering: Dict[str, str]
    analysis: Dict[str, AnalyzerPrompts]
    orchestration: Dict[str, str]
    post_processing: Dict[str, str]
```

### Anti-Patterns to Avoid
- **Hardcoded fallbacks:** Don't fall back to hardcoded prompts if YAML is missing. Fail fast with clear instructions.
- **Prompt composition:** User decided against includes/inheritance. Keep prompts self-contained even if it means some repetition.
- **Exposing config in output:** Don't include prompt keys or config paths in JSON output.
- **Complex templating:** Avoid Jinja2 or complex logic in templates. Simple ${var} substitution only.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML multiline strings | Custom escaping | YAML literal blocks (`\|`) | YAML handles this natively |
| Environment variable interpolation | New parser | Existing `_resolve_env_vars()` | Already proven in codebase |
| Config validation | Manual checks | Pydantic models | Type safety, clear errors |
| Missing file handling | Silently fail | `sys.exit(1)` with instructions | User decided fail-fast |

**Key insight:** The existing config pattern is well-designed. Extend it rather than creating a parallel system.

## Common Pitfalls

### Pitfall 1: YAML Multiline String Formatting
**What goes wrong:** Prompts include special YAML characters (`:`, `#`, `{`, `}`) causing parse errors
**Why it happens:** YAML has complex rules about special characters in strings
**How to avoid:** Always use literal block scalars (`|`) for prompts. They're safe and preserve formatting exactly.
**Warning signs:** YAML parse errors mentioning "unexpected" characters, prompts rendered as YAML mappings

### Pitfall 2: Variable vs Environment Variable Confusion
**What goes wrong:** User puts `${MODEL}` expecting runtime but it looks for env var
**Why it happens:** Original pattern only supported env vars; user needs both
**How to avoid:** Use explicit `${env:VAR}` for env vars, bare `${var}` for runtime context
**Warning signs:** Variables not resolving, env vars leaking into prompts

### Pitfall 3: Prompt Key Mismatch
**What goes wrong:** Code asks for `analysis.news.filter` but YAML has `analysis.news.filter_prompt`
**Why it happens:** No compile-time validation between code and YAML keys
**How to avoid:** Pydantic schema catches missing keys at startup; document expected keys clearly
**Warning signs:** KeyError at runtime, prompts rendering as empty strings

### Pitfall 4: Losing Formatting in Folded Blocks
**What goes wrong:** Using `>` (folded) instead of `|` (literal) and losing intentional newlines
**Why it happens:** Folded blocks convert single newlines to spaces
**How to avoid:** Always use literal blocks (`|`) for prompts. Folded (`>`) is for prose, not prompts.
**Warning signs:** Prompt rendered as single long line, formatting instructions lost

### Pitfall 5: Stale Variables Documentation
**What goes wrong:** Comment says "Available: ${x}, ${y}" but code now passes ${z}
**Why it happens:** Documentation falls out of sync with code
**How to avoid:** Generate variable docs from code if possible, or validate at startup
**Warning signs:** Variables listed in comments don't match what gets resolved

## Code Examples

Verified patterns from official sources and existing codebase:

### Loading Prompts at Startup
```python
# Source: Derived from existing loader.py pattern

def load_prompts(config_dir: str) -> PromptConfig:
    """Load and validate prompts configuration.

    Args:
        config_dir: Directory containing prompts.yaml

    Returns:
        Validated PromptConfig

    Raises:
        SystemExit: If prompts.yaml missing or invalid
    """
    prompts_path = Path(config_dir) / "prompts.yaml"

    if not prompts_path.exists():
        logger.error(f"Prompts file not found: {prompts_path}")
        logger.error("  This file contains all LLM prompts for the pipeline.")
        logger.error("  Restore from git or recreate from documentation.")
        sys.exit(1)

    try:
        with open(prompts_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)

        # Resolve env vars only at load time (runtime vars resolved later)
        resolved = _resolve_env_vars(raw_config)

        # Validate schema
        return PromptConfig.model_validate(resolved)

    except yaml.YAMLError as e:
        logger.error(f"YAML parse error in {prompts_path}: {e}")
        sys.exit(1)
    except ValidationError as e:
        logger.error("Prompts validation failed:")
        for err in e.errors():
            loc = '.'.join(str(l) for l in err['loc'])
            logger.error(f"  {loc}: {err['msg']}")
        sys.exit(1)
```

### Prompt Accessor Class
```python
# Source: Original design for this phase

class PromptAccessor:
    """Provides typed access to prompts with runtime variable resolution."""

    def __init__(self, config: PromptConfig):
        self.config = config

    def get_analysis_prompt(
        self,
        category: str,
        prompt_type: str,
        context: Dict[str, Any]
    ) -> str:
        """Get an analysis prompt with variables resolved.

        Args:
            category: 'news', 'research', 'social', 'reddit'
            prompt_type: 'batch_analysis', 'ranking', 'filter', etc.
            context: Runtime variables to substitute

        Returns:
            Resolved prompt string
        """
        analyzer_prompts = getattr(self.config.analysis, category, None)
        if not analyzer_prompts:
            raise ValueError(f"Unknown category: {category}")

        prompt_template = getattr(analyzer_prompts, prompt_type, None)
        if not prompt_template:
            raise ValueError(f"Unknown prompt type: {prompt_type} for {category}")

        return resolve_variables(prompt_template, context)
```

### YAML Literal Block for Prompts
```yaml
# Source: https://yaml-multiline.info/ and derived from codebase analysis

# Use literal block scalar (|) to preserve exact formatting
analysis:
  news:
    batch_analysis: |
      You are an AI news analyst covering the frontier of artificial intelligence.
      Analyze these AI news articles (batch ${batch_index} of ${total_batches}).

      For each article, provide:
      1. A concise summary (2-3 sentences) focusing on what's new/significant
      2. An importance score (0-100) based on FRONTIER AI significance
      3. Brief reasoning for the score
      4. Relevant themes

      Articles:
      ${items_context}

      Return your analysis as JSON:
      ```json
      {
        "items": [
          {"id": "item_id", "summary": "...", "importance_score": 85, "reasoning": "...", "themes": ["theme1"]}
        ]
      }
      ```

# BAD: Folded block (>) would collapse newlines
# BAD: Quoted string would require escape sequences
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded prompts in class constants | External YAML configuration | This phase | Prompts can be modified without code changes |
| Env var only substitution | Dual env + runtime var substitution | This phase | More flexible prompt templating |

**Deprecated/outdated:**
- Class-level prompt constants (e.g., `BATCH_ANALYSIS_PROMPT`): Will be removed, prompts loaded from YAML instead

## Open Questions

Things that couldn't be fully resolved:

1. **Prompt Version Tracking**
   - What we know: User may want to experiment with prompt variants
   - What's unclear: Should we support multiple versions of the same prompt?
   - Recommendation: Out of scope for this phase. Single prompt per key. User can manually version in git.

2. **Thinking Budget in YAML**
   - What we know: Different prompts use different thinking budgets (QUICK/STANDARD/DEEP/ULTRATHINK)
   - What's unclear: Should thinking budget be in prompts.yaml or stay in code?
   - Recommendation: Keep thinking budget in code. It's an implementation detail, not a prompt customization target.

## Inventory of Prompts to Extract

Based on codebase analysis, these prompts need extraction:

### Analyzers (4 files, 4 categories)
| File | Class | Prompt Constant | Purpose |
|------|-------|-----------------|---------|
| `news_analyzer.py` | NewsAnalyzer | `BATCH_ANALYSIS_PROMPT` | Map phase batch analysis |
| `news_analyzer.py` | NewsAnalyzer | `FILTER_PROMPT` | LLM filter for frontier AI |
| `news_analyzer.py` | NewsAnalyzer | `COMBINED_ANALYSIS_PROMPT` | Small batch combined |
| `news_analyzer.py` | NewsAnalyzer | `ANALYSIS_PROMPT` | Legacy (keep for reference) |
| `news_analyzer.py` | NewsAnalyzer | `RANKING_PROMPT` | Reduce phase ranking |
| `research_analyzer.py` | ResearchAnalyzer | `BATCH_ANALYSIS_PROMPT` | Map phase batch analysis |
| `research_analyzer.py` | ResearchAnalyzer | `ANALYSIS_PROMPT` | Legacy (keep for reference) |
| `research_analyzer.py` | ResearchAnalyzer | `RANKING_PROMPT` | Reduce phase ranking |
| `social_analyzer.py` | SocialAnalyzer | `BATCH_ANALYSIS_PROMPT` | Map phase batch analysis |
| `social_analyzer.py` | SocialAnalyzer | `ANALYSIS_PROMPT` | Legacy (keep for reference) |
| `social_analyzer.py` | SocialAnalyzer | `RANKING_PROMPT` | Reduce phase ranking |
| `reddit_analyzer.py` | RedditAnalyzer | `BATCH_ANALYSIS_PROMPT` | Map phase batch analysis |
| `reddit_analyzer.py` | RedditAnalyzer | `ANALYSIS_PROMPT` | Legacy (keep for reference) |
| `reddit_analyzer.py` | RedditAnalyzer | `RANKING_PROMPT` | Reduce phase ranking |

### Orchestrator (1 file)
| File | Location | Variables | Purpose |
|------|----------|-----------|---------|
| `orchestrator.py` | `_detect_cross_category_topics()` | `${context}` | Topic detection |
| `orchestrator.py` | `_generate_executive_summary()` | `${context}` | Executive summary |

### Utility Agents (3 files)
| File | Location | Variables | Purpose |
|------|----------|-----------|---------|
| `link_enricher.py` | `_enrich_text()` | `${date}`, `${items_json}`, `${text}` | Link enrichment |
| `link_follower.py` | `should_follow_link()` | `${url}`, `${post_context}` | Link relevance check |
| `ecosystem_context.py` | `ENRICHMENT_PROMPT` | `${report_date}`, `${coverage_date}`, `${existing_models}`, `${news_items}` | Ecosystem enrichment |

**Total:** ~18 distinct prompts to extract

## Sources

### Primary (HIGH confidence)
- Context7 `/pydantic/pydantic` - Nested model validation, model_validate()
- Context7 `/yaml/pyyaml` - safe_load, loader options
- Existing codebase `agents/config/loader.py` - Proven env var interpolation pattern
- Existing codebase `agents/config/schema.py` - Pydantic validation pattern
- [YAML Multiline Info](https://yaml-multiline.info/) - Block scalar syntax

### Secondary (MEDIUM confidence)
- [Promptfoo Configuration Guide](https://www.promptfoo.dev/docs/configuration/guide/) - Prompt config patterns
- [Microsoft Semantic Kernel YAML Schema](https://learn.microsoft.com/en-us/semantic-kernel/concepts/prompts/yaml-schema) - YAML prompt schema design

### Tertiary (LOW confidence)
- [PDL Medium Article](https://medium.com/@seemabanu1610/mastering-prompt-engineering-with-pdl-the-yaml-based-solution-for-llm-development-bf9245c1cd4b) - YAML-based prompt patterns (blog, not official)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Using existing libraries already in project
- Architecture: HIGH - Extending proven pattern from providers.yaml
- Pitfalls: HIGH - Based on existing codebase analysis and YAML documentation
- Prompt inventory: HIGH - Exhaustive codebase grep

**Research date:** 2026-01-25
**Valid until:** Indefinitely (stable domain, no fast-moving dependencies)
