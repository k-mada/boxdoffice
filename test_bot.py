"""
Unit tests for the pure (no-network) functions in bot.py.
Run with: python -m pytest test_bot.py -v
"""
import datetime
from bot import (
    _abbrev_gross,
    _format_chart_table,
    _parse_grosses,
    _parse_chart,
    _weekend_url_candidates,
    _WeekendCache,
)


# ---------------------------------------------------------------------------
# _abbrev_gross
# ---------------------------------------------------------------------------
class TestAbbrevGross:
    def test_millions(self):
        assert _abbrev_gross("$21,706,163") == "$21.7M"

    def test_billions(self):
        assert _abbrev_gross("$2,923,706,026") == "$2.92B"

    def test_thousands(self):
        assert _abbrev_gross("$452,000") == "$452K"

    def test_invalid_passthrough(self):
        assert _abbrev_gross("N/A") == "N/A"


# ---------------------------------------------------------------------------
# _format_chart_table
# ---------------------------------------------------------------------------
class TestFormatChartTable:
    MOVIES = [
        {"rank": "1", "title": "Minecraft Movie", "gross": "$162,753,003", "change": "NEW",    "theaters": "4,300"},
        {"rank": "2", "title": "Flow",            "gross": "$4,200,000",   "change": "-18.2%", "theaters": "2,100"},
        {"rank": "3", "title": "Short Film",      "gross": "$980,000",     "change": "-",      "theaters": "800"},
    ]

    def test_new_entry(self):
        result = _format_chart_table([self.MOVIES[0]])
        assert "**1.**" in result
        assert "Minecraft Movie" in result
        assert "$162.8M" in result
        assert "🆕" in result

    def test_pct_change(self):
        result = _format_chart_table([self.MOVIES[1]])
        assert "-18.2%" in result

    def test_dash_change_suppressed(self):
        result = _format_chart_table([self.MOVIES[2]])
        assert "·" not in result


# ---------------------------------------------------------------------------
# _parse_grosses
# ---------------------------------------------------------------------------
class TestParseGrosses:
    TITLE_HTML = """
    <html><body>
      <h1>A Minecraft Movie (2025)</h1>
      <div>Domestic (44.1%)</div><div>$424,087,780</div>
      <div>International (55.9%)</div><div>$537,100,000</div>
      <div>Worldwide</div><div>$961,187,780</div>
    </body></html>
    """

    def test_parses_all_fields(self):
        result = _parse_grosses(self.TITLE_HTML)
        assert result["title"] == "A Minecraft Movie (2025)"
        assert result["domestic"] == "$424,087,780"
        assert result["international"] == "$537,100,000"
        assert result["worldwide"] == "$961,187,780"

    def test_returns_none_when_no_grosses(self):
        result = _parse_grosses("<html><body><h1>Empty</h1></body></html>")
        assert result is None


# ---------------------------------------------------------------------------
# _parse_chart
# ---------------------------------------------------------------------------
class TestParseChart:
    CHART_HTML = """
    <html><body>
      <h4>March 7-9, 2026</h4>
      <table>
        <tr>
          <th>Rank</th><th>LW</th><th>Title</th>
          <th>Weekend Gross</th><th>% Change</th><th>Theaters</th>
        </tr>
        <tr>
          <td>1</td><td>-</td><td>Minecraft Movie</td>
          <td>$162,753,003</td><td>-</td><td>4,300</td>
        </tr>
        <tr>
          <td>2</td><td>1</td><td>Flow</td>
          <td>$4,200,000</td><td>-18.2%</td><td>2,100</td>
        </tr>
      </table>
    </body></html>
    """

    def test_date_label(self):
        _, label = _parse_chart(self.CHART_HTML)
        assert label == "March 7-9, 2026"

    def test_parses_rows(self):
        movies, _ = _parse_chart(self.CHART_HTML)
        assert movies[0] == {"rank": "1", "title": "Minecraft Movie", "gross": "$162,753,003", "change": "-", "theaters": "4,300"}
        assert movies[1]["change"] == "-18.2%"

    def test_no_table_returns_empty(self):
        movies, _ = _parse_chart("<html><body><h4>Date</h4></body></html>")
        assert movies == []

    def test_unexpected_headers_returns_empty(self):
        html = """
        <html><body><h4>Date</h4>
        <table><tr><th>Col1</th><th>Col2</th></tr>
        <tr><td>a</td><td>b</td></tr></table>
        </body></html>
        """
        movies, _ = _parse_chart(html)
        assert movies == []


# ---------------------------------------------------------------------------
# _weekend_url_candidates
# ---------------------------------------------------------------------------
class TestWeekendUrlCandidates:
    def test_most_recent_weekend(self):
        # Monday 2026-03-09 → most recent Friday is 2026-03-06 → ISO week 10
        urls = _weekend_url_candidates(datetime.date(2026, 3, 9))
        assert "2026W10" in urls[0]
        assert "2026W09" in urls[1]

    def test_friday_maps_to_same_week(self):
        urls = _weekend_url_candidates(datetime.date(2026, 3, 6))
        assert "2026W10" in urls[0]


# ---------------------------------------------------------------------------
# _WeekendCache
# ---------------------------------------------------------------------------
class TestWeekendCache:
    def test_default_state(self):
        cache = _WeekendCache()
        assert cache.data is None
        assert cache.timestamp == 0.0
