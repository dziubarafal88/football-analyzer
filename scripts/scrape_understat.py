"""
Hybrydowy scraper xG:
1. Próba Understat (zwykle blokowany dla GitHub Actions)
2. Fallback: FBref via webscraping (StatsBomb-powered xG, mniej agresywna blokada)
3. Fallback: football-data.co.uk CSV (historyczne dane, mecze zakończone)

Zapisuje wszystko do data/xg.json w spójnym formacie.
"""
import json
import re
import os
import time
import sys

try:
    import requests
except ImportError:
    requests = None
    import urllib.request

LEAGUES = {
    "EPL": "PL",
    "Bundesliga": "BL1",
    "La_liga": "PD",
    "Serie_A": "SA",
    "Ligue_1": "FL1",
}

# Mapowanie kodów ligi do football-data.co.uk
FOOTBALL_DATA_CO_UK = {
    "PL": "E0", "BL1": "D1", "PD": "SP1", "SA": "I1", "FL1": "F1"
}

SEASON = "2025"  # Bieżący sezon Understat
FD_SEASONS = ["2526", "2425"]  # football-data.co.uk format

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

def fetch_url(url, max_retries=2):
    for attempt in range(max_retries):
        try:
            if requests:
                r = requests.get(url, headers=HEADERS, timeout=30)
                print(f"    HTTP {r.status_code}, {len(r.content)} bytes")
                if r.status_code == 200 and len(r.content) > 500:
                    return r.text
            else:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                    print(f"    HTTP {r.status}, {len(html)} chars")
                    if len(html) > 500:
                        return html
        except Exception as e:
            print(f"    Attempt {attempt+1}: {e}")
        time.sleep(5)
    return None

# ============================================================
# ŹRÓDŁO 1: Understat (próba z wieloma patternami)
# ============================================================
def parse_understat(html):
    matches = []
    # Pattern 1: datesData = JSON.parse('...')
    m = re.search(r"datesData\s*=\s*JSON\.parse\('([^']+)'\)", html)
    if not m:
        # Pattern 2: var datesData = JSON.parse(...)
        m = re.search(r"var\s+datesData\s*=\s*JSON\.parse\('([^']+)'\)", html)
    if not m:
        # Pattern 3: datesData = JSON.parse("...")
        m = re.search(r'datesData\s*=\s*JSON\.parse\("([^"]+)"\)', html)
    if not m:
        print(f"    No datesData pattern found. Searching alternative patterns...")
        # Search for what JS variables ARE there
        all_vars = re.findall(r"(\w+Data)\s*=\s*JSON", html[:10000])
        print(f"    Available JS vars: {set(all_vars)}")
        return None
    
    try:
        raw = m.group(1).encode().decode("unicode_escape")
        data = json.loads(raw)
        for m_obj in data:
            try:
                matches.append({
                    "id": str(m_obj["id"]),
                    "date": m_obj["datetime"],
                    "home": m_obj["h"]["title"],
                    "away": m_obj["a"]["title"],
                    "h_goals": int(m_obj["goals"]["h"]) if m_obj["goals"]["h"] not in (None, "") else None,
                    "a_goals": int(m_obj["goals"]["a"]) if m_obj["goals"]["a"] not in (None, "") else None,
                    "h_xg": float(m_obj["xG"]["h"]) if m_obj["xG"]["h"] not in (None, "") else None,
                    "a_xg": float(m_obj["xG"]["a"]) if m_obj["xG"]["a"] not in (None, "") else None,
                    "finished": m_obj.get("isResult", False),
                    "source": "understat"
                })
            except (KeyError, ValueError, TypeError):
                continue
        return matches if matches else None
    except Exception as e:
        print(f"    Parse error: {e}")
        return None

def try_understat(league_url):
    url = f"https://understat.com/league/{league_url}/{SEASON}"
    print(f"  [Understat] GET {url}")
    html = fetch_url(url)
    if not html:
        return None
    return parse_understat(html)

