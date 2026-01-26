#!/usr/bin/env python3
"""
Regenerate Hero Image

Regenerate hero images for specific dates or all dates.

Usage:
    python3 scripts/regenerate_hero.py                    # Prompts for today's date
    python3 scripts/regenerate_hero.py -y                 # Today's date without prompt
    python3 scripts/regenerate_hero.py 2026-01-06         # Specific date
    python3 scripts/regenerate_hero.py 2026-01-06 --prompt "Custom scene"
    python3 scripts/regenerate_hero.py -a                 # All dates
    python3 scripts/regenerate_hero.py -a -s 2026-01-06   # All except one date
    python3 scripts/regenerate_hero.py -a -s 2026-01-05:2026-01-08  # Skip a range
    python3 scripts/regenerate_hero.py -a -s 2026-01-01,2026-01-05:2026-01-07  # Mixed
    python3 scripts/regenerate_hero.py -a -t 4            # 4 parallel threads
    python3 scripts/regenerate_hero.py 2026-01-06 -e "Add a coffee cup"  # Edit existing

Configuration:
    Requires image provider configuration in config/providers.yaml:

    image:
      mode: native  # or openai-compatible
      api_key: ${GOOGLE_API_KEY}  # or your image API key
      model: gemini-3-pro-image-preview  # optional, default model

    See config/providers.example.yaml for full examples.
"""

import argparse
import asyncio
import json
import logging
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from generators.hero_generator import HeroGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_image_config():
    """
    Load image provider configuration from providers.yaml.

    Returns:
        ImageProviderConfig if configured, None otherwise

    Raises:
        SystemExit: If config is missing or invalid
    """
    from agents.config import load_config
    from agents.config.schema import ImageProviderConfig

    config_dir = str(project_root / "config")
    providers_yaml = project_root / "config" / "providers.yaml"

    # Check if providers.yaml exists
    if not providers_yaml.exists():
        example_yaml = project_root / "config" / "providers.example.yaml"
        if example_yaml.exists():
            logger.error(
                f"No providers.yaml found at {providers_yaml}.\n"
                f"Create one by copying the example:\n"
                f"  cp {example_yaml} {providers_yaml}\n"
                f"Then edit to add your image provider API key."
            )
        else:
            logger.error(
                f"No providers.yaml found at {providers_yaml}.\n"
                f"Create one with image provider configuration:\n\n"
                f"image:\n"
                f"  mode: native  # or openai-compatible\n"
                f"  api_key: ${{GOOGLE_API_KEY}}\n"
                f"  model: gemini-3-pro-image-preview\n"
            )
        sys.exit(1)

    try:
        provider_config = load_config(config_dir)
    except Exception as e:
        logger.error(f"Failed to load config from {config_dir}: {e}")
        sys.exit(1)

    if provider_config.image is None:
        logger.error(
            "No image provider configured in config/providers.yaml.\n"
            "Add an 'image' section with your configuration:\n\n"
            "image:\n"
            "  mode: native  # Use 'native' for Google API, 'openai-compatible' for proxies\n"
            "  api_key: ${GOOGLE_API_KEY}  # Or your image API key\n"
            "  model: gemini-3-pro-image-preview  # Optional\n"
        )
        sys.exit(1)

    return provider_config.image


def initialize_generator() -> HeroGenerator:
    """
    Initialize HeroGenerator from configuration.

    Returns:
        Configured HeroGenerator

    Raises:
        SystemExit: If initialization fails
    """
    image_config = load_image_config()

    try:
        return HeroGenerator.from_config(image_config)
    except ValueError as e:
        logger.error(f"Failed to initialize hero generator: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error initializing hero generator: {e}")
        sys.exit(1)


def parse_skip_dates(skip_str: str) -> set[str]:
    """
    Parse skip dates string into a set of date strings.

    Supports:
        - Single dates: 2026-01-05
        - Comma-separated: 2026-01-05,2026-01-06
        - Ranges (inclusive): 2026-01-05:2026-01-08
        - Mixed: 2026-01-01:2026-01-03,2026-01-07
    """
    if not skip_str:
        return set()

    dates = set()
    parts = skip_str.split(',')

    for part in parts:
        part = part.strip()
        if ':' in part:
            # Range
            start_str, end_str = part.split(':', 1)
            try:
                start = datetime.strptime(start_str.strip(), '%Y-%m-%d')
                end = datetime.strptime(end_str.strip(), '%Y-%m-%d')
                current = start
                while current <= end:
                    dates.add(current.strftime('%Y-%m-%d'))
                    current += timedelta(days=1)
            except ValueError as e:
                logger.warning(f"Invalid date range '{part}': {e}")
        else:
            # Single date
            dates.add(part)

    return dates


