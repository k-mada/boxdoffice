import os
import re
import time
import asyncio
import datetime
import discord
from discord import app_commands
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ---------------------------------------------------------------------------
# Cache for the weekend chart (so we don't scrape on every command)
# ---------------------------------------------------------------------------
weekend_cache = {
    "data": None,
    "timestamp": 0,
}
CACHE_DURATION = 3600  # refresh at most once per hour


# ---------------------------------------------------------------------------
# Box Office Mojo helpers (used by /boxoffice)
# ---------------------------------------------------------------------------
def _bom_search(query: str, year: str | None = None) -> tuple[str | None, str | None]:
    """Search BOM and return (title_page_url, poster_url) for the best match."""
    q = f"{query} {year}" if year else query
    search_url = f"https://www.boxofficemojo.com/search/?q={requests.utils.quote(q)}"
    resp = requests.get(search_url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.find("a", href=re.compile(r"/title/tt\d+/"))
    if not link:
        return None, None
    path = link["href"].split("?")[0]
    title_url = f"https://www.boxofficemojo.com{path}"
    img = link.find("img")
    poster_url = img["src"] if img and img.get("src") else None
    return title_url, poster_url


def _bom_scrape_grosses(title_url: str) -> dict | None:
    """Scrape domestic/international/worldwide gross from a BOM title page."""
    resp = requests.get(title_url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else "Unknown"

    money_re = re.compile(r"^\$[\d,]+$")
    lines = [l.strip() for l in soup.get_text(separator="\n").split("\n") if l.strip()]

    result = {"title": title, "domestic": None, "international": None, "worldwide": None}
    for i, line in enumerate(lines):
        for j in range(i + 1, min(i + 5, len(lines))):
            if money_re.match(lines[j]):
                if line.startswith("Domestic") and result["domestic"] is None:
                    result["domestic"] = lines[j]
                elif line.startswith("International") and result["international"] is None:
                    result["international"] = lines[j]
                elif line == "Worldwide" and result["worldwide"] is None:
                    result["worldwide"] = lines[j]
                break

    return result


# ---------------------------------------------------------------------------
# Box Office Mojo scraper (used by /weekend)
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _weekend_url_candidates(target_date: datetime.date | None = None) -> list[str]:
    """
    Return BOM weekend URL candidates most-recent-first.
    BOM weekends run Fri-Sun. We find the most recent Friday on or before
    target_date (defaulting to today) and build ISO-week URLs from there.
    """
    if target_date is None:
        target_date = datetime.date.today()

    # Find the most recent Friday on or before target_date
    # isoweekday: Mon=1 ... Fri=5, Sat=6, Sun=7
    days_since_friday = (target_date.isoweekday() - 5) % 7
    friday = target_date - datetime.timedelta(days=days_since_friday)

    candidates = []
    for delta in (0, -1):
        d = friday + datetime.timedelta(weeks=delta)
        y, w, _ = d.isocalendar()
        candidates.append(f"https://www.boxofficemojo.com/weekend/{y}W{w:02d}/")
    return candidates


def _parse_chart(html: str) -> tuple[list[dict], str]:
    """
    Parse a BOM weekend detail page.
    Column order: 0 Rank | 1 LW | 2 Title | 3 Weekend Gross | 4 %Chg |
                  5 Theaters | 6 Thtr Chg | 7 Per-Thtr Avg | 8 Total Gross | 9 Weeks
    Returns (movies, date_label) where date_label comes from the page <h4>.
    """
    soup = BeautifulSoup(html, "html.parser")

    # h4 contains the human-readable date range e.g. "February 27-March 1, 2026"
    h4 = soup.select_one("h4")
    date_label = h4.get_text(strip=True) if h4 else ""

    table = soup.select_one("table")
    if not table:
        return [], date_label

    results = []
    for row in table.select("tr")[1:11]:  # top 10 only
        cells = row.select("td")
        if len(cells) < 6:
            continue
        change = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        results.append({
            "rank": cells[0].get_text(strip=True),
            "title": cells[2].get_text(strip=True),
            "gross": cells[3].get_text(strip=True),
            "change": change or "NEW",
            "theaters": cells[5].get_text(strip=True),
        })

    return results, date_label


def _abbrev_gross(gross_str: str) -> str:
    """Convert '$21,706,163' → '$21.7M'."""
    digits = gross_str.replace("$", "").replace(",", "")
    try:
        amount = int(digits)
    except ValueError:
        return gross_str
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount / 1_000:.0f}K"


def _format_chart_table(movies: list[dict]) -> str:
    """Format the top 10 as a markdown list that reflows on mobile."""
    lines = []
    for m in movies:
        gross = _abbrev_gross(m["gross"])
        chg = m["change"]
        chg_str = " · 🆕" if chg == "NEW" else (f" · {chg}" if chg and chg != "-" else "")
        lines.append(f"**{m['rank']}.** {m['title']} — {gross}{chg_str}")
    return "\n".join(lines)


def _fetch_weekend_chart(target_date: datetime.date | None = None) -> tuple[list[dict], str]:
    """Synchronous fetch — called via asyncio.to_thread so it won't block the event loop."""
    for url in _weekend_url_candidates(target_date):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            movies, date_label = _parse_chart(resp.text)
            if movies:
                return movies, date_label
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}")
    return [], ""


