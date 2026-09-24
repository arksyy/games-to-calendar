#!/usr/bin/env python3
"""
Fetch hockey games from DDLC website and add them to Apple Calendar.
"""

import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from dotenv import load_dotenv


class DDLCGameFetcher:
    FRENCH_MONTHS = {
        'janvier': 1, 'février': 2, 'mars': 3, 'avril': 4,
        'mai': 5, 'juin': 6, 'juillet': 7, 'août': 8,
        'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12
    }

    @staticmethod
    def _normalize_team(name):
        return name.replace('’', "'").replace('‘', "'").strip().casefold()

    def __init__(self, mode="default", url="https://www.ddlc.ca/ligues/calendrier/"):
        load_dotenv()
        self.mode = mode

        if mode == "gab":
            team_names_str = os.getenv('GAB_TEAM_NAMES')
            if not team_names_str:
                raise ValueError("GAB_TEAM_NAMES not found in .env file. Please configure your gab team names.")
            self.category = os.getenv('GAB_CATEGORY', 'F6+ (Saint-Aug)').strip()
            self.gab_calendar_staug = os.getenv('GAB_CALENDAR_STAUG', 'Dek St-Aug GAB')
            self.gab_calendar_chauveau = os.getenv('GAB_CALENDAR_CHAUVEAU', 'Dek Chauveau GAB')
        else:
            team_names_str = os.getenv('TEAM_NAMES')
            if not team_names_str:
                raise ValueError("TEAM_NAMES not found in .env file. Please configure your team names.")
            self.category = os.getenv('CATEGORY', 'B2 (Interligue)').strip()

        self.filter_team = team_names_str.split(',')[0].strip()
        self.team_names = [self._normalize_team(name) for name in team_names_str.split(',')]
        season = os.getenv('SEASON')
        if not season:
            raise ValueError("SEASON not found in .env file. Please configure the season label (e.g. 'Adulte | Automne/Hiver 2026-2027').")
        self.season = season.strip()
        self.url = url
        self.games = []

    def parse_french_date(self, date_str, time_str, year=None):
        """Parse French date string like 'lundi, 15 décembre' and time like '18:00'."""
        match = re.search(r'(\d+)\s+(\w+)', date_str)
        if not match:
            return None

        day = int(match.group(1))
        month_name = match.group(2).lower()
        month = self.FRENCH_MONTHS.get(month_name)

        if not month:
            return None

        if year is None:
            current_date = datetime.now()
            year = current_date.year
            if month < current_date.month or (month == current_date.month and day < current_date.day):
                year += 1

        time_match = re.search(r'(\d+):(\d+)', time_str)
        if not time_match:
            return None

        hour = int(time_match.group(1))
        minute = int(time_match.group(2))

        try:
            game_datetime = datetime(year, month, day, hour, minute)
            return game_datetime
        except ValueError:
            return None

    def _classify_game(self, venue, category, our_team):
        """Map a game's venue to (calendar_name, event_title), or None to skip.

        Branches on self.mode: default uses the regular calendars and
        'game {category}' titles; gab uses the GAB calendars and titles the
        event after our own team (parenthetical stripped)."""
        venue_lower = venue.lower()
        is_staug = "(st-aug" in venue_lower
        is_chauveau = "(chauveau" in venue_lower

        if self.mode == "gab":
            team_title = re.sub(r'\s*\([^)]*\)', '', our_team).strip()
            if is_staug:
                return self.gab_calendar_staug, team_title
            if is_chauveau:
                return self.gab_calendar_chauveau, team_title
            return None

        if is_staug:
            return "Dek St-Aug", f"game {category}"
        if is_chauveau:
            return "Dek Chauveau", f"game {category}"
        if "lévis" in venue_lower or "levis" in venue_lower:
            return "Autre", f"game {category} Levis"
        return None

    async def _select_filter(self, page, button_selector, option_selector, label):
        """Select a visible DDLC filter option and verify the selected label."""
        button = page.locator(button_selector)
        await button.click()
        for option in await page.locator(f'{option_selector}:visible').all():
            if self._normalize_team(await option.inner_text()) == self._normalize_team(label):
                await option.click()
                await page.wait_for_function(
                    "([selector, expected]) => document.querySelector(selector)?.textContent.trim().replace(/[’‘]/g, \"'\").toLowerCase() === expected",
                    arg=[button_selector, self._normalize_team(label)],
                )
                await page.locator('.loading_overlay').wait_for(state='hidden')
                return
        raise RuntimeError(f"Calendar filter option not found: {label}")

    async def fetch_games(self):
        """Fetch games from the DDLC website using Playwright."""
        async with async_playwright() as p:
            browser_path = os.getenv('CHROME_EXECUTABLE')
            if not browser_path and sys.platform == 'darwin':
                chrome_path = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                if chrome_path.exists():
                    browser_path = str(chrome_path)
            browser = await p.chromium.launch(headless=True, executable_path=browser_path)
            page = await browser.new_page()

            print(f"Loading {self.url}...")
            await page.goto(self.url, wait_until="networkidle")
            await page.wait_for_selector("iframe", timeout=10000)

            iframe_element = await page.query_selector("iframe")
            if not iframe_element:
                print("Error: No iframe found on the page")
                await browser.close()
                return self.games

            iframe_url = await iframe_element.get_attribute("src")
            print(f"Found calendar iframe: {iframe_url}")

            iframe_page = await browser.new_page()
            await iframe_page.goto(iframe_url, wait_until="networkidle")
            await asyncio.sleep(2)

            print(f"Selecting season '{self.season}', category '{self.category}', team '{self.filter_team}'...")
            await self._select_filter(iframe_page, '#dropdownMenuButton', 'a.dropdown-item.select_season', self.season)
            await self._select_filter(iframe_page, '#dropdownMenuButtonCategories', 'a.dropdown-item.select_category', self.category)
            await self._select_filter(iframe_page, '#dropdownMenuButtonTeam', 'a.dropdown-item.select_team', self.filter_team)

            print("Switching to full calendar view...")
            await iframe_page.locator('label.list_view[data-view="list"]').click()
            await iframe_page.locator('.loading_overlay').wait_for(state='hidden')
            await iframe_page.locator('table.schedule_table').wait_for(state='visible')

            content = await iframe_page.content()
            soup = BeautifulSoup(content, 'html.parser')

            current_date_str = None
            schedule_table = soup.find('table', class_='schedule_table')

            if not schedule_table:
                raise RuntimeError("Could not find schedule table")

            all_rows = schedule_table.find_all('tr')
            print(f"Processing {len(all_rows)} rows from schedule...\n")

            for row in all_rows:
                date_header = row.find('h2')
                if date_header:
                    current_date_str = date_header.get_text(strip=True)
                    continue

                if 'schedule_container' not in row.get('class', []):
                    continue

                team_links = row.find_all('a', href=re.compile(r'/equipes/'))
                if len(team_links) < 2:
                    continue

                all_teams = [link.get_text(strip=True) for link in team_links]
                all_teams = [t for t in all_teams if t]

                seen = set()
                team_names = []
                for team in all_teams:
                    if team not in seen:
                        seen.add(team)
                        team_names.append(team)

                if len(team_names) < 2:
                    print(f"  Skipped: Could not extract two team names from game row")
                    continue

                our_team = None
                opponent = None
                is_home = False

                for i, team in enumerate(team_names):
                    if self._normalize_team(team) in self.team_names:
                        our_team = team
                        opponent = team_names[1 - i]
                        is_home = i == 1
                        break

                if not our_team:
                    continue

                cat_name_div = row.find('div', class_='cat_name')
                category = "Hockey"
                if cat_name_div:
                    cat_span = cat_name_div.find('span')
                    if cat_span:
                        category_text = cat_span.get_text(strip=True)
                        if self._normalize_team(category_text) != self._normalize_team(self.category):
                            continue
                        category = category_text.split()[0] if category_text else "Hockey"
                else:
                    print(f"  Warning: Could not find category for game between {team_names[0]} and {team_names[1]}")
                    category = "Unknown"

                td_elements = row.find_all('td')
                game_datetime = None
                venue = "TBD"

                for td in td_elements:
                    date_div = td.find('div', class_='game_date')
                    if date_div:
                        date_text = date_div.get_text(strip=True)
                        if re.search(r'\d{4}-\d{2}-\d{2}', date_text):
                            continue
                        elif re.match(r'^\d{1,2}:\d{2}$', date_text):
                            time_str = date_text
                            for prev_td in td_elements:
                                prev_date_div = prev_td.find('div', class_='game_date')
                                if prev_date_div:
                                    prev_text = prev_date_div.get_text(strip=True)
                                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', prev_text)
                                    if date_match:
                                        date_str = date_match.group(1)
                                        try:
                                            date_parts = date_str.split('-')
                                            year = int(date_parts[0])
                                            month = int(date_parts[1])
                                            day = int(date_parts[2])

                                            time_match = re.match(r'(\d{1,2}):(\d{2})', time_str)
                                            if time_match:
                                                hour = int(time_match.group(1))
                                                minute = int(time_match.group(2))
                                                game_datetime = datetime(year, month, day, hour, minute)
                                        except (ValueError, IndexError) as e:
                                            print(f"  Error: Could not parse date '{date_str}' and time '{time_str}': {e}")
                                        break

                            venue_div = td.find('div', class_='game_venue')
                            if venue_div:
                                venue = venue_div.get_text(strip=True)
                            break

                if not game_datetime and current_date_str:
                    game_date_div = row.find('div', class_='game_date')
                    if game_date_div:
                        time_str = game_date_div.get_text(strip=True)
                        game_datetime = self.parse_french_date(current_date_str, time_str)

                    venue_div = row.find('div', class_='game_venue')
                    if venue_div:
                        venue = venue_div.get_text(strip=True)

                if not game_datetime:
                    print(f"  Skipped: Could not determine date/time for {our_team} game")
                    continue

                if game_datetime < datetime.now():
                    continue

                classification = self._classify_game(venue, category, our_team)
                if classification is None:
                    print(f"  Skipped: Unknown venue '{venue}' for {our_team} vs {opponent}")
                    continue
                calendar, title = classification

                venue_name = re.sub(r'\s*\([^)]*\)', '', venue).strip()

                if is_home:
                    notes = f"{opponent} @ {our_team}\n{venue_name}"
                else:
                    notes = f"{our_team} @ {opponent}\n{venue_name}"

                game = {
                    'title': title,
                    'start': game_datetime,
                    'end': game_datetime + timedelta(minutes=50),
                    'location': '',
                    'notes': notes,
                    'calendar': calendar,
                    'our_team': our_team,
                    'opponent': opponent,
                    'is_home': is_home
                }

                self.games.append(game)
                print(f"  Found: {title} - {game_datetime.strftime('%Y-%m-%d %H:%M')}")

            await iframe_page.close()
            await browser.close()

        return self.games

    @staticmethod
    def _applescript_date(dt, var_name):
        """Build an AppleScript snippet that sets `var_name` to a date,
        locale-independently (avoids `date "..."` string parsing)."""
        return (
            f'set {var_name} to (current date)\n'
            f'set day of {var_name} to 1\n'
            f'set year of {var_name} to {dt.year}\n'
            f'set month of {var_name} to {dt.month}\n'
            f'set day of {var_name} to {dt.day}\n'
            f'set hours of {var_name} to {dt.hour}\n'
            f'set minutes of {var_name} to {dt.minute}\n'
            f'set seconds of {var_name} to 0'
        )

    def add_to_calendar(self):
        """Add games to Apple Calendar using AppleScript."""
        if not self.games:
            return

        added_count = 0
        skipped_count = 0

        for game in self.games:
            start_decl = self._applescript_date(game['start'], 'startDate')
            end_decl = self._applescript_date(game['end'], 'endDate')
            calendar_name = game['calendar']

            check_script = f'''
            {start_decl}
            tell application "Calendar"
                tell calendar "{calendar_name}"
                    set eventExists to false
                    repeat with evt in (every event whose start date is startDate)
                        if summary of evt is "{game['title']}" then
                            set eventExists to true
                            exit repeat
                        end if
                    end repeat
                    return eventExists
                end tell
            end tell
            '''

            try:
                result = subprocess.run(["osascript", "-e", check_script],
                                      check=True, capture_output=True, text=True)
                exists = result.stdout.strip() == "true"

                if exists:
                    print(f"  Skipped: {game['title']} - {game['start'].strftime('%Y-%m-%d %H:%M')}")
                    skipped_count += 1
                    continue
            except subprocess.CalledProcessError as e:
                print(f"  Warning: Could not check if event exists for {game['title']}: {e}")
                print(f"  Attempting to add anyway...")
                pass

            add_script = f'''
            {start_decl}
            {end_decl}
            tell application "Calendar"
                tell calendar "{calendar_name}"
                    make new event with properties {{summary:"{game['title']}", start date:startDate, end date:endDate, location:"{game['location']}", description:"{game['notes']}"}}
                end tell
            end tell
            '''

            try:
                subprocess.run(["osascript", "-e", add_script], check=True, capture_output=True)
                print(f"  Added: {game['title']} - {game['start'].strftime('%Y-%m-%d %H:%M')}")
                added_count += 1
            except subprocess.CalledProcessError as e:
                print(f"  Error adding {game['title']}: {e}")
                print(f"  Make sure calendar '{calendar_name}' exists in Apple Calendar")

        print(f"\nAdded {added_count} games, skipped {skipped_count} duplicates")


async def main():
    mode = "gab" if len(sys.argv) > 1 and sys.argv[1].lower() == "gab" else "default"
    fetcher = DDLCGameFetcher(mode=mode)

    print("Fetching games from DDLC website...")
    if mode == "gab":
        print("Mode: gab")
    print(f"Looking for teams: {', '.join(fetcher.team_names)}\n")

    games = await fetcher.fetch_games()

    if games:
        print(f"\n{'='*60}")
        print(f"Found {len(games)} games for your teams")
        print(f"{'='*60}\n")
        print("Adding games to your calendars...\n")
        fetcher.add_to_calendar()
        print("\nDone!")
    else:
        print("\nNo games found for your teams.")
        print("Make sure the team names in .env are correct:")
        for team in fetcher.team_names:
            print(f"  - {team}")


if __name__ == "__main__":
    asyncio.run(main())
