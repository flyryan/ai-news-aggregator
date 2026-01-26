#!/usr/bin/env python3
"""
Hero Image Generator

Generates daily hero images with the AATF skunk mascot via configured image provider.
The mascot is placed in topic-related scenes based on the day's top topics.

Supports two initialization modes:
1. New: HeroGenerator.from_config(config) - uses unified ImageClient abstraction
2. Legacy: HeroGenerator(api_key, endpoint, model) - backwards compatible, deprecated
"""

import os
import re
import logging
import warnings
from pathlib import Path
from typing import Optional, List, Any, Dict, TYPE_CHECKING

from generators.image_optimizer import optimize_hero_image

if TYPE_CHECKING:
    from agents.config import ImageProviderConfig
    from generators.image_client import BaseImageClient

logger = logging.getLogger(__name__)


class HeroGenerator:
    """Generates daily hero images with skunk mascot via configured image provider."""

    SKUNK_REFERENCE = Path(__file__).parent.parent / "assets" / "skunk-reference.png"

    # Topic-to-visual mapping for scene generation
    VISUAL_MAPPINGS = {
        "infrastructure": "server racks, cooling systems, blue LED glow, data center",
        "datacenter": "server racks, cooling systems, blue LED glow",
        "safety": "shield icons, protective barriers, guardrails",
        "alignment": "scales of balance, alignment targets",
        "research": "floating papers, neural network diagrams, lab setting",
        "papers": "scientific documents, equations, research environment",
        "robotics": "robot arms, mechanical components, factory setting",
        "model": "neural network visualization, glowing nodes, architecture",
        "release": "rocket launch, celebration confetti, announcement banners",
        "regulation": "gavel, scales of justice, official documents",
        "funding": "growth charts, money symbols, investment visuals",
        "multimodal": "eyes, cameras, sound waves, multiple sensory inputs",
        "agent": "autonomous systems, workflow diagrams, connected tools",
        "open source": "connected nodes, community gathering, collaboration",
        "reasoning": "thought bubbles, chain of logic, decision trees",
        "benchmark": "performance charts, comparison graphs, trophy",
        "language": "floating text, speech bubbles, translation symbols",
        "vision": "camera lens, image processing, visual recognition",
        "code": "terminal screens, code snippets, developer workspace",
        "security": "locks, shields, firewall barriers, protection symbols",
        "training": "compute clusters, gradient flows, learning curves",
        "deployment": "cloud infrastructure, scaling arrows, production systems",
    }

    def __init__(
        self,
        client: Optional['BaseImageClient'] = None,
        # Legacy parameters (deprecated)
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None
    ):
        """
        Initialize hero generator.

        Preferred: Use from_config() classmethod for config-based initialization.

        Args:
            client: ImageClient instance (preferred, new pattern)
            api_key: DEPRECATED - API key for legacy mode. Use from_config() instead.
            endpoint: DEPRECATED - API endpoint for legacy mode.
            model: DEPRECATED - Model name for legacy mode.
        """
        if client is not None:
            # New pattern: use provided ImageClient
            self.client = client
            logger.info("HeroGenerator initialized with ImageClient")
        elif api_key is not None:
            # Legacy pattern: create OpenAICompatibleClient internally
            warnings.warn(
                "Passing api_key, endpoint, model to HeroGenerator() is deprecated. "
                "Use HeroGenerator.from_config(config) instead.",
                DeprecationWarning,
                stacklevel=2
            )
            from generators.image_client import OpenAICompatibleClient

            # Legacy defaults
            legacy_endpoint = endpoint
            legacy_model = model or "gemini-3-pro-image"

            self.client = OpenAICompatibleClient(
                api_key=api_key,
                endpoint=legacy_endpoint,
                model=legacy_model
            )
            logger.info(f"HeroGenerator initialized in legacy mode (deprecated)")
        else:
            raise ValueError(
                "HeroGenerator requires an ImageClient. Use HeroGenerator.from_config(config) "
                "or pass client parameter directly."
            )

        # Verify skunk reference exists
        if not self.SKUNK_REFERENCE.exists():
            raise FileNotFoundError(f"Skunk reference image not found at {self.SKUNK_REFERENCE}")

    @classmethod
    def from_config(cls, config: 'ImageProviderConfig') -> 'HeroGenerator':
        """
        Create hero generator from ImageProviderConfig.

        Args:
            config: ImageProviderConfig with api_key, endpoint, model, mode

        Returns:
            Configured HeroGenerator instance

        Raises:
            ValueError: If ImageClient creation fails
        """
        from generators.image_client import ImageClient

        try:
            client = ImageClient.from_config(config)
        except ValueError as e:
            # Add mode-specific troubleshooting guidance
            if config.mode == "native":
                raise ValueError(
                    f"{e}\n\n"
                    f"Troubleshooting (native mode):\n"
                    f"- Verify your GOOGLE_API_KEY is valid\n"
                    f"- Ensure google-genai SDK is installed: pip install google-genai\n"
                    f"- Check that your API key has access to image generation models"
                ) from e
            else:
                raise ValueError(
                    f"{e}\n\n"
                    f"Troubleshooting (openai-compatible mode):\n"
                    f"- Verify your endpoint URL is correct: {config.endpoint}\n"
                    f"- Check that your api_key has proper permissions\n"
                    f"- Ensure the proxy supports image generation"
                ) from e

        return cls(client=client)

    def _extract_visuals(self, topics: List[Any]) -> List[str]:
        """Extract visual elements from topic names."""
        visuals = []
        for topic in topics:
            name_lower = topic.name.lower() if hasattr(topic, 'name') else str(topic).lower()
            for keyword, visual in self.VISUAL_MAPPINGS.items():
                if keyword in name_lower:
                    visuals.append(visual)
                    break
        return visuals if visuals else ["abstract AI visualization, neural networks, data flow patterns"]

    def _get_topic_names(self, topics: List[Any]) -> List[str]:
        """Extract topic names from topic objects."""
        names = []
        for topic in topics:
            if hasattr(topic, 'name'):
                names.append(topic.name)
            elif isinstance(topic, dict) and 'name' in topic:
                names.append(topic['name'])
            else:
                names.append(str(topic))
        return names

    def _strip_markdown_links(self, text: str) -> str:
        """Strip markdown link syntax, keeping just the link text."""
        # Convert [text](url) to just text
        return re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    def _get_topic_summaries(self, topics: List[Any]) -> List[Dict[str, str]]:
        """Extract topic names and clean descriptions for prompt context."""
        summaries = []
        for topic in topics:
            name = ""
            description = ""

            if hasattr(topic, 'name'):
                name = topic.name
            elif isinstance(topic, dict) and 'name' in topic:
                name = topic['name']
            else:
                name = str(topic)

            if hasattr(topic, 'description'):
                description = self._strip_markdown_links(topic.description)
            elif isinstance(topic, dict) and 'description' in topic:
                description = self._strip_markdown_links(topic['description'])

            summaries.append({"name": name, "description": description})

        return summaries

    def _build_prompt(self, topic_summaries: List[Dict[str, str]], visual_elements: List[str]) -> str:
        """Build the image generation prompt from topics and visuals."""
        # Build topic sections for prompt
        topic_sections = []
        for i, summary in enumerate(topic_summaries, 1):
            section = f"**Topic {i}: {summary['name']}**"
            if summary['description']:
                section += f"\n{summary['description']}"
            topic_sections.append(section)

        return f"""You are generating a daily hero image for an AI news aggregator website.

## Your Goal
Create a playful, colorful editorial illustration that visually represents today's top AI news stories. The scene should immediately convey the themes of the day's news to readers.

## The Mascot (CRITICAL)
The attached image shows our skunk mascot. You MUST:
- Keep the EXACT circuit board pattern on the skunk's body and tail - this is a core part of the brand identity
- Maintain the skunk's white and black coloring with the tech circuit pattern visible
- The skunk must be ACTIVELY DOING SOMETHING related to the topics - typing on a keyboard, reading papers, adjusting equipment, pointing at a screen, holding tools, etc. NOT just standing and smiling at the camera!
- Position the skunk in the lower-left or lower-right portion, engaged with the scene

## Today's Stories

{chr(10).join(topic_sections)}

## Visual Direction
Create a scene that represents these stories. You must include Topic 1 (the top story), then pick 2-3 others that would make the best scene together. Consider:
- What visual metaphors could represent these themes?
- How can the skunk mascot interact with or observe these elements?
- Suggested scene elements: {', '.join(visual_elements)}

## Style Requirements
- Playful cartoon illustration, tech editorial art style
- Vibrant colors with Trend Red (#E63946) accents
- Energetic, forward-looking, tech-optimistic mood
- No company logos or watermarks - but topic-relevant company logos (OpenAI, Anthropic, Google, etc.) are encouraged when relevant to the stories"""

    async def generate(
        self,
        top_topics: List[Any],
        date: str,
        output_dir: Path,
        custom_prompt: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Generate hero image based on top topics.

        Args:
            top_topics: List of TopTopic objects or dicts with topic info
            date: Date string (YYYY-MM-DD) for output path
            output_dir: Base output directory for web data
            custom_prompt: Optional custom prompt to override auto-generated prompt

        Returns:
            Dict with 'path' (relative URL path) and 'prompt' (used prompt), or None on failure
        """
        # Read skunk reference image
        try:
            with open(self.SKUNK_REFERENCE, "rb") as f:
                skunk_bytes = f.read()
        except Exception as e:
            logger.error(f"Failed to read skunk reference image: {e}")
            return None

        # Extract visual elements and topic summaries from all available topics
        visual_elements = self._extract_visuals(top_topics)
        topic_summaries = self._get_topic_summaries(top_topics)

        # Build prompt
        if custom_prompt:
            instructions = custom_prompt
        else:
            instructions = self._build_prompt(topic_summaries, visual_elements)

        topic_names = [s['name'] for s in topic_summaries]
        logger.info(f"Generating hero image for {date} with topics: {topic_names}")

        try:
            # Use ImageClient for generation
            response = await self.client.generate(
                prompt=instructions,
                reference_image=skunk_bytes,
                aspect_ratio="21:9",
                image_size="2K"
            )

            # Save raw image from API
            png_path = output_dir / "data" / date / "hero.png"
            png_path.parent.mkdir(parents=True, exist_ok=True)

            with open(png_path, "wb") as f:
                f.write(response.image_data)

            # Optimize to compressed WebP
            webp_path = optimize_hero_image(png_path)
            png_path.unlink()  # Remove original PNG

            # Return relative URL path for web serving
            relative_url = f"/data/{date}/hero.webp"

            logger.info(f"Hero image generated and optimized: {webp_path}")

            return {
                "path": relative_url,
                "prompt": instructions
            }

        except RuntimeError as e:
            # ImageClient raises RuntimeError for API errors
            logger.error(f"Hero image generation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Hero image generation failed unexpectedly: {e}")
            return None

    def generate_sync(
        self,
        top_topics: List[Any],
        date: str,
        output_dir: Path,
        custom_prompt: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Synchronous wrapper for generate() for use in non-async contexts.
        """
        import asyncio

        # Create event loop if needed
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, need to use different approach
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.generate(top_topics, date, output_dir, custom_prompt)
                )
                return future.result()
        except RuntimeError:
            # No running loop, create one
            return asyncio.run(self.generate(top_topics, date, output_dir, custom_prompt))

    async def edit(
        self,
        existing_image_path: Path,
        edit_instructions: str,
        date: str,
        output_dir: Path
    ) -> Optional[Dict[str, str]]:
        """
        Edit an existing hero image with specific changes.

        Args:
            existing_image_path: Path to the existing hero image
            edit_instructions: What to change in the image
            date: Date string (YYYY-MM-DD) for output path
            output_dir: Base output directory for web data

        Returns:
            Dict with 'path' (relative URL path) and 'prompt' (used prompt), or None on failure
        """
        # Read existing hero image
        try:
            with open(existing_image_path, "rb") as f:
                hero_bytes = f.read()
        except Exception as e:
            logger.error(f"Failed to read existing hero image: {e}")
            return None

        # Build edit prompt
        instructions = f"""You are editing an existing hero image. The attached image is the current version which is GOOD.

DO NOT regenerate the entire image. Make ONLY the following specific change:

{edit_instructions}

IMPORTANT:
- Keep the overall composition, style, and colors the same
- Preserve everything else exactly as it appears
- Only modify what is explicitly requested above
- The result should look like a minor edit, not a new image"""

        logger.info(f"Editing hero image for {date}: {edit_instructions[:50]}...")

        try:
            # Use ImageClient for generation (edit is just generate with edit prompt and reference)
            response = await self.client.generate(
                prompt=instructions,
                reference_image=hero_bytes,
                aspect_ratio="21:9",
                image_size="2K"
            )

            # Save raw image from API
            png_path = output_dir / "data" / date / "hero.png"
            png_path.parent.mkdir(parents=True, exist_ok=True)

            with open(png_path, "wb") as f:
                f.write(response.image_data)

            # Optimize to compressed WebP
            webp_path = optimize_hero_image(png_path)
            png_path.unlink()  # Remove original PNG

            # Return relative URL path for web serving
            relative_url = f"/data/{date}/hero.webp"

            logger.info(f"Hero image edited and optimized: {webp_path}")

            return {
                "path": relative_url,
                "prompt": instructions
            }

        except RuntimeError as e:
            logger.error(f"Hero image edit failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Hero image edit failed unexpectedly: {e}")
            return None

    def edit_sync(
        self,
        existing_image_path: Path,
        edit_instructions: str,
        date: str,
        output_dir: Path
    ) -> Optional[Dict[str, str]]:
        """
        Synchronous wrapper for edit() for use in non-async contexts.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.edit(existing_image_path, edit_instructions, date, output_dir)
                )
                return future.result()
        except RuntimeError:
            return asyncio.run(self.edit(existing_image_path, edit_instructions, date, output_dir))


def initialize_hero_generator(config: Optional['ImageProviderConfig']) -> Optional['HeroGenerator']:
    """
    Initialize HeroGenerator from config, returning None if not configured.

    This is the preferred entry point for pipeline code. It handles missing
    configuration gracefully with clear warning messages.

    Args:
        config: ImageProviderConfig or None

    Returns:
        HeroGenerator if configured, None otherwise (with warning logged)

    Note:
        When this returns None, the pipeline should:
        - Set hero_image_url to null in summary.json
        - Set hero_image_prompt to null in summary.json
        - Continue without hero images
    """
    if config is None:
        logger.warning(
            "Hero image generation disabled: no 'image' section in providers.yaml. "
            "To enable, add image provider config. You can run "
            "scripts/regenerate_hero.py later to generate images."
        )
        return None

    try:
        return HeroGenerator.from_config(config)
    except ValueError as e:
        logger.warning(
            f"Hero image generation disabled: {e}. "
            "Check your image provider configuration in providers.yaml."
        )
        return None
    except Exception as e:
        logger.warning(
            f"Hero image generation disabled due to initialization error: {e}. "
            "Pipeline will continue without hero images."
        )
        return None


if __name__ == "__main__":
    import sys
    import argparse

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="Generate hero image for a date")
    parser.add_argument("date", help="Date in YYYY-MM-DD format")
    parser.add_argument("--output-dir", default="./web", help="Output directory")
    parser.add_argument("--prompt", help="Custom prompt override")
    args = parser.parse_args()

    # Mock topics for testing
    mock_topics = [
        {"name": "AI Infrastructure Investments"},
        {"name": "Reasoning Model Advances"},
        {"name": "Open Source LLMs"}
    ]

    # Try to load config, fall back to legacy mode for testing
    try:
        from agents.config import load_config
        config = load_config("./config")
        if config.image:
            generator = HeroGenerator.from_config(config.image)
        else:
            print("No image config found, cannot generate hero image")
            sys.exit(1)
    except Exception as e:
        print(f"Config load failed: {e}")
        print("Falling back to legacy mode (deprecated)")
        generator = HeroGenerator()

    result = generator.generate_sync(
        mock_topics,
        args.date,
        Path(args.output_dir),
        args.prompt
    )

    if result:
        print(f"Generated hero image: {result['path']}")
    else:
        print("Failed to generate hero image")
        sys.exit(1)