async def get_weekend_chart(target_date: datetime.date | None = None) -> tuple[list[dict], str]:
    """Return the weekend chart. Uses cache only for the default (most recent) case."""
    if target_date is None:
        now = time.time()
        if weekend_cache["data"] and (now - weekend_cache["timestamp"] < CACHE_DURATION):
            return weekend_cache["data"], weekend_cache.get("date_label", "")

        data, date_label = await asyncio.to_thread(_fetch_weekend_chart)
        if data:
            weekend_cache["data"] = data
            weekend_cache["date_label"] = date_label
            weekend_cache["timestamp"] = now
        return data, date_label

    # Historical lookup — always fetch fresh, no caching
    return await asyncio.to_thread(_fetch_weekend_chart, target_date)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")


@tree.command(name="boxoffice", description="Get the box office gross for a movie")
@app_commands.describe(movie="Movie name, optionally followed by year (e.g. 'sabrina 1995')")
async def box_office(interaction: discord.Interaction, movie: str):
    await interaction.response.defer()

    # Extract optional trailing year from query
    year = None
    m = re.search(r'\b((?:19|20)\d{2})\s*$', movie)
    if m:
        year = m.group(1)
        movie = movie[:m.start()].strip()

    title_url, poster_url = await asyncio.to_thread(_bom_search, movie, year)

    if not title_url:
        await interaction.followup.send(f"Couldn't find **{movie}** on Box Office Mojo.")
        return

    data = await asyncio.to_thread(_bom_scrape_grosses, title_url)

    if not data:
        await interaction.followup.send("Found the movie but couldn't parse its gross data.")
        return

    embed = discord.Embed(
        title=f"🎬 {data['title']}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Domestic", value=data["domestic"] or "N/A", inline=True)
    embed.add_field(name="International", value=data["international"] or "N/A", inline=True)
    embed.add_field(name="Worldwide", value=data["worldwide"] or "N/A", inline=True)
    if poster_url:
        embed.set_thumbnail(url=poster_url)
    embed.set_footer(text="Source: boxofficemojo.com")
    await interaction.followup.send(embed=embed)


def _fetch_yearly_top10(year: str) -> list[dict]:
    """Scrape the top 10 grossing movies for a given year from BOM."""
    url = f"https://www.boxofficemojo.com/year/{year}/"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.select_one("table")
    if not table:
        return []

    results = []
    for row in table.select("tr")[1:11]:  # top 10 only
        cells = row.select("td")
        if len(cells) < 3:
            continue
        link = cells[1].find("a", href=True)
        title_url = f"https://www.boxofficemojo.com{link['href'].split('?')[0]}" if link else None
        results.append({
            "rank": cells[0].get_text(strip=True),
            "title": cells[1].get_text(strip=True),
            "worldwide": cells[2].get_text(strip=True),
            "title_url": title_url,
        })

    return results


@tree.command(name="yearlytop10", description="Get the top 10 grossing movies for a given year")
@app_commands.describe(year="4-digit year (e.g. 1988)")
async def yearly_top10(interaction: discord.Interaction, year: str):
    if not re.fullmatch(r"(?:19|20)\d{2}", year):
        await interaction.response.send_message("Please provide a valid 4-digit year (e.g. 1988).")
        return

    await interaction.response.defer()

    try:
        movies = await asyncio.to_thread(_fetch_yearly_top10, year)
    except requests.RequestException:
        await interaction.followup.send("Couldn't reach Box Office Mojo. Try again later.")
        return

    if not movies:
        await interaction.followup.send(f"No data found for **{year}**. Box Office Mojo may not have records for that year.")
        return

    # Fetch domestic gross for each movie concurrently
    async def _get_domestic(title_url: str | None) -> str | None:
        if not title_url:
            return None
        data = await asyncio.to_thread(_bom_scrape_grosses, title_url)
        return data["domestic"] if data else None

    domestics = await asyncio.gather(*[_get_domestic(m["title_url"]) for m in movies])

    lines = []
    for m, domestic in zip(movies, domestics):
        ww = _abbrev_gross(m["worldwide"])
        dom_str = f" · Domestic: {_abbrev_gross(domestic)}" if domestic else ""
        lines.append(f"**{m['rank']}.** {m['title']} — WW: {ww}{dom_str}")

    embed = discord.Embed(
        title=f"🎬 Top 10 Grossing Movies of {year}",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Source: boxofficemojo.com")
    await interaction.followup.send(embed=embed)


@tree.command(name="weekendtop10", description="Get a weekend's top 10 box office films")
@app_commands.describe(date="Date in MM/DD/YYYY format — returns the closest weekend on or before that date")
async def weekend(interaction: discord.Interaction, date: str | None = None):
    target_date = None
    if date is not None:
        try:
            target_date = datetime.datetime.strptime(date, "%m/%d/%Y").date()
        except ValueError:
            await interaction.response.send_message("Use the date format MM/DD/YYYY")
            return

    await interaction.response.defer()

    movies, date_label = await get_weekend_chart(target_date)

    if not movies:
        await interaction.followup.send(
            "Couldn't fetch the chart for that date. Box Office Mojo may not have data that far back, or the page layout changed."
        )
        return

    embed = discord.Embed(
        title=f"🍿 Weekend Box Office Top 10 — {date_label}",
        color=discord.Color.blue(),
    )
    embed.description = _format_chart_table(movies)
    embed.set_footer(text="Source: boxofficemojo.com")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------

@client.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot is online as {client.user}")
    print(f"   Slash commands synced — try /ping, /boxoffice, /weekendtop10, or /yearlytop10")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found. Make sure your .env file is set up.")
    else:
        client.run(DISCORD_TOKEN)
