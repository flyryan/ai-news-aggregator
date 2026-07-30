"""Minimal GitHub REST client for the admin panel.

Only what the panel needs: list runs, dispatch, and read job logs. Logs are the
reason a token is mandatory rather than optional -- run metadata is public on
this repo, but the logs endpoint returns 403 unauthenticated.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

__all__ = ["GitHubClient", "GitHubError"]

logger = logging.getLogger("admin_service.github")

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repo: str, token: str = "", *, timeout: float = 20.0) -> None:
        self._repo = repo
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{API}/repos/{self._repo}{path}"
        try:
            response = httpx.get(
                url, headers=self._headers(), params=params, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if response.status_code == 403 and not self._token:
            raise GitHubError(
                "GitHub returned 403. Run metadata is public on this repo but logs "
                "are not; set ADMIN_GITHUB_TOKEN."
            )
        if response.status_code == 429 or (
            response.status_code == 403 and "rate limit" in response.text.lower()
        ):
            raise GitHubError(
                "GitHub rate limit reached. Unauthenticated requests are capped at "
                "60/hour; set ADMIN_GITHUB_TOKEN for 5,000."
            )
        if response.status_code >= 400:
            raise GitHubError(f"GitHub {response.status_code} for {path}")
        return response.json()

    def list_runs(self, workflow: str = "daily-pipeline.yml", limit: int = 30) -> list[dict]:
        payload = self._get(f"/actions/workflows/{workflow}/runs", per_page=limit)
        runs = []
        for run in payload.get("workflow_runs", []):
            runs.append({
                "id": run.get("id"),
                "run_number": run.get("run_number"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "event": run.get("event"),
                "display_title": run.get("display_title"),
                "actor": (run.get("actor") or {}).get("login"),
                "head_sha": run.get("head_sha"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "html_url": run.get("html_url"),
                "run_attempt": run.get("run_attempt"),
            })
        return runs

    def run_jobs(self, run_id: int) -> list[dict]:
        """Jobs and steps for one run — the expandable detail in the panel.

        Public on this repo (unlike logs), so it works without a token.
        """
        def seconds(started: str | None, completed: str | None) -> int | None:
            if not started or not completed:
                return None
            try:
                from datetime import datetime
                a = datetime.fromisoformat(started.replace("Z", "+00:00"))
                b = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                return max(0, int((b - a).total_seconds()))
            except ValueError:
                return None

        payload = self._get(f"/actions/runs/{run_id}/jobs", per_page=20)
        jobs = []
        for job in payload.get("jobs", []):
            jobs.append({
                "id": job.get("id"),
                "name": job.get("name"),
                "status": job.get("status"),
                "conclusion": job.get("conclusion"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "duration_seconds": seconds(job.get("started_at"), job.get("completed_at")),
                "html_url": job.get("html_url"),
                "steps": [
                    {
                        "name": step.get("name"),
                        "status": step.get("status"),
                        "conclusion": step.get("conclusion"),
                        "duration_seconds": seconds(
                            step.get("started_at"), step.get("completed_at")
                        ),
                    }
                    for step in (job.get("steps") or [])
                ],
            })
        return jobs

    def in_flight(self, workflow: str = "daily-pipeline.yml") -> dict | None:
        """A queued or running run, if any.

        Dispatching while one is active would cancel it: daily-pipeline.yml sets
        cancel-in-progress for workflow_dispatch.
        """
        for status in ("in_progress", "queued"):
            payload = self._get(
                f"/actions/workflows/{workflow}/runs", status=status, per_page=1
            )
            runs = payload.get("workflow_runs") or []
            if runs:
                return {"id": runs[0].get("id"), "status": status,
                        "html_url": runs[0].get("html_url")}
        return None

    def dispatch(self, workflow: str, inputs: dict[str, str], ref: str = "main") -> None:
        if not self._token:
            raise GitHubError("dispatch requires ADMIN_GITHUB_TOKEN with actions:write")
        url = f"{API}/repos/{self._repo}/actions/workflows/{workflow}/dispatches"
        try:
            response = httpx.post(
                url, headers=self._headers(),
                json={"ref": ref, "inputs": inputs}, timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"dispatch failed: {exc}") from exc
        if response.status_code != 204:
            raise GitHubError(f"dispatch returned {response.status_code}: {response.text[:200]}")

    def job_logs(self, run_id: int, max_jobs: int = 5) -> str:
        """Plain-text logs for a run's jobs.

        Uses the per-job endpoint (plain text) rather than the run endpoint
        (a zip). The redirect Location points at a DIFFERENT host, so the auth
        header is deliberately not forwarded across it -- doing so would leak
        the bearer token to a third-party origin.
        """
        if not self._token:
            raise GitHubError("reading logs requires ADMIN_GITHUB_TOKEN")

        jobs = self._get(f"/actions/runs/{run_id}/jobs").get("jobs", [])
        chunks: list[str] = []
        for job in jobs[:max_jobs]:
            job_id = job.get("id")
            url = f"{API}/repos/{self._repo}/actions/jobs/{job_id}/logs"
            try:
                head = httpx.get(
                    url, headers=self._headers(),
                    follow_redirects=False, timeout=self._timeout,
                )
                if head.status_code in (301, 302, 307):
                    location = head.headers.get("location", "")
                    # No Authorization here: different host, and forwarding a
                    # bearer token across a redirect leaks it.
                    body = httpx.get(location, timeout=self._timeout).text
                else:
                    body = head.text
            except httpx.HTTPError as exc:
                body = f"(could not fetch logs for job {job_id}: {exc})"
            chunks.append(f"===== {job.get('name', job_id)} =====\n{body}")
        return "\n\n".join(chunks)