def get_all_dates(web_dir: Path) -> list[str]:
    """Get all date directories from web/data that have summary.json."""
    data_dir = web_dir / "data"
    dates = []
    for item in data_dir.iterdir():
        if item.is_dir() and len(item.name) == 10 and item.name[4] == '-':
            if (item / "summary.json").exists():
                dates.append(item.name)
    return sorted(dates)


def load_summary(web_dir: Path, date: str) -> dict:
    """Load existing summary.json for the date."""
    summary_path = web_dir / "data" / date / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary not found: {summary_path}")

    with open(summary_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_summary(web_dir: Path, date: str, summary: dict) -> None:
    """Save updated summary.json."""
    summary_path = web_dir / "data" / date / "summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info(f"Updated summary.json at {summary_path}")


def regenerate_one(generator: HeroGenerator, web_dir: Path, date: str) -> tuple[str, bool, str]:
    """Regenerate hero image for a single date (thread-safe). Returns (date, success, message)."""
    try:
        logger.info(f"Starting: {date}")
        summary = load_summary(web_dir, date)
        top_topics = summary.get('top_topics', [])

        if not top_topics:
            return (date, False, "No top topics found")

        result = generator.generate_sync(
            top_topics=top_topics,
            date=date,
            output_dir=web_dir
        )

        if not result:
            return (date, False, "Generation returned None")

        # Update summary with cache-busting URL
        hero_file = web_dir / "data" / date / "hero.webp"
        hero_url = result['path']
        if hero_file.exists():
            mtime = int(hero_file.stat().st_mtime)
            hero_url = f"{hero_url}?v={mtime}"
        summary['hero_image_url'] = hero_url
        summary['hero_image_prompt'] = result['prompt']
        save_summary(web_dir, date, summary)

        logger.info(f"Completed: {date}")
        return (date, True, "Success")

    except Exception as e:
        logger.error(f"Failed: {date} - {e}")
        return (date, False, str(e))


async def regenerate_single(generator: HeroGenerator, web_dir: Path, date: str, custom_prompt: str = None) -> bool:
    """Regenerate hero image for a single date using async."""
    try:
        summary = load_summary(web_dir, date)
    except FileNotFoundError as e:
        logger.error(f"Summary not found: {e}")
        logger.error("Run the pipeline first to generate data for this date.")
        return False

    top_topics = summary.get('top_topics', [])
    if not top_topics:
        logger.error("No top topics found in summary - cannot generate hero image")
        return False

    logger.info(f"Found {len(top_topics)} topics: {[t.get('name', 'unknown') for t in top_topics[:3]]}")
    logger.info("Generating new hero image...")

    result = await generator.generate(
        top_topics=top_topics,
        date=date,
        output_dir=web_dir,
        custom_prompt=custom_prompt
    )

    if not result:
        logger.error("Hero image generation failed")
        return False

    # Update summary with new hero image path (with cache-busting)
    hero_file = web_dir / "data" / date / "hero.webp"
    hero_url = result['path']
    if hero_file.exists():
        mtime = int(hero_file.stat().st_mtime)
        hero_url = f"{hero_url}?v={mtime}"
    summary['hero_image_url'] = hero_url
    summary['hero_image_prompt'] = result['prompt']
    save_summary(web_dir, date, summary)

    logger.info(f"Success! Hero image generated at: {result['path']}")
    return True


async def edit_single(generator: HeroGenerator, web_dir: Path, date: str, edit_instructions: str) -> bool:
    """Edit an existing hero image for a single date."""
    hero_file = web_dir / "data" / date / "hero.webp"

    if not hero_file.exists():
        logger.error(f"No hero image found for {date} - nothing to edit")
        logger.error(f"Expected: {hero_file}")
        return False

    # Backup original
    backup_file = web_dir / "data" / date / "hero.backup.webp"
    shutil.copy2(hero_file, backup_file)
    logger.info(f"Original backed up to {backup_file}")

    logger.info(f"Editing hero image: {edit_instructions}")

    result = await generator.edit(
        existing_image_path=hero_file,
        edit_instructions=edit_instructions,
        date=date,
        output_dir=web_dir
    )

    if not result:
        logger.error("Hero image edit failed")
        return False

    # Update summary with new hero image path (with cache-busting)
    try:
        summary = load_summary(web_dir, date)
        hero_url = result['path']
        if hero_file.exists():
            mtime = int(hero_file.stat().st_mtime)
            hero_url = f"{hero_url}?v={mtime}"
        summary['hero_image_url'] = hero_url
        summary['hero_image_prompt'] = result['prompt']
        save_summary(web_dir, date, summary)
    except FileNotFoundError:
        logger.warning("No summary.json found - skipping summary update")

    logger.info(f"Success! Hero image edited at: {result['path']}")
    return True


def regenerate_all(generator: HeroGenerator, web_dir: Path, skip_dates: set[str], max_parallel: int) -> None:
    """Regenerate hero images for all dates in parallel."""
    all_dates = get_all_dates(web_dir)
    dates_to_process = [d for d in all_dates if d not in skip_dates]

    if not dates_to_process:
        logger.error("No dates to process")
        sys.exit(1)

    logger.info(f"Found {len(all_dates)} dates, processing {len(dates_to_process)} (skipping: {skip_dates or 'none'})")
    logger.info(f"Dates: {', '.join(dates_to_process)}")
    logger.info(f"Running {max_parallel} in parallel")

    # Run regeneration in parallel using threads
    results = []
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {
            executor.submit(regenerate_one, generator, web_dir, date): date
            for date in dates_to_process
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Summary
    successful = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]

    print("\n" + "="*50)
    print(f"Completed: {len(successful)}/{len(results)}")
    if successful:
        print(f"Success: {', '.join(sorted(r[0] for r in successful))}")
    if failed:
        print(f"Failed: {', '.join(r[0] for r in failed)}")
        for r in failed:
            print(f"  {r[0]}: {r[2]}")
    print("="*50)


async def main():
    parser = argparse.ArgumentParser(
        description="Regenerate hero image for specific date(s)",
        epilog="Configuration: Requires 'image' section in config/providers.yaml"
    )
    parser.add_argument(
        "date",
        nargs='?',
        help="Date in YYYY-MM-DD format (default: today with prompt)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt for today's date"
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="Regenerate all dates"
    )
    parser.add_argument(
        "-s", "--skip",
        help="Dates to skip (comma-separated, ranges with colon). Example: 2026-01-01:2026-01-03,2026-01-07"
    )
    parser.add_argument(
        "-t", "--max-parallel",
        type=int,
        default=3,
        help="Maximum parallel regenerations for --all mode (default: 3)"
    )
    parser.add_argument(
        "--prompt",
        help="Custom prompt to override auto-generated prompt (single date only)"
    )
    parser.add_argument(
        "-e", "--edit",
        help="Edit existing hero image with specific changes (backs up original)"
    )
    parser.add_argument(
        "--web-dir",
        default="./web",
        help="Web output directory (default: ./web)"
    )
    args = parser.parse_args()

    # Validate mutually exclusive options
    if args.edit and args.all:
        parser.error("--edit cannot be used with --all")
    if args.edit and args.prompt:
        parser.error("--edit cannot be used with --prompt")

    web_dir = Path(args.web_dir).resolve()

    # Initialize generator from config
    generator = initialize_generator()

    # Determine mode
    if args.all:
        # All dates mode
        skip_dates = parse_skip_dates(args.skip) if args.skip else set()
        regenerate_all(generator, web_dir, skip_dates, args.max_parallel)
    elif args.edit:
        # Edit mode
        if args.date:
            target_date = args.date
        else:
            # Default to today
            target_date = datetime.now().strftime('%Y-%m-%d')
            if not args.yes:
                response = input(f"Do you want to edit the hero for today's date ({target_date})? [Y/n]: ").strip().lower()
                if response and response not in ('y', 'yes'):
                    print("Cancelled.")
                    sys.exit(0)

        success = await edit_single(generator, web_dir, target_date, args.edit)
        if not success:
            sys.exit(1)
    else:
        # Single date regenerate mode
        if args.date:
            target_date = args.date
        else:
            # Default to today
            target_date = datetime.now().strftime('%Y-%m-%d')
            if not args.yes:
                response = input(f"Do you want to re-generate the hero for today's date ({target_date})? [Y/n]: ").strip().lower()
                if response and response not in ('y', 'yes'):
                    print("Cancelled.")
                    sys.exit(0)

        logger.info(f"Loading summary for {target_date}...")
        success = await regenerate_single(generator, web_dir, target_date, args.prompt)
        if not success:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
