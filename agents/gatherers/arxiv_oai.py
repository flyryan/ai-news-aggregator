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
from typing import List, Dict, Optional
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

OAI_BASE_URL = "https://oaipmh.arxiv.org/oai"
ARXIV_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"


class ArxivOAIHarvester:
    """Harvest arXiv papers by announcement date using OAI-PMH."""

    def __init__(self, categories: List[str]):
        """
        Initialize harvester with list of arXiv categories.

        Args:
            categories: List of arXiv category codes (e.g., ['cs.AI', 'cs.LG', 'cs.CL'])
        """
        self.categories = set(categories)
        # Group categories by archive for efficient querying
        # OAI-PMH only supports archive-level sets (e.g., 'cs', 'stat'), not subject classes
        self.archives = set(cat.split('.')[0] for cat in categories)

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

        # Query by archive (OAI-PMH only supports archive-level sets)
        for archive in self.archives:
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

        # Deduplicate by arxiv_id (papers can appear in multiple categories)
        seen = set()
        unique_papers = []
        for paper in papers:
            if paper['arxiv_id'] not in seen:
                seen.add(paper['arxiv_id'])
                unique_papers.append(paper)

        date_desc = from_date if from_date == query_until else f"{from_date} to {query_until}"
        logger.info(f"OAI-PMH total: {len(unique_papers)} unique new papers for {date_desc}")
        return unique_papers

    def _matches_categories(self, paper: Dict) -> bool:
        """Check if paper belongs to any of our target categories."""
        paper_categories = paper.get('categories', '').split()
        return any(cat in self.categories for cat in paper_categories)

    def _harvest_archive(self, from_date: str, until_date: str, archive: str) -> List[Dict]:
        """Harvest all papers from an archive for a date range, handling pagination."""
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
                response = requests.get(url, timeout=60)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.error(f"OAI-PMH request failed for {archive}: {e}")
                break

            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                logger.error(f"OAI-PMH XML parse error for {archive}: {e}")
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