# ============================================================
# ŹRÓDŁO 2: football-data.co.uk CSV (kursy + xG dla wybranych)
# ============================================================
def parse_fd_csv(text, code):
    """Parsuj CSV football-data.co.uk - zawiera bramki, daty, drużyny."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return None
    headers = [h.strip() for h in lines[0].split(",")]
    
    def col(name):
        try: return headers.index(name)
        except ValueError: return -1
    
    iDate = col("Date")
    iHome = col("HomeTeam")
    iAway = col("AwayTeam")
    iFTHG = col("FTHG")  # Full Time Home Goals
    iFTAG = col("FTAG")  # Full Time Away Goals
    
    if iHome < 0 or iAway < 0:
        return None
    
    matches = []
    for line in lines[1:]:
        cols = line.split(",")
        if len(cols) < 5:
            continue
        try:
            date_raw = cols[iDate] if iDate >= 0 else ""
            # Parse DD/MM/YYYY or DD/MM/YY
            try:
                parts = date_raw.split("/")
                if len(parts) == 3:
                    d, mo, y = parts
                    if len(y) == 2: y = "20" + y
                    date_iso = f"{y}-{mo.zfill(2)}-{d.zfill(2)} 15:00:00"
                else:
                    date_iso = date_raw
            except:
                date_iso = date_raw
            
            home = cols[iHome].strip()
            away = cols[iAway].strip()
            hg = int(cols[iFTHG]) if iFTHG >= 0 and cols[iFTHG] else None
            ag = int(cols[iFTAG]) if iFTAG >= 0 and cols[iFTAG] else None
            
            if not home or not away:
                continue
            
            matches.append({
                "id": f"fd_{code}_{date_iso}_{home}_{away}",
                "date": date_iso,
                "home": home,
                "away": away,
                "h_goals": hg,
                "a_goals": ag,
                "h_xg": None,  # football-data nie ma xG ale ma wyniki
                "a_xg": None,
                "finished": hg is not None and ag is not None,
                "source": "football-data.co.uk"
            })
        except Exception:
            continue
    return matches

def try_football_data_co_uk(code):
    """Pobierz CSV dla danej ligi z football-data.co.uk."""
    fd_code = FOOTBALL_DATA_CO_UK.get(code)
    if not fd_code:
        return None
    all_matches = []
    for season in FD_SEASONS:
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{fd_code}.csv"
        print(f"  [FD.co.uk] GET {url}")
        text = fetch_url(url)
        if not text:
            continue
        matches = parse_fd_csv(text, code)
        if matches:
            all_matches.extend(matches)
            print(f"    Got {len(matches)} matches for {season}")
    return all_matches if all_matches else None

# ============================================================
# MAIN
# ============================================================
def main():
    out = {"updated": int(time.time()), "season": SEASON, "leagues": {}, "team_xg": {}}
    
    for league_url, code in LEAGUES.items():
        print(f"\n══ {league_url} ({code}) ══")
        
        # Try Understat first
        matches = try_understat(league_url)
        if matches:
            print(f"  ✅ Understat: {len(matches)} matches")
        else:
            # Fallback to football-data.co.uk
            print(f"  ⚠️  Understat failed, trying football-data.co.uk...")
            matches = try_football_data_co_uk(code)
            if matches:
                print(f"  ✅ FD.co.uk: {len(matches)} matches")
            else:
                print(f"  ❌ All sources failed for {code}")
                continue
        
        out["leagues"][code] = matches
        
        # Aggregate per team
        team_aggr = {}
        for m in matches:
            if not m.get("finished"):
                continue
            ht = m["home"]
            team_aggr.setdefault(ht, []).append({
                "date": m["date"],
                "xg_for": m.get("h_xg"),
                "xg_against": m.get("a_xg"),
                "goals_for": m.get("h_goals"),
                "goals_against": m.get("a_goals"),
                "is_home": True
            })
            at = m["away"]
            team_aggr.setdefault(at, []).append({
                "date": m["date"],
                "xg_for": m.get("a_xg"),
                "xg_against": m.get("h_xg"),
                "goals_for": m.get("a_goals"),
                "goals_against": m.get("h_goals"),
                "is_home": False
            })
        
        for team, ms in team_aggr.items():
            ms.sort(key=lambda x: x["date"], reverse=True)
            out["team_xg"][f"{code}|{team}"] = ms[:15]
        
        print(f"  📊 {len(team_aggr)} teams aggregated")
        time.sleep(3)
    
    os.makedirs("data", exist_ok=True)
    with open("data/xg.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    
    total_matches = sum(len(ms) for ms in out["leagues"].values())
    total_teams = len(out["team_xg"])
    has_xg = sum(1 for matches in out["leagues"].values() for m in matches if m.get("h_xg") is not None)
    print(f"\n══════════════════════════════════════")
    print(f"📊 SUMMARY: {total_matches} matches, {total_teams} teams across {len(out['leagues'])} leagues")
    print(f"   With xG data: {has_xg} / {total_matches} ({100*has_xg//max(total_matches,1)}%)")
    print(f"══════════════════════════════════════")

if __name__ == "__main__":
    main()
