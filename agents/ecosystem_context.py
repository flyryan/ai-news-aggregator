"""
Ecosystem Context Manager - Pipeline Phase 0

Provides AI ecosystem grounding context to analyzers to prevent hallucinations
about model release dates, capabilities, and current state.

Strategy:
- Load curated model_releases.yaml (GA dates from Wikipedia, API dates from OpenRouter)
- Fetch fresh API dates from OpenRouter for any new models
- Agent enrichment phase fills gaps from daily news
- Builds system prompt with both GA and API dates for LLM context
"""

import json
import logging
import yaml
import aiohttp
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm_client import AsyncAnthropicClient
    from .base import AnalyzedItem
    from .config.prompts import PromptAccessor

logger = logging.getLogger(__name__)


class EcosystemContextManager:
    """
    Manages AI ecosystem state for grounding LLM analysis.

    Fetches current model information from OpenRouter API and builds
    a system prompt that provides temporal and ecosystem awareness
    to prevent the LLM from making incorrect assumptions about
    model release dates and capabilities.
    """

    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
    FETCH_TIMEOUT = 15  # seconds

    # Major providers to track (OpenRouter id prefix -> display name)
    TRACKED_PROVIDERS = {
        'openai': 'OpenAI',
        'anthropic': 'Anthropic',
        'google': 'Google',
        'x-ai': 'xAI',
        'meta-llama': 'Meta',
        'deepseek': 'DeepSeek',
        'qwen': 'Alibaba/Qwen',
        'mistralai': 'Mistral',
        'cohere': 'Cohere',
    }

    # Max models per provider to keep prompt size reasonable
    MAX_MODELS_PER_PROVIDER = 15

    def __init__(self, config_dir: Path, prompt_accessor: Optional['PromptAccessor'] = None):
        """
        Initialize the ecosystem context manager.

        Args:
            config_dir: Path to config directory containing config files
            prompt_accessor: Optional PromptAccessor for config-based prompts.
        """
        self.config_dir = Path(config_dir)
        self.releases_path = self.config_dir / "model_releases.yaml"
        self.cache_path = self.config_dir / "ecosystem_context.yaml"
        self.releases: Dict[str, Dict[str, Dict[str, str]]] = {}  # provider -> model -> {ga_date, api_date}
        self.context: Dict[str, Any] = {}
        self.report_date: Optional[date] = None
        self._system_prompt: Optional[str] = None
        self.prompt_accessor = prompt_accessor

    async def initialize(self, report_date: date) -> str:
        """
        Pipeline Phase 0: Initialize ecosystem context.

        Strategy:
        1. Load curated model_releases.yaml (GA + API dates)
        2. Fetch fresh OpenRouter data to detect new models
        3. Merge: curated dates take precedence, OpenRouter fills gaps
        4. Build system prompt with both GA and API dates

        Args:
            report_date: The report date for this pipeline run

        Returns:
            System prompt string for injection into LLM calls
        """
        self.report_date = report_date

        # Load curated release dates (source of truth)
        self.releases = self._load_releases()
        curated_count = sum(len(models) for models in self.releases.values())
        logger.info(f"Loaded {curated_count} curated model releases")

        # Fetch fresh OpenRouter data to detect new models
        openrouter_data = await self._fetch_from_openrouter()

        if openrouter_data:
            # Merge: curated dates + OpenRouter for gaps
            self.context = self._merge_with_curated(openrouter_data)
            self._save_cache()
            logger.info("Ecosystem context: curated releases + OpenRouter")
        else:
            # Fallback: just use curated data
            self.context = self._curated_to_context()
            logger.warning("Ecosystem context: using curated data only (OpenRouter unavailable)")

        # Build system prompt
        self._system_prompt = self._build_system_prompt()

        model_count = sum(
            len(models) for models in self.context.get('models', {}).values()
        )
        logger.info(f"Ecosystem context ready: {len(self.context.get('models', {}))} providers, {model_count} models")

        return self._system_prompt

    def get_system_prompt(self) -> str:
        """Return pre-built system prompt for injection."""
        if self._system_prompt is None:
            raise RuntimeError("EcosystemContextManager not initialized. Call initialize() first.")
        return self._system_prompt

    def _load_releases(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        """
        Load curated model releases from model_releases.yaml.

        Returns:
            Dict of provider -> model_name -> {ga_date, api_date}
        """
        if not self.releases_path.exists():
            logger.warning(f"Model releases file not found: {self.releases_path}")
            return {}

        try:
            with open(self.releases_path, 'r') as f:
                data = yaml.safe_load(f) or {}
                # Remove any non-provider keys (like comments section)
                return {k: v for k, v in data.items() if isinstance(v, dict) and k in self.TRACKED_PROVIDERS}
        except Exception as e:
            logger.warning(f"Failed to load model releases: {e}")
            return {}

    def _load_cache(self) -> Dict[str, Any]:
        """Load cached ecosystem context from local YAML file."""
        if not self.cache_path.exists():
            logger.warning(f"Ecosystem cache not found: {self.cache_path}")
            return {'metadata': {'last_updated': '1970-01-01'}, 'models': {}}

        with open(self.cache_path, 'r') as f:
            return yaml.safe_load(f) or {}

    def _save_cache(self):
        """Save current context to local YAML cache file."""
        try:
            with open(self.cache_path, 'w') as f:
                yaml.dump(self.context, f, default_flow_style=False, sort_keys=False)
            logger.debug(f"Saved ecosystem context to {self.cache_path}")
        except Exception as e:
            logger.warning(f"Failed to save ecosystem cache: {e}")

    def _merge_with_curated(self, openrouter_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge OpenRouter data with curated releases.

        Strategy:
        - Curated GA dates always take precedence
        - OpenRouter provides API dates (when model became available via API)
        - OpenRouter also provides model discovery (finds new models not in curated list)

        Args:
            openrouter_data: Parsed OpenRouter API response

        Returns:
            Merged context with both GA and API dates
        """
        merged_models: Dict[str, List[Dict[str, Any]]] = {}

        # Start with curated releases
        for provider, models in self.releases.items():
            if provider not in merged_models:
                merged_models[provider] = []

            for model_name, dates in models.items():
                merged_models[provider].append({
                    'name': model_name,
                    'ga_date': dates.get('ga_date', 'unknown'),
                    'api_date': dates.get('api_date', 'unknown'),
                })

        # Add any OpenRouter models not in curated list (new models)
        openrouter_models = openrouter_data.get('models', {})
        for provider, model_list in openrouter_models.items():
            if provider not in merged_models:
                merged_models[provider] = []

            # Get existing model names for this provider (normalized for comparison)
            existing_names = {
                self._normalize_model_name(m['name'])
                for m in merged_models[provider]
            }

            for model in model_list:
                model_name = model.get('name', '')
                normalized_name = self._normalize_model_name(model_name)

                # Skip if we already have this model from curated list
                # Check both exact match and substring containment
                is_duplicate = False
                if normalized_name in existing_names:
                    is_duplicate = True
                else:
                    # Check if any curated name is contained in OpenRouter name
                    # e.g., "claude41" in "claudeopus41"
                    for existing_name in existing_names:
                        if existing_name in normalized_name or normalized_name in existing_name:
                            is_duplicate = True
                            break

                if is_duplicate:
                    continue

                # Add new model from OpenRouter (API date only)
                merged_models[provider].append({
                    'name': model_name,
                    'ga_date': 'unknown',  # Not in curated list
                    'api_date': model.get('released', 'unknown'),
                })

        # Sort each provider's models by GA date (newest first), then API date
        for provider in merged_models:
            merged_models[provider].sort(
                key=lambda m: (
                    m.get('ga_date', '1970-01-01') if m.get('ga_date') != 'unknown' else '1970-01-01',
                    m.get('api_date', '1970-01-01') if m.get('api_date') != 'unknown' else '1970-01-01'
                ),
                reverse=True
            )
            # Cap at max models
            merged_models[provider] = merged_models[provider][:self.MAX_MODELS_PER_PROVIDER]

        return {
            'metadata': {
                'last_updated': datetime.now().strftime('%Y-%m-%d'),
                'schema_version': '3.0',
                'source': 'curated+openrouter'
            },
            'models': merged_models
        }

    def _curated_to_context(self) -> Dict[str, Any]:
        """
        Convert curated releases to context format (fallback when OpenRouter unavailable).

        Returns:
            Context dict with models from curated releases only
        """
        models: Dict[str, List[Dict[str, Any]]] = {}

        for provider, provider_models in self.releases.items():
            models[provider] = []
            for model_name, dates in provider_models.items():
                models[provider].append({
                    'name': model_name,
                    'ga_date': dates.get('ga_date', 'unknown'),
                    'api_date': dates.get('api_date', 'unknown'),
                })

            # Sort by GA date (newest first)
            models[provider].sort(
                key=lambda m: m.get('ga_date', '1970-01-01') if m.get('ga_date') != 'unknown' else '1970-01-01',
                reverse=True
            )

        return {
            'metadata': {
                'last_updated': datetime.now().strftime('%Y-%m-%d'),
                'schema_version': '3.0',
                'source': 'curated'
            },
            'models': models
        }

    def _normalize_model_name(self, name: str) -> str:
        """
        Normalize model name for comparison (lowercase, no special chars).

        Also strips provider prefixes like "OpenAI: " from OpenRouter names.
        """
        # Strip provider prefix if present (e.g., "OpenAI: GPT-5.2" -> "GPT-5.2")
        if ': ' in name:
            name = name.split(': ', 1)[1]
        return name.lower().replace('-', '').replace(' ', '').replace('.', '').replace(':', '')

    def _get_cache_age(self) -> int:
        """Get age of cached config in days."""
        if not self.context or 'metadata' not in self.context:
            return -1
        try:
            last_updated = datetime.fromisoformat(
                self.context['metadata'].get('last_updated', '1970-01-01')
            )
            return (datetime.now() - last_updated).days
        except (ValueError, TypeError):
            return -1

    async def _fetch_from_openrouter(self) -> Optional[Dict[str, Any]]:
        """
        Fetch latest model info from OpenRouter API.

        Returns:
            Structured context dict, or None if fetch fails.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.OPENROUTER_API_URL,
                    timeout=aiohttp.ClientTimeout(total=self.FETCH_TIMEOUT)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"OpenRouter API returned {resp.status}")
                        return None

                    data = await resp.json()
                    return self._parse_openrouter_response(data)

        except aiohttp.ClientError as e:
            logger.warning(f"Failed to fetch from OpenRouter: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error fetching ecosystem context: {e}")
            return None

    def _parse_openrouter_response(self, api_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse OpenRouter API response into flat list per provider.

        Args:
            api_data: Raw API response with 'data' array of models

        Returns:
            Structured context dict with models as flat lists per provider
        """
        # Use dict to dedupe by base model ID (strip :free, :extended suffixes)
        models_by_provider: Dict[str, Dict[str, Dict[str, Any]]] = {}

        for model in api_data.get('data', []):
            model_id = model.get('id', '')
            if '/' not in model_id:
                continue

            # Extract provider from model ID (e.g., "openai/gpt-4" -> "openai")
            provider_key = model_id.split('/')[0].lower()

            # Only track major providers
            if provider_key not in self.TRACKED_PROVIDERS:
                continue

            # Dedupe: strip ":free", ":extended", etc. suffixes
            base_id = model_id.split(':')[0]

            # Skip if we already have this base model (keep first/best one)
            if provider_key in models_by_provider and base_id in models_by_provider[provider_key]:
                continue

            # Parse creation date
            created_ts = model.get('created')
            if created_ts:
                try:
                    released = datetime.fromtimestamp(created_ts).strftime('%Y-%m-%d')
                except (ValueError, TypeError, OSError):
                    released = 'unknown'
            else:
                released = 'unknown'

            model_info = {
                'name': model.get('name', model_id),
                'released': released,
            }

            if provider_key not in models_by_provider:
                models_by_provider[provider_key] = {}
            models_by_provider[provider_key][base_id] = model_info

        # Convert to flat lists, sort by release date (newest first), cap at max
        flat_models = {}
        for provider_key, models_dict in models_by_provider.items():
            models_list = list(models_dict.values())
            # Sort by release date descending (unknown dates go to end)
            models_list.sort(
                key=lambda m: m.get('released', '1970-01-01') if m.get('released') != 'unknown' else '1970-01-01',
                reverse=True
            )
            flat_models[provider_key] = models_list[:self.MAX_MODELS_PER_PROVIDER]

        return {
            'metadata': {
                'last_updated': datetime.now().strftime('%Y-%m-%d'),
                'schema_version': '2.0',
                'source': 'openrouter'
            },
            'models': flat_models
        }

    def _build_system_prompt(self) -> str:
        """
        Build formatted system prompt for LLM injection.

        Shows both GA (General Availability) and API dates when they differ.
        Places grounding context at the START of the prompt (best practice
        per research on "lost in the middle" bias).
        """
        if not self.report_date:
            report_date_str = datetime.now().strftime('%Y-%m-%d')
            coverage_date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            report_date_str = self.report_date.isoformat() if isinstance(self.report_date, date) else str(self.report_date)
            coverage_date = (
                self.report_date - timedelta(days=1)
                if isinstance(self.report_date, date)
                else datetime.fromisoformat(str(self.report_date)).date() - timedelta(days=1)
            )
            coverage_date_str = coverage_date.isoformat()

        lines = [
            "=== AI ECOSYSTEM GROUNDING ===",
            f"Report Date: {report_date_str}",
            f"Coverage Date: {coverage_date_str} (news is from this date)",
            "",
            "## Date Types",
            "- GA date: General Availability - when model was publicly announced/released",
            "- API date: When model became available via public APIs (OpenRouter, etc.)",
            "",
            "## Current Models by Provider",
            ""
        ]

        models = self.context.get('models', {})
        for provider_key, model_list in models.items():
            provider_name = self.TRACKED_PROVIDERS.get(provider_key, provider_key.title())
            lines.append(f"### {provider_name}")

            if isinstance(model_list, list):
                for model_info in model_list:
                    name = model_info.get('name', 'Unknown')
                    ga_date = model_info.get('ga_date', 'unknown')
                    api_date = model_info.get('api_date', 'unknown')

                    # Format based on what dates we have
                    if ga_date != 'unknown' and api_date != 'unknown':
                        if ga_date == api_date:
                            lines.append(f"- {name} (released {ga_date})")
                        else:
                            lines.append(f"- {name} (GA: {ga_date}, API: {api_date})")
                    elif ga_date != 'unknown':
                        lines.append(f"- {name} (GA: {ga_date})")
                    elif api_date != 'unknown':
                        lines.append(f"- {name} (API: {api_date})")
                    else:
                        lines.append(f"- {name} (date unknown)")
            lines.append("")

        lines.extend([
            "## How to Use This",
            f"Compare model release dates to coverage date ({coverage_date_str}):",
            "- GA date on/near coverage date → could be release announcement",
            "- GA date weeks/months before → news ABOUT existing model, NOT a release",
            "- API date may differ from GA date (some models available in apps before APIs)",
        ])

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Agent Enrichment Phase
    # -------------------------------------------------------------------------

    ENRICHMENT_PROMPT = """You are an AI model release analyst. Review today's news items and identify any NEW model releases that should be added to our tracking database.

Current date: {report_date}
Coverage date: {coverage_date} (news is from this date)

## Models Already Tracked
{existing_models}

## Today's News Items
{news_items}

## Task
Identify any NEW model releases announced in today's news that are NOT already in our tracked list.

For each new release found, extract:
1. provider: The company (openai, anthropic, google, x-ai, meta-llama, deepseek, mistralai)
2. model_name: The model name (e.g., "GPT-5.3", "Claude-5", "Gemini-4")
3. ga_date: The release/announcement date (YYYY-MM-DD format, use coverage date if "today")
4. confidence: How confident you are this is a genuine new release (high/medium/low)
5. source: Brief note about which news item mentions this

Return JSON:
```json
{{
  "new_releases": [
    {{
      "provider": "openai",
      "model_name": "GPT-5.3",
      "ga_date": "2026-01-14",
      "confidence": "high",
      "source": "OpenAI blog announcement"
    }}
  ],
  "notes": "Any observations about model releases in today's news"
}}
```

IMPORTANT:
- Only include GENUINE new model releases (not updates, features, or news about existing models)
- Do NOT include models already in our tracked list
- Be conservative - only high/medium confidence releases should be added
- If no new releases found, return empty array"""

    async def enrich_from_news(
        self,
        news_items: List['AnalyzedItem'],
        llm_client: 'AsyncAnthropicClient'
    ) -> Dict[str, Any]:
        """
        Pipeline Phase 4.8: Enrich model releases from daily news.

        Analyzes today's news to detect new model releases and updates
        model_releases.yaml with any discoveries.

        Args:
            news_items: Analyzed news items from today
            llm_client: Async Anthropic client for LLM calls

        Returns:
            Dict with enrichment results (new releases found, updates made)
        """
        if not news_items:
            logger.info("No news items for enrichment")
            return {"new_releases": [], "updates_made": 0}

        # Build context for LLM
        existing_models = self._format_existing_models()
        news_context = self._format_news_for_enrichment(news_items)

        coverage_date = (
            self.report_date - timedelta(days=1)
            if self.report_date else date.today() - timedelta(days=1)
        )

        if self.prompt_accessor:
            prompt = self.prompt_accessor.get_post_processing_prompt(
                'ecosystem_enrichment',
                {
                    'report_date': self.report_date.isoformat() if self.report_date else date.today().isoformat(),
                    'coverage_date': coverage_date.isoformat(),
                    'existing_models': existing_models,
                    'news_items': news_context
                }
            )
        else:
            # Fallback to class constant for backwards compatibility
            prompt = self.ENRICHMENT_PROMPT.format(
                report_date=self.report_date.isoformat() if self.report_date else date.today().isoformat(),
                coverage_date=coverage_date.isoformat(),
                existing_models=existing_models,
                news_items=news_context
            )

        try:
            # Use LLM to analyze news for releases
            from .llm_client import ThinkingLevel
            response = await llm_client.call_with_thinking(
                prompt=prompt,
                thinking_budget=ThinkingLevel.STANDARD
            )

            # Parse response
            result = self._parse_enrichment_response(response.get('text', ''))

            if result.get('new_releases'):
                updates = self._apply_enrichment(result['new_releases'], coverage_date)
                result['updates_made'] = updates
                if updates > 0:
                    logger.info(f"Enrichment: Added {updates} new model releases")
            else:
                result['updates_made'] = 0
                logger.info("Enrichment: No new model releases detected")

            return result

        except Exception as e:
            logger.warning(f"Enrichment failed: {e}")
            return {"new_releases": [], "updates_made": 0, "error": str(e)}

    def _format_existing_models(self) -> str:
        """Format existing models for the enrichment prompt."""
        lines = []
        for provider, models in self.releases.items():
            provider_name = self.TRACKED_PROVIDERS.get(provider, provider)
            lines.append(f"\n### {provider_name}")
            for model_name in models.keys():
                lines.append(f"- {model_name}")
        return "\n".join(lines)

    def _format_news_for_enrichment(self, items: List['AnalyzedItem']) -> str:
        """Format news items for the enrichment prompt."""
        lines = []
        for i, item in enumerate(items[:30], 1):  # Limit to top 30
            lines.append(f"\n--- Item {i} ---")
            lines.append(f"Title: {item.title}")
            lines.append(f"Summary: {item.summary}")
            if item.themes:
                lines.append(f"Themes: {', '.join(item.themes)}")
        return "\n".join(lines)

    def _parse_enrichment_response(self, response_text: str) -> Dict[str, Any]:
        """Parse LLM response for enrichment results."""
        try:
            # Extract JSON from response
            if '```json' in response_text:
                json_str = response_text.split('```json')[1].split('```')[0]
            elif '```' in response_text:
                json_str = response_text.split('```')[1].split('```')[0]
            else:
                json_str = response_text

            result = json.loads(json_str.strip())

            # Filter to only high/medium confidence
            if 'new_releases' in result:
                result['new_releases'] = [
                    r for r in result['new_releases']
                    if r.get('confidence', 'low') in ('high', 'medium')
                ]

            return result

        except (json.JSONDecodeError, IndexError) as e:
            logger.warning(f"Failed to parse enrichment response: {e}")
            return {"new_releases": [], "parse_error": str(e)}

    def _apply_enrichment(self, new_releases: List[Dict], coverage_date: date) -> int:
        """
        Apply discovered releases to model_releases.yaml.

        Args:
            new_releases: List of new release dicts from LLM
            coverage_date: The coverage date for default GA date

        Returns:
            Number of updates made
        """
        if not new_releases:
            return 0

        updates = 0

        for release in new_releases:
            provider = release.get('provider', '').lower()
            model_name = release.get('model_name', '')
            ga_date = release.get('ga_date', coverage_date.isoformat())

            # Validate provider
            if provider not in self.TRACKED_PROVIDERS:
                logger.debug(f"Skipping unknown provider: {provider}")
                continue

            # Skip if already exists
            if provider in self.releases and model_name in self.releases[provider]:
                logger.debug(f"Skipping existing model: {model_name}")
                continue

            # Add to releases
            if provider not in self.releases:
                self.releases[provider] = {}

            self.releases[provider][model_name] = {
                'ga_date': ga_date,
                'api_date': 'unknown'  # Will be filled by OpenRouter on next run
            }
            updates += 1
            logger.info(f"Added new model: {provider}/{model_name} (GA: {ga_date})")

        # Save updated releases
        if updates > 0:
            self._save_releases()

        return updates

    def _save_releases(self):
        """Save updated releases back to model_releases.yaml."""
        try:
            # Load existing file to preserve comments and structure
            if self.releases_path.exists():
                with open(self.releases_path, 'r') as f:
                    content = f.read()
                    # Find where the data starts (after comments)
                    lines = content.split('\n')
                    header_lines = []
                    for line in lines:
                        if line.startswith('#') or line.strip() == '':
                            header_lines.append(line)
                        else:
                            break
                    header = '\n'.join(header_lines)
            else:
                header = "# Model Release Dates - Source of Truth\n"

            # Update the "Last verified" date in header
            today = date.today().isoformat()
            if 'Last verified:' in header:
                import re
                header = re.sub(
                    r'Last verified: \d{4}-\d{2}-\d{2}',
                    f'Last verified: {today}',
                    header
                )

            # Write back with header preserved
            with open(self.releases_path, 'w') as f:
                f.write(header)
                if not header.endswith('\n\n'):
                    f.write('\n')
                yaml.dump(self.releases, f, default_flow_style=False, sort_keys=False)

            logger.debug(f"Saved releases to {self.releases_path}")

        except Exception as e:
            logger.error(f"Failed to save releases: {e}")
