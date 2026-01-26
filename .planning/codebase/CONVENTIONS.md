# Coding Conventions

**Analysis Date:** 2026-01-24

## Naming Patterns

**Files:**
- Python: snake_case (e.g., `news_gatherer.py`, `llm_client.py`, `base_analyzer.py`)
- TypeScript/Svelte: camelCase for services/stores (e.g., `dataLoader.ts`, `dateStore.ts`)
- Svelte components: PascalCase (e.g., `Header.svelte`, `NewsCard.svelte`, `ThemeToggle.svelte`)
- Config files: lowercase with underscores (e.g., `rss_feeds.txt`, `twitter_accounts.txt`)

**Functions:**
- Python: snake_case (e.g., `async def gather()`, `def parse_date()`, `def build_items_context()`)
- TypeScript: camelCase (e.g., `async function loadIndex()`, `function navigateToDate()`)
- Private methods: prefix with underscore (e.g., `def _gather_all()`, `def _save_result()`)

**Variables:**
- Python: snake_case (e.g., `target_date`, `lookback_hours`, `collected_items`)
- TypeScript: camelCase (e.g., `currentDate`, `availableDates`, `isLoading`)

**Classes:**
- Python: PascalCase (e.g., `NewsGatherer`, `BaseAnalyzer`, `AnthropicClient`, `ThinkingLevel`)
- TypeScript types: PascalCase (e.g., `DataIndex`, `DaySummary`, `CategoryData`)

**Constants:**
- Python: SCREAMING_SNAKE_CASE (e.g., `MODEL_MAX_TOKENS`, `HERO_GENERATOR_AVAILABLE`)
- TypeScript: camelCase for runtime constants (e.g., `const cacheKey`)

## Code Style

**Formatting:**
- No formatter configured (no `.prettierrc`, `black`, or `ruff` config)
- Manual consistency maintained across codebase
- Python: Standard PEP 8 style with 4-space indentation
- TypeScript: Tabs for indentation (per svelte-kit default)
- Line length: Generally kept under 100 chars in Python, flexible in TypeScript

**Linting:**
- No linter configured for Python (no `.flake8`, `pylint`, or `ruff.toml`)
- TypeScript: `svelte-check` used for type checking only
- No ESLint configuration

**Type Annotations:**
- Python: Comprehensive type hints using `typing` module (e.g., `List[CollectedItem]`, `Dict[str, Any]`, `Optional[str]`)
- TypeScript: Strict mode enabled (`"strict": true` in `tsconfig.json`)
- Return types always specified for functions
- Dataclasses used extensively for structured data in Python

## Import Organization

**Python Order:**
1. Standard library (e.g., `import os`, `import json`, `import logging`)
2. Third-party packages (e.g., `import feedparser`, `from dateutil import parser`)
3. Local modules (e.g., `from .base import BaseGatherer`, `from agents import MainOrchestrator`)

**TypeScript Order:**
1. Svelte framework imports (e.g., `from 'svelte/store'`, `from '$app/environment'`)
2. Library imports (e.g., `from '$lib/stores/dateStore'`, `from '$lib/services/dataLoader'`)
3. Component imports (e.g., `import Header from './Header.svelte'`)

**Path Aliases:**
- TypeScript: `$lib` alias maps to `src/lib` (SvelteKit convention)
- TypeScript: `$app` alias for SvelteKit runtime modules
- Python: Relative imports within `agents/` package (e.g., `from .base import ...`)

## Error Handling

**Python Patterns:**
- Try/except blocks wrap external API calls and file I/O
- Errors logged with `logger.error(f"message: {e}", exc_info=True)` for full stack traces
- Graceful degradation: gatherers return empty lists on failure, analyzers return empty reports
- Custom exceptions not used; standard exceptions (ValueError, TypeError) with descriptive messages

**Example from `agents/orchestrator.py`:**
```python
try:
    news_items = await news_gatherer.gather(social_posts=social_posts)
    collection_status['news'] = {'status': 'success', 'count': len(news_items), 'error': None}
except Exception as e:
    logger.error(f"news gatherer failed: {e}")
    results['news'] = []
    collection_status['news'] = {'status': 'failed', 'count': 0, 'error': str(e)}
```

**TypeScript Patterns:**
- Async functions use try/catch for fetch operations
- Errors caught and ignored in background tasks (e.g., `preloadAdjacentDates`)
- Fetch failures throw Error with descriptive messages

## Logging

