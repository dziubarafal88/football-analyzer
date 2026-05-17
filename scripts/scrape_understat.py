"""
Scraper xG z Understat dla 5 lig: PL, Bundesliga, Serie A, La Liga, Ligue 1.
Uruchamiany przez GitHub Actions co 6h. Zapisuje JSON do data/xg.json.

UWAGA: Understat jest agresywnie blokowany. Skrypt używa requests z pełnymi
headerami i retry. Jeśli mimo to blokuje, alternatywne źródło to FBref.
"""
import json
import re
import os
import time
import sys

try:
    import requests
except ImportError:
    print("requests not installed, falling back to urllib")
    requests = None
    import urllib.request

LEAGUES = {
    "EPL": "PL",
    "Bundesliga": "BL1",
    "La_liga": "PD",
    "Serie_A": "SA",
    "Ligue_1": "FL1",
}

# Bieżący sezon
SEASON = "2025"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

def fetch_url(url, max_retries=3):
    """Fetch z retry i pełnymi nagłówkami."""
    for attempt in range(max_retries):
        try:
            if requests:
                r = requests.get(url, headers=HEADERS, timeout=30)
                print(f"    HTTP {r.status_code}, {len(r.content)} bytes")
                if r.status_code == 200 and len(r.content) > 1000:
                    return r.text
            else:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                    print(f"    HTTP {r.status}, {len(html)} chars")
                    if len(html) > 1000:
                        return html
        except Exception as e:
            print(f"    Attempt {attempt+1} failed: {e}")
        time.sleep(5 * (attempt+1))
    return None

def parse_understat(html, league_url):
    # Try datesData (mecze)
    m = re.search(r"datesData\s*=\s*JSON\.parse\('(.+?)'\)", html)
    if not m:
        # Different format - try teamsData
        m2 = re.search(r"teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html)
        if not m2:
            return None
        print(f"  Found teamsData format")
        try:
            raw = m2.group(1).encode().decode("unicode_escape")
            teams = json.loads(raw)
            # Convert teamsData -> matches format
            matches = []
            for team_id, team_data in teams.items():
                for h in team_data.get("history", []):
                    matches.append({
                        "id": f"{team_id}_{h.get('date','')}",
                        "date": h.get("date"),
                        "home": h.get("h_team") if h.get("h_a")=="h" else h.get("a_team", "?"),
                        "away": h.get("a_team") if h.get("h_a")=="h" else h.get("h_team", "?"),
                        "h_goals": int(h.get("goals", 0)) if h.get("h_a")=="h" else None,
                        "a_goals": None if h.get("h_a")=="h" else int(h.get("goals", 0)),
                        "h_xg": float(h.get("xG", 0)) if h.get("h_a")=="h" else None,
                        "a_xg": None if h.get("h_a")=="h" else float(h.get("xG", 0)),
                        "team_id": team_id,
                        "team_name": team_data.get("title", "?"),
                        "is_home": h.get("h_a")=="h",
                        "finished": True,
                        "raw": h
                    })
            return matches
        except Exception as e:
            print(f"  teamsData parse error: {e}")
            return None
    
    # Standard datesData format
    try:
        raw = m.group(1).encode().decode("unicode_escape")
        data = json.loads(raw)
        matches = []
        for m_obj in data:
            try:
                matches.append({
                    "id": m_obj["id"],
                    "date": m_obj["datetime"],
                    "home": m_obj["h"]["title"],
                    "home_id": m_obj["h"]["id"],
                    "away": m_obj["a"]["title"],
                    "away_id": m_obj["a"]["id"],
                    "h_goals": int(m_obj["goals"]["h"]) if m_obj["goals"]["h"] else None,
                    "a_goals": int(m_obj["goals"]["a"]) if m_obj["goals"]["a"] else None,
                    "h_xg": float(m_obj["xG"]["h"]) if m_obj["xG"]["h"] else None,
                    "a_xg": float(m_obj["xG"]["a"]) if m_obj["xG"]["a"] else None,
                    "finished": m_obj.get("isResult", False),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return matches
    except Exception as e:
        print(f"  datesData parse error: {e}")
        return None

def fetch_understat_league(league_url):
    url = f"https://understat.com/league/{league_url}/{SEASON}"
    print(f"  GET {url}")
    html = fetch_url(url)
    if not html:
        return None
    matches = parse_understat(html, league_url)
    if not matches:
        # Save HTML sample for debugging
        print(f"  No matches parsed. HTML preview: {html[:200]}")
        return None
    return matches

def main():
    out = {"updated": int(time.time()), "season": SEASON, "leagues": {}, "team_xg": {}}
    
    for league_url, code in LEAGUES.items():
        print(f"\nScraping {league_url}...")
        matches = fetch_understat_league(league_url)
        if matches is None:
            print(f"  ❌ Failed for {league_url}")
            continue
        out["leagues"][code] = matches
        
        # Aggregate per team
        team_aggr = {}
        for m in matches:
            if not m.get("finished") or m.get("h_xg") is None:
                continue
            ht = m["home"]
            team_aggr.setdefault(ht, []).append({
                "date": m["date"], "xg_for": m["h_xg"], "xg_against": m["a_xg"],
                "goals_for": m["h_goals"], "goals_against": m["a_goals"], "is_home": True
            })
            at = m["away"]
            team_aggr.setdefault(at, []).append({
                "date": m["date"], "xg_for": m["a_xg"], "xg_against": m["h_xg"],
                "goals_for": m["a_goals"], "goals_against": m["h_goals"], "is_home": False
            })
        
        for team, ms in team_aggr.items():
            ms.sort(key=lambda x: x["date"], reverse=True)
            out["team_xg"][f"{code}|{team}"] = ms[:15]
        
        print(f"  ✅ {league_url}: {len(matches)} meczów, {len(team_aggr)} drużyn")
        time.sleep(10)
    
    os.makedirs("data", exist_ok=True)
    with open("data/xg.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    
    total_matches = sum(len(ms) for ms in out["leagues"].values())
    total_teams = len(out["team_xg"])
    print(f"\n📊 Total: {total_matches} matches, {total_teams} teams across {len(out['leagues'])} leagues")
    
    if total_matches == 0:
        print("\n⚠️  WSZYSTKO PUSTE — Understat prawdopodobnie blokuje GitHub Actions IP.")
        print("    Rozwiązanie: użyj alternatywnego źródła (fbref-data-cup, openfootball)")
        # Don't fail - leave empty file so frontend handles gracefully
        sys.exit(0)

if __name__ == "__main__":
    main()
