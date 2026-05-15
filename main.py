#!/usr/bin/env python3
"""
Fetch hockey games from DDLC website and add them to Apple Calendar.
"""

import asyncio
import os
import re
import subprocess
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from dotenv import load_dotenv


class DDLCGameFetcher:
    FRENCH_MONTHS = {
        'janvier': 1, 'février': 2, 'mars': 3, 'avril': 4,
        'mai': 5, 'juin': 6, 'juillet': 7, 'août': 8,
        'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12
    }

    def __init__(self, url="https://www.ddlc.ca/ligues/calendrier/"):
        load_dotenv()
        team_names_str = os.getenv('TEAM_NAMES')
        if not team_names_str:
            raise ValueError("TEAM_NAMES not found in .env file. Please configure your team names.")
        self.team_names = [name.strip() for name in team_names_str.split(',')]
        season = os.getenv('SEASON')
        if not season:
            raise ValueError("SEASON not found in .env file. Please configure the season label (e.g. 'Adulte | ÉTÉ 2026').")
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

    async def fetch_games(self):
        """Fetch games from the DDLC website using Playwright."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
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

            try:
                print(f"Selecting season '{self.season}'...")
                season_dropdown = iframe_page.locator(
                    'button, .dropdown-toggle, [class*="dropdown"]'
                ).filter(has_text=re.compile(r'(ÉTÉ|Automne/Hiver|Toutes les saisons)'))
                if await season_dropdown.count() > 0:
                    await season_dropdown.first.click()
                    await asyncio.sleep(1)
                    option = iframe_page.locator(
                        'li, a, [role="option"], .dropdown-item'
                    ).get_by_text(self.season, exact=True).first
                    await option.click()
                    await asyncio.sleep(3)
                else:
                    print("Warning: Could not find season dropdown, using default season")
            except Exception as e:
                print(f"Warning: Error selecting season '{self.season}': {e}")
                print("Continuing with default season...")

            try:
                list_view_button = await iframe_page.query_selector('label.list_view[data-view="list"]')
                if list_view_button:
                    print("Switching to full calendar view...")
                    await list_view_button.click()
                    await asyncio.sleep(5)
                else:
                    print("Warning: Could not find full calendar view button, using default view")
            except Exception as e:
                print(f"Warning: Error switching to full calendar view: {e}")
                print("Continuing with default view...")

            content = await iframe_page.content()
            soup = BeautifulSoup(content, 'html.parser')

            current_date_str = None
            schedule_table = soup.find('table', class_='schedule_table')

            if not schedule_table:
                print("Error: Could not find schedule table")
                await iframe_page.close()
                await browser.close()
                return self.games

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
                    if team in self.team_names:
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

                if "(St-Aug" in venue or "(st-aug" in venue.lower():
                    calendar = "Dek St-Aug"
                    title = f"game {category}"
                elif "(Chauveau" in venue or "(chauveau" in venue.lower():
                    calendar = "Dek Chauveau"
                    title = f"game {category}"
                elif "lévis" in venue.lower() or "levis" in venue.lower():
                    calendar = "Autre"
                    title = f"game {category} Levis"
                else:
                    print(f"  Skipped: Unknown venue '{venue}' for {our_team} vs {opponent}")
                    continue

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

    def add_to_calendar(self):
        """Add games to Apple Calendar using AppleScript."""
        if not self.games:
            return

        added_count = 0
        skipped_count = 0

        for game in self.games:
            start_str = game['start'].strftime('%m/%d/%Y %I:%M:%S %p')
            end_str = game['end'].strftime('%m/%d/%Y %I:%M:%S %p')
            calendar_name = game['calendar']

            check_script = f'''
            tell application "Calendar"
                tell calendar "{calendar_name}"
                    set eventExists to false
                    set checkDate to date "{start_str}"
                    repeat with evt in (every event whose start date is checkDate)
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
            tell application "Calendar"
                tell calendar "{calendar_name}"
                    make new event with properties {{summary:"{game['title']}", start date:date "{start_str}", end date:date "{end_str}", location:"{game['location']}", description:"{game['notes']}"}}
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
    fetcher = DDLCGameFetcher()

    print("Fetching games from DDLC website...")
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