**Framework:** Python `logging` module (standard library)

**Patterns:**
- Logger initialized per module: `logger = logging.getLogger(__name__)`
- Levels used: INFO (default), DEBUG (detailed tracing), WARNING (non-critical issues), ERROR (failures)
- Format: `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'` (configured in `run_pipeline.py`)
- Structured logging for phases: `logger.info("Phase 1: Gathering from all sources...")`
- Use f-strings for interpolation: `logger.info(f"Collected {len(items)} items")`
- Status symbols in logs: ✓ (success), ⚠ (partial), ✗ (failed), - (skipped)

**Example from `agents/orchestrator.py`:**
```python
logger.info(f"Starting orchestrator run for {self.target_date}")
logger.warning(f"Response truncated at max_tokens ({max_tokens})")
logger.error(f"Cross-category topic detection failed: {e}")
logger.debug(f"Calling with thinking: budget={budget_tokens}")
```

## Comments

**When to Comment:**
- Module docstrings required for all Python files (describing purpose and key classes)
- Class docstrings required (describing responsibilities and usage)
- Method docstrings for public APIs (Args, Returns, Raises sections)
- Inline comments for non-obvious logic (e.g., cache-busting timestamps, date semantics)
- Configuration constants explained inline (e.g., `MODEL_MAX_TOKENS = 64000  # RDSec proxy model limit`)

**JSDoc/TSDoc:**
- Used in TypeScript for function documentation
- Service functions documented with purpose and return types
- Example from `dataLoader.ts`: `/** Load the main data index */`

**Comment Style:**
- Python: Triple-quoted docstrings for modules/classes/functions
- Python: `#` for inline comments
- TypeScript: `/** ... */` for documentation, `//` for inline comments

## Function Design

**Size:**
- Functions generally 20-50 lines
- Large orchestration functions (e.g., `MainOrchestrator.run()`) up to 150 lines
- Complex operations broken into private helper methods (e.g., `_gather_all()`, `_analyze_all()`)

**Parameters:**
- Use dataclasses/TypeScript types for complex parameter sets
- Optional parameters use `Optional[T]` with default `None`
- Configuration passed in constructors, not as function parameters
- Example: `__init__(self, config_dir: str, data_dir: str, lookback_hours: int = 24)`

**Return Values:**
- Explicit return types always specified
- Async functions return `Coroutine[...]` or explicit type
- Multiple values returned as tuples (e.g., `tuple[Dict[str, List[CollectedItem]], Dict[str, Dict[str, Any]]]`)
- Structured data returned as dataclasses (e.g., `CategoryReport`, `OrchestratorResult`)

## Module Design

**Exports:**
- Python: Public classes/functions defined at module level
- Python: Private helpers prefixed with underscore
- TypeScript: Named exports (e.g., `export async function loadIndex()`)
- TypeScript: Type exports (e.g., `export type { DataIndex, DaySummary }`)

**Barrel Files:**
- Python: `__init__.py` used to expose public API (e.g., `agents/__init__.py` exports `MainOrchestrator`)
- TypeScript: No barrel files; explicit imports from source files

## Async/Await Patterns

**Python:**
- Async functions use `async def`
- Parallel operations with `asyncio.gather(*tasks)`
- Event loop management: `asyncio.run(main())` in entry points
- Thread pools for blocking I/O: `ThreadPoolExecutor` for feedparser

**TypeScript:**
- Async functions use `async` keyword
- Promises chained with `await`
- Background tasks fire-and-forget with `.catch(() => {})` (e.g., preloading)

## Data Structures

**Python Dataclasses:**
- Used extensively for structured data
- All dataclasses include `to_dict()` method for JSON serialization
- Fields use `field(default_factory=...)` for mutable defaults
- Example: `CollectedItem`, `CategoryReport`, `OrchestratorResult`

**TypeScript Interfaces:**
- Defined in `frontend/src/lib/types/index.ts`
- All API response shapes typed
- Strict null checking enforced

## Configuration Management

**Environment Variables:**
- Loaded via `python-dotenv` in Python (`.env` file support)
- Accessed via `os.environ.get('VAR_NAME', 'default')`
- Required vars validated in constructors (e.g., `AnthropicClient.__init__`)

**Config Files:**
- Text files in `config/` directory (one entry per line)
- Comments supported with `#` prefix
- Loaded via `BaseGatherer.load_config_list()` helper

---

*Convention analysis: 2026-01-24*
