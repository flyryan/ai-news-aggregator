"""
arXiv OAI-PMH harvester for accurate announcement-date-based collection.

Uses the arXivRaw metadata format to get version history and filter new papers.
The OAI-PMH datestamp field represents the announcement date, which matches
what the RSS feed would return for that day.

arXiv publishing schedule:
- Announcements happen Sun-Thu at ~8PM ET
- No announcements Friday or Saturday night
- Papers announced Thursday 8PM have Friday datestamp
- Monday's announcements cover all weekend submissions (Fri-Sun)
"""
import logging
import os
import time
from typing import List, Dict, Optional
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

OAI_BASE_URL = "https://oaipmh.arxiv.org/oai"
ARXIV_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"


def _env_float(name: str, default: float) -> float:
    """Read a positive float from the environment, falling back on bad input."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning(f"{name}={raw!r} is not a number; using {default}")
        return default
    if value <= 0:
        logger.warning(f"{name}={value} must be positive; using {default}")
        return default
    return value


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


# Per-request read timeout. Measured 2026-08-17: a *successful* ListRecords for
# the cs archive took 51.5s, and the previous 60s ceiling turned that into a
# hard failure on a slow morning. The endpoint's latency is erratic rather than
# uniformly slow -- the identical query returned in 0.6s, 51.5s, and >90s within
# minutes of each other -- so the timeout is sized for the tail, not the median.
DEFAULT_TIMEOUT = _env_float("ARXIV_OAI_TIMEOUT", 180.0)

# Attempts per HTTP request, including the first. Retry buys more than a longer
# timeout does against an endpoint that stalls intermittently.
DEFAULT_MAX_ATTEMPTS = max(1, _env_int("ARXIV_OAI_MAX_ATTEMPTS", 3))

# Base for exponential backoff between attempts (5s, 10s, 20s...).
DEFAULT_BACKOFF = _env_float("ARXIV_OAI_BACKOFF_SECONDS", 5.0)

# Whole-harvest wall-clock ceiling across every archive, page and retry. Without
# it, timeout x attempts x pages x archives can run to tens of minutes and eat
# the job's budget for a source that is already failing.
DEFAULT_DEADLINE = _env_float("ARXIV_OAI_DEADLINE_SECONDS", 600.0)


class ArxivOAIHarvester:
    """Harvest arXiv papers by announcement date using OAI-PMH."""

    def __init__(
        self,
        categories: List[str],
        timeout: Optional[float] = None,
        max_attempts: Optional[int] = None,
        backoff_seconds: Optional[float] = None,
        deadline_seconds: Optional[float] = None,
        sleep=time.sleep,
        now=time.monotonic,
    ):
        """
        Initialize harvester with list of arXiv categories.

        Args:
            categories: List of arXiv category codes (e.g., ['cs.AI', 'cs.LG', 'cs.CL'])
            timeout: Per-request read timeout in seconds.
            max_attempts: Attempts per request, including the first.
            backoff_seconds: Base for exponential backoff between attempts.
            deadline_seconds: Wall-clock ceiling for the whole harvest.
            sleep: Injected for tests; must not be called in the happy path.
            now: Monotonic clock, injected for tests.
        """
        self.categories = set(categories)
        # Group categories by archive for efficient querying
        # OAI-PMH only supports archive-level sets (e.g., 'cs', 'stat'), not subject classes
        self.archives = set(cat.split('.')[0] for cat in categories)

        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self.max_attempts = DEFAULT_MAX_ATTEMPTS if max_attempts is None else max(1, max_attempts)
        self.backoff_seconds = DEFAULT_BACKOFF if backoff_seconds is None else backoff_seconds
        self.deadline_seconds = DEFAULT_DEADLINE if deadline_seconds is None else deadline_seconds
        self._sleep = sleep
        self._now = now

        # Archives whose harvest ended early -- transport failure, XML error, or
        # the deadline. Non-empty means the returned paper list is a floor, not a
        # complete answer, and the caller must not publish it as if it were whole.
        # Reset at the start of every harvest_date call.
        self.incomplete_archives: List[str] = []

    @property
    def last_harvest_complete(self) -> bool:
        """False when the most recent harvest gave up on at least one archive."""
        return not self.incomplete_archives

    def harvest_date(self, from_date: str, until_date: Optional[str] = None) -> List[Dict]:
        """
        Harvest all NEW papers announced on a specific date or date range.

        Uses the OAI-PMH datestamp field which represents the announcement date.
        Filters to v1-only papers to exclude revisions/updates.

        Args:
            from_date: Start date in YYYY-MM-DD format
            until_date: End date in YYYY-MM-DD format. If None, uses from_date (single day).

        Returns:
            List of paper metadata dicts for new papers announced in the date range
        """
        query_until = until_date or from_date
        if from_date == query_until:
            logger.info(f"OAI-PMH harvesting papers for datestamp={from_date}, archives={self.archives}")
        else:
            logger.info(f"OAI-PMH harvesting papers for datestamp={from_date} to {query_until}, archives={self.archives}")

        papers = []
        self.incomplete_archives = []
        self._deadline_at = self._now() + self.deadline_seconds

        # Query by archive (OAI-PMH only supports archive-level sets). Sorted so
        # a deadline cut-off truncates the same way run to run.
        for archive in sorted(self.archives):
            try:
                archive_papers = self._harvest_archive(from_date, query_until, archive)
                # Filter to papers that match our target categories
                matching_papers = [
                    p for p in archive_papers
                    if self._matches_categories(p)
                ]
                papers.extend(matching_papers)
                logger.info(f"OAI-PMH: {len(matching_papers)} new papers from {archive} "
                           f"(filtered from {len(archive_papers)} in archive)")
            except Exception as e:
                logger.error(f"OAI-PMH error for archive {archive}: {e}")
                if archive not in self.incomplete_archives:
                    self.incomplete_archives.append(archive)

        # Deduplicate by arxiv_id (papers can appear in multiple categories)
        seen = set()
        unique_papers = []
        for paper in papers:
            if paper['arxiv_id'] not in seen:
                seen.add(paper['arxiv_id'])
                unique_papers.append(paper)

        date_desc = from_date if from_date == query_until else f"{from_date} to {query_until}"
        if self.incomplete_archives:
            # Loud, and distinct from a legitimately empty day: 0 papers because
            # arXiv had none reads identically to 0 papers because the transport
            # died, and that ambiguity is exactly how the 2026-08-17 outage
            # published as a green run.
            logger.error(
                f"OAI-PMH INCOMPLETE for {date_desc}: gave up on "
                f"{', '.join(self.incomplete_archives)}. "
                f"{len(unique_papers)} papers is a floor, not the full set."
            )
        else:
            logger.info(f"OAI-PMH total: {len(unique_papers)} unique new papers for {date_desc}")
        return unique_papers

    def _matches_categories(self, paper: Dict) -> bool:
        """Check if paper belongs to any of our target categories."""
        paper_categories = paper.get('categories', '').split()
        return any(cat in self.categories for cat in paper_categories)

    # HTTP statuses worth another attempt. arXiv's OAI endpoint answers 503 with
    # a Retry-After when it is shedding load, which is a request to wait, not a
    # reason to give up.
    RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    def _budget_left(self) -> float:
        """Seconds remaining before the whole-harvest deadline."""
        return getattr(self, '_deadline_at', self._now() + self.deadline_seconds) - self._now()

    def _retry_after_seconds(self, response) -> Optional[float]:
        """Parse a Retry-After header, when the server sent a usable one."""
        raw = (response.headers.get('Retry-After') or '').strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None  # HTTP-date form; fall back to our own backoff
        return value if value >= 0 else None

    def _request_with_retry(self, url: str, archive: str):
        """GET a ListRecords page, retrying transient failures.

        Raises the final exception when every attempt fails, so the caller can
        record the archive as incomplete rather than mistake it for empty.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_attempts + 1):
            budget = self._budget_left()
            if budget <= 0:
                raise TimeoutError(
                    f"OAI-PMH deadline of {self.deadline_seconds:.0f}s exhausted "
                    f"before attempt {attempt} for {archive}"
                )

            # Never let one request outlive the whole-harvest budget.
            timeout = min(self.timeout, budget)

            try:
                response = requests.get(url, timeout=timeout)
            except requests.exceptions.RequestException as e:
                # Transport-level failure (timeout, DNS, reset) -- always worth
                # another go; this is the 2026-08-17 case.
                last_error = e
                if attempt < self.max_attempts:
                    self._backoff(attempt, archive, e)
                    continue
                raise

            if response.status_code in self.RETRYABLE_STATUS:
                wait = self._retry_after_seconds(response)
                last_error = requests.exceptions.HTTPError(
                    f"HTTP {response.status_code} from OAI-PMH"
                )
                if attempt < self.max_attempts:
                    self._backoff(attempt, archive, last_error, override=wait)
                    continue
                raise last_error

            # Any other non-2xx is a malformed query or a protocol change on our
            # side. Deliberately raised outside the retry path: `raise_for_status`
            # throws a RequestException, so catching it above would silently retry
            # our own bugs and turn a fast 404 into three slow ones.
            response.raise_for_status()

            if attempt > 1:
                logger.info(f"OAI-PMH recovered for {archive} on attempt {attempt}")
            return response

        raise last_error if last_error else RuntimeError("unreachable")

    def _backoff(self, attempt: int, archive: str, error: Exception, override: Optional[float] = None) -> None:
        """Sleep between attempts, clamped to the remaining harvest budget."""
        wait = override if override is not None else self.backoff_seconds * (2 ** (attempt - 1))
        wait = max(0.0, min(wait, self._budget_left()))
        logger.warning(
            f"OAI-PMH attempt {attempt}/{self.max_attempts} failed for {archive} "
            f"({type(error).__name__}: {error}); retrying in {wait:.0f}s"
        )
        if wait > 0:
            self._sleep(wait)

    def _harvest_archive(self, from_date: str, until_date: str, archive: str) -> List[Dict]:
        """Harvest all papers from an archive for a date range, handling pagination.

        Pages already fetched are kept when a later page fails; the archive is
        recorded in `incomplete_archives` so the caller knows the set is partial.
        """
        papers = []
        resumption_token = None
        page = 0

        while True:
            page += 1
            if resumption_token:
                url = f"{OAI_BASE_URL}?verb=ListRecords&resumptionToken={resumption_token}"
            else:
                # Query by archive only (e.g., 'cs', 'stat')
                # OAI-PMH doesn't support subject-class-level sets for most archives
                url = (
                    f"{OAI_BASE_URL}?verb=ListRecords"
                    f"&metadataPrefix=arXivRaw"
                    f"&from={from_date}&until={until_date}"
                    f"&set={archive}"
                )

            try:
                response = self._request_with_retry(url, archive)
            except Exception as e:
                logger.error(
                    f"OAI-PMH request failed for {archive} after "
                    f"{self.max_attempts} attempt(s) on page {page}: {e}"
                )
                self.incomplete_archives.append(archive)
                break

            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                logger.error(f"OAI-PMH XML parse error for {archive}: {e}")
                self.incomplete_archives.append(archive)
                break

            # Check for OAI-PMH errors
            error = root.find(f".//{{{OAI_NS}}}error")
            if error is not None:
                error_code = error.get('code', 'unknown')
                if error_code == 'noRecordsMatch':
                    # No records for this date range/archive - not an error
                    date_desc = from_date if from_date == until_date else f"{from_date} to {until_date}"
                    logger.debug(f"OAI-PMH: No records for {archive} on {date_desc}")
                    break
                else:
                    logger.error(f"OAI-PMH error for {archive}: {error_code} - {error.text}")
                    self.incomplete_archives.append(archive)
                    break

            # Parse records
            records = root.findall(f".//{{{OAI_NS}}}record")
            for record in records:
                paper = self._parse_record(record)
                if paper and self._is_new_paper(paper):
                    papers.append(paper)

            # Check for resumption token (pagination)
            token_elem = root.find(f".//{{{OAI_NS}}}resumptionToken")
            if token_elem is not None and token_elem.text:
                if self._budget_left() <= 0:
                    logger.error(
                        f"OAI-PMH deadline reached for {archive} after page {page}; "
                        f"{len(papers)} papers collected, more were available"
                    )
                    self.incomplete_archives.append(archive)
                    break
                resumption_token = token_elem.text
                logger.debug(f"OAI-PMH: Fetching page {page + 1} for {archive}")
            else:
                break

        return papers

    def _parse_record(self, record: ET.Element) -> Optional[Dict]:
        """Parse a single OAI-PMH record into paper metadata."""
        try:
            header = record.find(f"{{{OAI_NS}}}header")
            if header is None:
                return None

            # Check for deleted records
            status = header.get('status')
            if status == 'deleted':
                return None

            metadata = record.find(f".//{{{ARXIV_RAW_NS}}}arXivRaw")
            if metadata is None:
                return None

            # Extract required fields
            arxiv_id_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}id")
            title_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}title")
            authors_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}authors")
            abstract_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}abstract")
            categories_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}categories")
            datestamp_elem = header.find(f"{{{OAI_NS}}}datestamp")

            if any(elem is None for elem in [arxiv_id_elem, title_elem, authors_elem,
                                              abstract_elem, categories_elem, datestamp_elem]):
                return None

            arxiv_id = arxiv_id_elem.text
            datestamp = datestamp_elem.text

            # Get version history
            versions = []
            for v in metadata.findall(f"{{{ARXIV_RAW_NS}}}version"):
                version_num = v.get('version')
                date_elem = v.find(f"{{{ARXIV_RAW_NS}}}date")
                if version_num and date_elem is not None:
                    versions.append({
                        'version': version_num,
                        'date': date_elem.text
                    })

            # Get optional fields
            comments_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}comments")
            license_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}license")
            journal_ref_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}journal-ref")
            doi_elem = metadata.find(f"{{{ARXIV_RAW_NS}}}doi")

            return {
                'arxiv_id': arxiv_id,
                'datestamp': datestamp,  # Announcement date
                'title': title_elem.text or '',
                'authors': authors_elem.text or '',
                'abstract': abstract_elem.text or '',
                'categories': categories_elem.text or '',
                'versions': versions,
                'comments': comments_elem.text if comments_elem is not None else None,
                'license': license_elem.text if license_elem is not None else None,
                'journal_ref': journal_ref_elem.text if journal_ref_elem is not None else None,
                'doi': doi_elem.text if doi_elem is not None else None,
            }

        except Exception as e:
            logger.error(f"Error parsing OAI-PMH record: {e}")
            return None

    def _is_new_paper(self, paper: Dict) -> bool:
        """
        Check if paper is newly announced (v1 only) vs a revision.

        Papers with only v1 in their version history are new announcements.
        Papers with v2, v3, etc. are revisions of previously announced papers.
        """
        versions = paper.get('versions', [])
        if not versions:
            return False
        # New papers have exactly one version entry: v1
        return len(versions) == 1 and versions[0].get('version') == 'v1'
