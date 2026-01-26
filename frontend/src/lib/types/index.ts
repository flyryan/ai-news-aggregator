/**
 * Type definitions for AI News Aggregator frontend
 */

export type Category = 'news' | 'research' | 'social' | 'reddit';

export interface NewsItem {
	id: string;
	title: string;
	content: string;
	content_html?: string;
	url: string;
	author: string;
	published: string;
	source: string;
	source_type: string;
	tags: string[];
	summary: string;
	summary_html?: string;
	importance_score: number;
	reasoning: string;
	themes: string[];
}

export interface CategoryTheme {
	name: string;
	description: string;
	item_count: number;
	example_items: string[];
	importance: number;
}

export interface TopTopic {
	name: string;
	description: string;
	description_html: string;
	category_breakdown: Record<Category, number>;
	representative_items: string[];
	importance: number;
}

export interface CategorySummary {
	count: number;
	category_summary: string;
	category_summary_html?: string;
	themes: CategoryTheme[];
	top_items: NewsItem[];
}

export interface CollectionSource {
	name: string;
	display_name: string;
	status: 'success' | 'partial' | 'failed';
	count: number;
	error: string | null;
}

export interface CollectionStatus {
	overall: 'success' | 'partial' | 'failed';
	sources: CollectionSource[];
}

export interface DaySummary {
	date: string;
	coverage_date?: string;
	coverage_start?: string;
	coverage_end?: string;
	executive_summary: string;
	executive_summary_html?: string;
	top_topics: TopTopic[];
	total_items_collected: number;
	total_items_analyzed: number;
	generated_at: string;
	categories: Record<Category, CategorySummary>;
	hero_image_url?: string;
	hero_image_prompt?: string;
	collection_status?: CollectionStatus;
}

export interface CategoryNotice {
	type: 'info' | 'warning';
	title: string;
	message: string;
}

export interface CategoryData {
	category: Category;
	date: string;
	category_summary: string;
	category_summary_html?: string;
	themes: CategoryTheme[];
	total_items: number;
	items: NewsItem[];
	notice?: CategoryNotice;
}

export interface DateEntry {
	date: string;
	total_items: number;
	categories: Record<Category, { count: number; file_size: number }>;
}

export interface DataIndex {
	version: string;
	dates: DateEntry[];
	latestDate: string | null;
	generatedAt: string;
	totalDates: number;
}

export interface SearchDocument {
	id: string;
	title: string;
	summary: string;
	url: string;
	date: string;
	category: Category;
	source: string;
	importance: number;
}

export interface SearchResult {
	ref: string;
	score: number;
	doc?: SearchDocument;
}

// Category display configuration
export const CATEGORY_CONFIG: Record<
	Category,
	{
		title: string;
		shortTitle: string;
		color: string;
		bgClass: string;
		textClass: string;
		badgeClass: string;
		accentClass: string;
	}
> = {
	news: {
		title: 'AI News',
		shortTitle: 'News',
		color: '#667eea',
		bgClass: 'bg-category-news',
		textClass: 'text-category-news',
		badgeClass: 'badge-news',
		accentClass: 'category-accent-news'
	},
	research: {
		title: 'Research Papers',
		shortTitle: 'Papers',
		color: '#10b981',
		bgClass: 'bg-category-research',
		textClass: 'text-category-research',
		badgeClass: 'badge-research',
		accentClass: 'category-accent-research'
	},
	social: {
		title: 'Social Media',
		shortTitle: 'Social',
		color: '#f59e0b',
		bgClass: 'bg-category-social',
		textClass: 'text-category-social',
		badgeClass: 'badge-social',
		accentClass: 'category-accent-social'
	},
	reddit: {
		title: 'Reddit Discussions',
		shortTitle: 'Reddit',
		color: '#ef4444',
		bgClass: 'bg-category-reddit',
		textClass: 'text-category-reddit',
		badgeClass: 'badge-reddit',
		accentClass: 'category-accent-reddit'
	}
};
