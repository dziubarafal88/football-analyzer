"""
Scraper xG z Understat dla 6 lig (PL, Bundesliga, Serie A, La Liga, Ligue 1, RPL).
Uruchamiany przez GitHub Actions co 6h. Zapisuje JSON do data/xg.json.
"""
import json
import re
import os
import time
import urllib.request

LEAGUES = {
    "EPL": "PL",       # Premier League
    "Bundesliga": "BL1",
    "La_liga": "PD",
    "Serie_A": "SA",
    "Ligue_1": "FL1",
    "RFPL": "RU",      # Russian Premier
}

# Bieżący sezon - dla maja 2026 = 2025/26
SEASON = "2025"  # Understat używa roku startu sezonu

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

def fetch_understat_league(league_url):
    """Pobierz wszystkie mecze z danej ligi i sezonu."""
    url = f"https://understat.com/league/{league_url}/{SEASON}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8")
    except Exception as e:
        print(f"  ❌ {league_url}: {e}")
        return None
    
    # Wyciągnij datesData (JSON osadzony w JavaScripcie)
    m = re.search(r"datesData\s*=\s*JSON\.parse\('(.+?)'\)", html)
    if not m:
        print(f"  ❌ {league_url}: no datesData")
        return None
    
    # Decodowanie escape \xAB itp.
    raw = m.group(1).encode().decode("unicode_escape")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ❌ {league_url}: JSON decode error")
        return None
    
    matches = []
    for m in data:
        try:
            matches.append({
                "id": m["id"],
                "date": m["datetime"],
                "home": m["h"]["title"],
                "home_id": m["h"]["id"],
                "away": m["a"]["title"],
                "away_id": m["a"]["id"],
                "h_goals": int(m["goals"]["h"]) if m["goals"]["h"] else None,
                "a_goals": int(m["goals"]["a"]) if m["goals"]["a"] else None,
                "h_xg": float(m["xG"]["h"]) if m["xG"]["h"] else None,
                "a_xg": float(m["xG"]["a"]) if m["xG"]["a"] else None,
                "finished": m.get("isResult", False),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return matches

def main():
    out = {"updated": int(time.time()), "season": SEASON, "leagues": {}, "team_xg": {}}
    
    for league_url, code in LEAGUES.items():
        print(f"Scraping {league_url}...")
        matches = fetch_understat_league(league_url)
        if matches is None:
            continue
        out["leagues"][code] = matches
        
        # Agreguj xG per drużyna - ostatnie 10 meczów
        team_aggr = {}
        for m in matches:
            if not m["finished"] or m["h_xg"] is None:
                continue
            # Home team
            ht = m["home"]
            team_aggr.setdefault(ht, []).append({
                "date": m["date"], "xg_for": m["h_xg"], "xg_against": m["a_xg"],
                "goals_for": m["h_goals"], "goals_against": m["a_goals"], "is_home": True
            })
            # Away team
            at = m["away"]
            team_aggr.setdefault(at, []).append({
                "date": m["date"], "xg_for": m["a_xg"], "xg_against": m["h_xg"],
                "goals_for": m["a_goals"], "goals_against": m["h_goals"], "is_home": False
            })
        
        # Trzymaj ostatnie 15 meczów per drużyna
        for team, ms in team_aggr.items():
            ms.sort(key=lambda x: x["date"], reverse=True)
            out["team_xg"][f"{code}|{team}"] = ms[:15]
        
        print(f"  ✅ {league_url}: {len(matches)} meczów, {len(team_aggr)} drużyn")
        time.sleep(8)  # respektuj Understat, nie nadużywaj
    
    # Zapisz
    os.makedirs("data", exist_ok=True)
    with open("data/xg.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    
    total_matches = sum(len(ms) for ms in out["leagues"].values())
    total_teams = len(out["team_xg"])
    print(f"\n✅ Total: {total_matches} matches, {total_teams} teams")

if __name__ == "__main__":
    main()
