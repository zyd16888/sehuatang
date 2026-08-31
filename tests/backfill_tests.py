import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main
from scrapers.page_parser import PageParser
from scrapers.web_scraper import WebScraper


def thread_html(tid, thread_date):
    return f"""
    <tbody id="normalthread_{tid}">
      <tr>
        <th><a class="s xst">ABP-{tid} title</a></th>
        <td class="by"><em><span title="{thread_date}">{thread_date}</span></em></td>
        <td><a class="showcontent y" id="content_{tid}"></a></td>
      </tr>
    </tbody>
    """


class PageParserBackfillTests(unittest.TestCase):
    def setUp(self):
        self.parser = PageParser()

    def test_filters_a_full_year_and_reads_last_page(self):
        html = f"""
        <html><body>
          <table>
            {thread_html("100", "2025-12-31 23:59")}
            {thread_html("101", "2025-01-01 00:01")}
            {thread_html("102", "2024-12-31 23:59")}
          </table>
          <div class="pg">
            <strong>200</strong>
            <a class="last" href="forum.php?mod=forumdisplay&amp;fid=103&amp;page=1492">
              ... 1492
            </a>
          </div>
        </body></html>
        """

        info_list, tid_list = self.parser.parse_plate_page(html, "2025")

        self.assertEqual(["100", "101"], tid_list)
        self.assertEqual(2, len(info_list))
        self.assertEqual(1492, self.parser.parse_last_page(html))


class LocateYearTests(unittest.TestCase):
    def test_binary_search_locates_and_expands_year_boundaries(self):
        scraper = WebScraper.__new__(WebScraper)
        pages = {
            1: (date(2026, 8, 31), date(2026, 8, 1)),
            2: (date(2025, 12, 31), date(2025, 9, 1)),
            3: (date(2025, 8, 31), date(2025, 4, 1)),
            4: (date(2025, 3, 31), date(2025, 1, 1)),
            5: (date(2024, 12, 31), date(2024, 1, 1)),
            6: (date(2023, 12, 31), date(2023, 1, 1)),
        }

        def metadata(_, fid, page):
            self.assertEqual(103, fid)
            newest, oldest = pages[page]
            return {
                "newest_date": newest,
                "oldest_date": oldest,
                "last_page": 6,
            }

        scraper._get_ordered_page_metadata = metadata.__get__(
            scraper,
            WebScraper,
        )

        self.assertEqual((1, 5), scraper._locate_year_page_range(103, 2025))


class DefaultFidTests(unittest.TestCase):
    def test_backfill_uses_configured_fids_when_not_specified(self):
        calls = []

        class FakeScraper:
            def __init__(self, target_date=None):
                self.target_date = target_date

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

            async def backfill_forum_section(self, fid, year, resume=False):
                calls.append((fid, year, resume))
                return {}

        with patch.object(main, "WebScraper", FakeScraper):
            result = asyncio.run(main.backfill(2025))

        self.assertTrue(result)
        self.assertEqual(
            [(fid, 2025, False) for fid in main.fid_list],
            calls,
        )


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_round_trip(self):
        scraper = WebScraper.__new__(WebScraper)
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "backfill_progress.json"
            with patch.object(
                WebScraper,
                "_checkpoint_path",
                return_value=checkpoint,
            ):
                scraper._save_backfill_checkpoint(2025, 103, 314)
                self.assertEqual(
                    314,
                    scraper._load_backfill_checkpoint(2025, 103),
                )


if __name__ == "__main__":
    unittest.main()
