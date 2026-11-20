# GEO: AI-crawler visibility + crawlable artifacts

This recipe makes the daily-edition SPA visible to **AI search engines** (Perplexity,
ChatGPT/OpenAI, Claude, Gemini) and gives Google a clean sitemap + article schema.

## Why
A JS-rendered SPA hands a non-JS crawler an empty `<body>`. AI engines and Google's
rendering step may never see the day's content. The fix is to serve the *structure*
at the HTTP layer, no JS required.

## What's included
1. `generators/sitemap_generator.py` — writes `sitemap.xml` every edition.
2. `generators/quick_answer_generator.py` — a dated, crawler-visible "quick answer"
   distilled from the latest executive summary.
3. `generators/edition_seo_generator.py` — server-side `<h1>` + top-story list +
   `NewsArticle` JSON-LD (headline / datePublished / author / publisher / image).
4. `frontend/static/robots.txt` — allows AI **search** bots, blocks AI **training**
   crawlers. Configure the `Sitemap:` line to your real host.
5. `nginx.conf` — `ssi on` + `.xml` served as `application/xml` (GSC rejects text/xml).
6. `frontend/src/app.html` — two SSI includes (`/data/quick-answer.html` +
   `/data/edition-seo.html`) injected into raw HTML for non-JS crawlers.

## Wire-in
The generators run in `run_pipeline.py` PHASE 7.5 (non-fatal on failure). Set each
generator's `base_url` to your deployed host (`https://yourdomain`).

## Resilience
nginx SSI drops an include whose fragment is missing — a missing `edition-seo.html`
degrades to the quick-answer only, never takes the page down. Generators fail
closed-safe (return an empty/minimal block rather than raising).
