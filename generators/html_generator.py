#!/usr/bin/env python3
"""
HTML Generator
Generates static HTML pages for the AI news website.
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, Any, List
import logging
from jinja2 import Environment, FileSystemLoader, select_autoescape

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HTMLGenerator:
    """Generates HTML pages from analyzed data."""
    
    def __init__(self, template_dir: str, output_dir: str):
        """
        Initialize HTML generator.
        
        Args:
            template_dir: Directory containing Jinja2 templates
            output_dir: Directory for generated HTML files
        """
        self.template_dir = template_dir
        self.output_dir = output_dir
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Initialize Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml'])
        )
        
        # Add custom filters
        self.env.filters['datetime_format'] = self._datetime_format
        self.env.filters['truncate_text'] = self._truncate_text
        self.env.filters['markdown_to_html'] = self._markdown_to_html
        self.env.filters['strip_markup'] = self._strip_markup
        
        logger.info(f"Initialized HTML generator with templates from {template_dir}")
    
    def _datetime_format(self, iso_string: str, format_str: str = '%B %d, %Y') -> str:
        """Format ISO datetime string."""
        try:
            dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            return dt.strftime(format_str)
        except:
            return iso_string
    
    def _truncate_text(self, text: str, length: int = 200) -> str:
        """Truncate text to specified length."""
        if len(text) <= length:
            return text
        return text[:length].rsplit(' ', 1)[0] + '...'

    def _strip_markup(self, text: str) -> str:
        """Strip HTML tags and markdown formatting, returning plain text."""
        if not text:
            return text

        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)

        # Remove HTML entities
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
        text = re.sub(r'&#\d+;', ' ', text)

        # Remove markdown links [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

        # Remove markdown images ![alt](url)
        text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)

        # Remove markdown bold/italic
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'_([^_]+)_', r'\1', text)

        # Remove markdown headers
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

        # Remove markdown code blocks
        text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]+)`', r'\1', text)

        # Remove markdown bullet points
        text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)

        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        return text

    def _markdown_to_html(self, text: str) -> str:
        """Convert simple markdown to HTML."""
        if not text:
            return text

        # Convert headers (### -> h3, ## -> h2, # -> h1)
        text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
        text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

        # Convert bold (**text** or __text__)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)

        # Convert italic (*text* or _text_)
        text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
        text = re.sub(r'_([^_]+)_', r'<em>\1</em>', text)

        # Convert bullet points
        text = re.sub(r'^- (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
        text = re.sub(r'(<li>.+</li>\n?)+', r'<ul>\g<0></ul>', text)

        # Convert line breaks to paragraphs
        paragraphs = text.split('\n\n')
        html_parts = []
        for p in paragraphs:
            p = p.strip()
            if p:
                # Don't wrap if it already starts with an HTML tag
                if not p.startswith('<'):
                    p = f'<p>{p}</p>'
                html_parts.append(p)

        return '\n'.join(html_parts)
    
    def generate_index(self, analysis: Dict[str, Any]) -> str:
        """
        Generate the main index page.
        
        Args:
            analysis: Analysis data dictionary
            
        Returns:
            Path to generated HTML file
        """
        template = self.env.get_template('index.html')
        
        html = template.render(
            date=analysis.get('date', ''),
            executive_summary=analysis.get('executive_summary', ''),
            top_items=analysis.get('top_items', []),
            trends=analysis.get('trends', []),
            categories=analysis.get('categories', {}),
            total_items=analysis.get('total_items', 0),
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        output_path = os.path.join(self.output_dir, 'index.html')
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"Generated index page: {output_path}")
        return output_path
    
    def generate_category_page(self, category: str, items: List[Dict[str, Any]], date: str) -> str:
        """
        Generate a category-specific page.
        
        Args:
            category: Category name
            items: List of items in this category
            date: Date string
            
        Returns:
            Path to generated HTML file
        """
        template = self.env.get_template('category.html')
        
        html = template.render(
            category=category,
            items=items,
            date=date,
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Create safe filename
        safe_category = category.lower().replace(' ', '_').replace('&', 'and')
        output_path = os.path.join(self.output_dir, f'{safe_category}.html')
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"Generated category page: {output_path}")
        return output_path
    
    def generate_archive_index(self, archive_dates: List[str]) -> str:
        """
        Generate archive index page.
        
        Args:
            archive_dates: List of available archive dates
            
        Returns:
            Path to generated HTML file
        """
        template = self.env.get_template('archive.html')
        
        html = template.render(
            archive_dates=archive_dates,
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        output_path = os.path.join(self.output_dir, 'archive.html')
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"Generated archive page: {output_path}")
        return output_path
    
    def generate_all(self, analysis: Dict[str, Any]):
        """
        Generate all HTML pages from analysis data.
        
        Args:
            analysis: Complete analysis data
        """
        logger.info("Starting HTML generation")
        
        # Generate main index page
        self.generate_index(analysis)
        
        # Generate category pages
        categories = analysis.get('categories', {})
        date = analysis.get('date', '')
        
        for category, category_data in categories.items():
            items = category_data.get('items', [])
            if items:
                self.generate_category_page(category, items, date)
        
        logger.info("HTML generation complete")


# Default HTML templates (embedded for simplicity)
DEFAULT_TEMPLATES = {
    'base.html': '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}AI News Daily{% endblock %}</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #1a1a1a;
            background: #f5f5f5;
        }
        
        header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 2rem 0;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 2rem;
        }
        
        h1 {
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
        }
        
        .subtitle {
            font-size: 1.1rem;
            opacity: 0.9;
        }
        
        nav {
            background: white;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        nav ul {
            list-style: none;
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
        }
        
        nav li {
            margin: 0;
        }
        
        nav a {
            display: block;
            padding: 1rem 1.5rem;
            color: #333;
            text-decoration: none;
            transition: all 0.3s;
        }
        
        nav a:hover {
            background: #667eea;
            color: white;
        }
        
        main {
            padding: 2rem 0;
            min-height: 60vh;
        }
        
        .card {
            background: white;
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }
        
        .card h2 {
            color: #667eea;
            margin-bottom: 0.5rem;
        }
        
        .card h3 {
            color: #333;
            margin-bottom: 0.75rem;
        }
        
        .meta {
            color: #666;
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
        }
        
        .badge {
            display: inline-block;
            padding: 0.25rem 0.75rem;
            background: #e0e7ff;
            color: #667eea;
            border-radius: 20px;
            font-size: 0.85rem;
            margin-right: 0.5rem;
            margin-bottom: 0.5rem;
        }
        
        .content {
            margin-top: 1rem;
            line-height: 1.8;
        }
        
        .btn {
            display: inline-block;
            padding: 0.5rem 1rem;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background 0.3s;
        }
        
        .btn:hover {
            background: #5568d3;
        }
        
        footer {
            background: #1a1a1a;
            color: white;
            text-align: center;
            padding: 2rem 0;
            margin-top: 3rem;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-top: 2rem;
        }
        
        @media (max-width: 768px) {
            h1 {
                font-size: 2rem;
            }
            
            nav ul {
                flex-direction: column;
            }
            
            .grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
    {% block extra_css %}{% endblock %}
</head>
<body>
    <header>
        <div class="container">
            <h1>🤖 AI News Daily</h1>
            <p class="subtitle">Your daily digest of AI developments</p>
        </div>
    </header>
    
    <nav>
        <ul>
            <li><a href="index.html">Home</a></li>
            <li><a href="research_and_papers.html">Research</a></li>
            <li><a href="industry_and_business.html">Industry</a></li>
            <li><a href="products_and_tools.html">Products</a></li>
            <li><a href="models_and_benchmarks.html">Models</a></li>
            <li><a href="archive.html">Archive</a></li>
        </ul>
    </nav>
    
    <main>
        <div class="container">
            {% block content %}{% endblock %}
        </div>
    </main>
    
    <footer>
        <div class="container">
            <p>AI News Daily | Generated on {{ generated_at }}</p>
            <p style="margin-top: 0.5rem; opacity: 0.7;">Powered by Claude Opus 4.5</p>
        </div>
    </footer>
</body>
</html>''',
    
    'index.html': '''{% extends "base.html" %}

{% block title %}AI News Daily - {{ date }}{% endblock %}

{% block content %}
<div class="card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
    <h2 style="color: white;">📅 {{ date }}</h2>
    <p style="opacity: 0.95;">{{ total_items }} items collected and analyzed</p>
</div>

<div class="card">
    <h2>📊 Executive Summary</h2>
    <div class="content">
        {{ executive_summary|markdown_to_html|safe }}
    </div>
</div>

{% if trends %}
<div class="card">
    <h2>📈 Trending Topics</h2>
    {% for trend in trends %}
    <div style="margin-bottom: 1rem;">
        <h3>{{ trend.trend }}</h3>
        <p>{{ trend.description }}</p>
    </div>
    {% endfor %}
</div>
{% endif %}

<div class="card">
    <h2>⭐ Top Stories</h2>
    {% for item in top_items[:10] %}
    <div style="border-left: 3px solid #667eea; padding-left: 1rem; margin-bottom: 1.5rem;">
        <h3>{{ item.title }}</h3>
        <div class="meta">
            <span class="badge">{{ item.source_type }}</span>
            <span>{{ item.source }}</span> • 
            <span>{{ item.published|datetime_format }}</span>
        </div>
        {% if item.llm_summary %}
        <div style="margin-top: 0.5rem;">{{ item.llm_summary|markdown_to_html|safe }}</div>
        {% endif %}
        <a href="{{ item.url }}" target="_blank" class="btn" style="margin-top: 0.5rem;">Read More →</a>
    </div>
    {% endfor %}
</div>

<h2 style="margin-top: 2rem; margin-bottom: 1rem;">📑 Browse by Category</h2>
<div class="grid">
    {% for category, data in categories.items() %}
    <div class="card">
        <h3>{{ category }}</h3>
        <p class="meta">{{ data.count }} items</p>
        <a href="{{ category|lower|replace(' ', '_')|replace('&', 'and') }}.html" class="btn">View All →</a>
    </div>
    {% endfor %}
</div>
{% endblock %}''',
    
    'category.html': '''{% extends "base.html" %}

{% block title %}{{ category }} - AI News Daily{% endblock %}

{% block content %}
<div class="card">
    <h2>{{ category }}</h2>
    <p class="meta">{{ items|length }} items from {{ date }}</p>
</div>

{% for item in items %}
<div class="card">
    <h3>{{ item.title }}</h3>
    <div class="meta">
        <span class="badge">{{ item.source_type }}</span>
        <span>{{ item.source }}</span> •
        <span>{{ item.published|datetime_format }}</span>
        {% if item.author and item.author != 'Unknown' %}
        • <span>{{ item.author }}</span>
        {% endif %}
    </div>
    <div class="content">
        {% if item.llm_summary %}
        <div><strong>Summary:</strong> {{ item.llm_summary|markdown_to_html|safe }}</div>
        {% endif %}
        <p>{{ item.content|strip_markup|truncate_text(300) }}</p>
    </div>
    <a href="{{ item.url }}" target="_blank" class="btn">Read Full Article →</a>
</div>
{% endfor %}
{% endblock %}''',
    
    'archive.html': '''{% extends "base.html" %}

{% block title %}Archive - AI News Daily{% endblock %}

{% block content %}
<div class="card">
    <h2>📚 Archive</h2>
    <p>Browse past AI news digests</p>
</div>

<div class="grid">
    {% for date in archive_dates %}
    <div class="card">
        <h3>{{ date }}</h3>
        <a href="archive/{{ date }}/index.html" class="btn">View Digest →</a>
    </div>
    {% endfor %}
</div>
{% endblock %}'''
}


def create_default_templates(template_dir: str):
    """Create default template files."""
    os.makedirs(template_dir, exist_ok=True)
    
    for filename, content in DEFAULT_TEMPLATES.items():
        filepath = os.path.join(template_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Created template: {filepath}")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python html_generator.py <analysis_file> <template_dir> <output_dir>")
        print("Example: python html_generator.py ./data/analyzed.json ./templates ./web")
        sys.exit(1)
    
    analysis_file = sys.argv[1]
    template_dir = sys.argv[2]
    output_dir = sys.argv[3]
    
    # Create default templates if directory doesn't exist
    if not os.path.exists(template_dir):
        logger.info(f"Creating default templates in {template_dir}")
        create_default_templates(template_dir)
    
    # Load analysis data
    with open(analysis_file, 'r', encoding='utf-8') as f:
        analysis = json.load(f)
    
    # Generate HTML
    generator = HTMLGenerator(template_dir, output_dir)
    generator.generate_all(analysis)
    
    logger.info(f"Website generated in {output_dir}")
