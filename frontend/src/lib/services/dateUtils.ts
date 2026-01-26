/**
 * Date utility functions
 */

import { format, parseISO, isValid, addDays, subDays, startOfMonth, endOfMonth, eachDayOfInterval, isSameMonth, isToday as dateFnsIsToday, isSameDay as dateFnsIsSameDay } from 'date-fns';

/**
 * Parse a date string (YYYY-MM-DD) into a Date object
 */
export function parseDate(dateStr: string): Date | null {
	if (!dateStr) return null;

	const date = parseISO(dateStr);
	return isValid(date) ? date : null;
}

/**
 * Format a date for display
 */
export function formatDate(date: Date | string, formatStr: string = 'MMMM d, yyyy'): string {
	const d = typeof date === 'string' ? parseDate(date) : date;
	if (!d) return '';
	return format(d, formatStr);
}

/**
 * Format a date to YYYY-MM-DD string
 */
export function toDateString(date: Date): string {
	return format(date, 'yyyy-MM-dd');
}

/**
 * Get the previous day
 */
export function getPreviousDay(date: Date | string): string {
	const d = typeof date === 'string' ? parseDate(date) : date;
	if (!d) return '';
	return toDateString(subDays(d, 1));
}

/**
 * Get the next day
 */
export function getNextDay(date: Date | string): string {
	const d = typeof date === 'string' ? parseDate(date) : date;
	if (!d) return '';
	return toDateString(addDays(d, 1));
}

/**
 * Get all days in a month
 */
export function getDaysInMonth(year: number, month: number): Date[] {
	const start = startOfMonth(new Date(year, month));
	const end = endOfMonth(new Date(year, month));
	return eachDayOfInterval({ start, end });
}

/**
 * Get calendar grid for a month (includes padding days from adjacent months)
 */
export function getCalendarGrid(year: number, month: number): Date[] {
	const firstDay = startOfMonth(new Date(year, month));
	const lastDay = endOfMonth(new Date(year, month));

	// Get day of week for first day (0 = Sunday)
	const startPadding = firstDay.getDay();

	// Start from the first day visible in the calendar
	const calendarStart = subDays(firstDay, startPadding);

	// We want 6 weeks (42 days) for consistent calendar height
	const days: Date[] = [];
	for (let i = 0; i < 42; i++) {
		days.push(addDays(calendarStart, i));
	}

	return days;
}

/**
 * Check if a date is in the same month
 */
export function isInMonth(date: Date, year: number, month: number): boolean {
	return isSameMonth(date, new Date(year, month));
}

/**
 * Check if a date is today
 */
export const isToday = dateFnsIsToday;
export const isSameDay = dateFnsIsSameDay;

/**
 * Format relative time (e.g., "2 hours ago")
 */
export function formatRelativeTime(dateStr: string): string {
	const date = parseDate(dateStr);
	if (!date) return '';

	const now = new Date();
	const diffMs = now.getTime() - date.getTime();
	const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
	const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

	if (diffHours < 1) return 'Just now';
	if (diffHours < 24) return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
	if (diffDays === 1) return 'Yesterday';
	if (diffDays < 7) return `${diffDays} days ago`;

	return formatDate(date, 'MMM d');
}

/**
 * Get the month name
 */
export function getMonthName(month: number): string {
	return format(new Date(2000, month), 'MMMM');
}

/**
 * Get short weekday names
 */
export function getWeekdayNames(): string[] {
	return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
}

/**
 * Format day of week (e.g., "Monday", "Tuesday")
 */
export function formatDayOfWeek(date: Date | string): string {
	const d = typeof date === 'string' ? parseDate(date) : date;
	if (!d) return '';
	return format(d, 'EEEE');
}
