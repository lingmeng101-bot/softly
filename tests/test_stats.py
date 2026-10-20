from pipeline import CrawlStats


def test_iadd():
    total = CrawlStats()
    total += CrawlStats(added=2, skipped=1, bad=3, blocked=1, parse_error=1)
    assert (total.added, total.skipped, total.bad, total.blocked, total.parse_error) == (2, 1, 3, 1, 1)
