import type {
	ActionSpec,
	ActionStatus,
	AuditEntry,
	Balance,
	CostRun,
	HealthSeries,
	LatestReport,
	PreviewJob,
	SourceDayDetail,
	WorkflowRun
} from '$lib/types/admin';

/** Thrown for any non-OK response so callers can show the real reason. */
export class AdminApiError extends Error {
	constructor(
		message: string,
		readonly status: number
	) {
		super(message);
		this.name = 'AdminApiError';
	}
}

async function get<T>(path: string): Promise<T> {
	const response = await fetch(path, { credentials: 'same-origin' });
	if (response.status === 401 || response.status === 403) {
		throw new AdminApiError(
			'Your Cloudflare Access session has expired. Reload to sign in again.',
			response.status
		);
	}
	if (!response.ok) {
		throw new AdminApiError(`Request failed (${response.status})`, response.status);
	}
	return (await response.json()) as T;
}

async function post<T>(path: string): Promise<T> {
	const response = await fetch(path, { method: 'POST', credentials: 'same-origin' });
	const body = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new AdminApiError(body?.detail ?? `Request failed (${response.status})`, response.status);
	}
	return body as T;
}

export const getLatest = () => get<{ latest: LatestReport | null }>('/api/dashboard/latest');
export const getHealth = (days = 90) => get<HealthSeries>(`/api/dashboard/health?days=${days}`);
export const getCost = (days = 90) => get<{ runs: CostRun[] }>(`/api/dashboard/cost?days=${days}`);
export const getSourceDay = (source: string, date: string) =>
	get<SourceDayDetail>(
		`/api/dashboard/source-day?source=${encodeURIComponent(source)}&date=${encodeURIComponent(date)}`
	);

export const getBalances = () => get<{ balances: Balance[] }>('/api/dashboard/balances');
export const getRuns = (limit = 30) =>
	get<{ runs: WorkflowRun[]; error?: string }>(`/api/dashboard/runs?limit=${limit}`);
export const getActions = () => get<{ actions: ActionSpec[] }>('/api/actions');
export const getAudit = (limit = 50) => get<{ actions: AuditEntry[] }>(`/api/audit?limit=${limit}`);

export const runAction = (action: string, arg?: string) =>
	post<{ unit: string; started: boolean }>(
		`/api/actions/${action}${arg ? `?arg=${encodeURIComponent(arg)}` : ''}`
	);

export const getActionStatus = (unit: string) =>
	get<ActionStatus>(`/api/actions/status/${encodeURIComponent(unit)}`);

export const getActionLogs = (unit: string, lines = 200) =>
	get<{ unit: string; lines: string[] }>(
		`/api/actions/logs/${encodeURIComponent(unit)}?lines=${lines}`
	);

export const dispatchPipeline = (targetDate?: string, commitOutputs = false) => {
	const params = new URLSearchParams({ commit_outputs: String(commitOutputs) });
	if (targetDate) params.set('target_date', targetDate);
	return post<{ dispatched: boolean }>(`/api/pipeline/dispatch?${params}`);
};

export const getPreviews = () => get<{ previews: PreviewJob[] }>('/api/previews');

export const createPreview = (kind: 'hero' | 'report', date: string) =>
	post<PreviewJob>(`/api/previews?kind=${kind}&date=${encodeURIComponent(date)}`);

export const promotePreview = (jobId: string) =>
	post<{ promoted: boolean; files: string[] }>(
		`/api/previews/${encodeURIComponent(jobId)}/promote`
	);

export const discardPreview = async (jobId: string): Promise<void> => {
	const response = await fetch(`/api/previews/${encodeURIComponent(jobId)}`, {
		method: 'DELETE',
		credentials: 'same-origin'
	});
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new AdminApiError(body?.detail ?? 'Could not discard preview.', response.status);
	}
};
