#!/usr/bin/env python3
"""
Generator kanału .ics z meczami reprezentacji Polski mężczyzn w siatkówce.

Źródła danych:

  1. FIVB VIS — publiczne XML API (bez klucza), https://www.fivb.org/vis2009/XmlRequest.asmx
     Pokrywa Ligę Narodów, mistrzostwa świata, EuroVolley (CEV też korzysta z VIS)
     oraz igrzyska olimpijskie. Zwraca czas w UTC, więc strefy są rozwiązane u źródła.

  2. PZPS — publiczne JSON API stojące za pzps.pl/pl/kalendarium,
     https://www.pzps.pl/strapi/api/events
     Dokłada mecze towarzyskie i turnieje towarzyskie oraz kanał TV.

  3. overrides.toml — ręczne uzupełnienia i poprawki dla tego, czego nie ma
     w żadnym z API (np. Memoriał Wagnera) albo co jest tam błędne.

Użycie:
    python3 generate.py --output public/kadra-mezczyzn.ics
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc

VIS_URL = "https://www.fivb.org/vis2009/XmlRequest.asmx"
PZPS_URL = "https://www.pzps.pl/strapi/api/events"
USER_AGENT = "polska-siatkowka-kalendarz/1.0 (+https://github.com/)"

CALNAME = "Reprezentacja Polski – siatkówka (mężczyźni)"
CALDESC = "Wszystkie mecze kadry: Liga Narodów, MŚ, ME, igrzyska, Memoriał Wagnera i towarzyskie."

# --- filtrowanie -----------------------------------------------------------

TEAM_VIS = "Poland"     # VIS nazywa każdą kadrę "Poland" — kluczowy jest filtr turnieju
TEAM_PZPS = "polska"    # fragment tytułu meczu po stronie PZPS
VIS_GENDER_MEN = "0"    # pole Gender turnieju w VIS: 0 = mężczyźni, 1 = kobiety
PZPS_CATEGORY_MEN = "VOLLEYBALL/NATIONAL-TEAMS/MEN"

# VIS zawiera też kadry młodzieżowe i rozgrywki klubowe pod tą samą nazwą "Poland".
SKIP_TOURNAMENT = re.compile(r"U1[5-9]|U2[0-3]|Boys|Girls|Junior|Youth|Club|\btest\b", re.I)

# Mecz uznajemy za ten sam, jeżeli start różni się o mniej niż tyle.
# 45 min — sprawdzone: rozbieżności VIS↔PZPS przy tych samych meczach to 0 min,
# a najbliższe różne mecze tego samego dnia dzieli ≥ 2 h.
DUPLICATE_WINDOW = timedelta(minutes=45)

MATCH_DURATION = timedelta(hours=2)

# Powiadomienie tyle minut przed pierwszą piłką.
ALARM_MINUTES = 15

# --- ładniejsze nazwy ------------------------------------------------------

COMPETITION_NAMES = [
    (re.compile(r"Men's Volleyball Nations League (\d{4})"), r"Liga Narodów \1"),
    (re.compile(r"CEV EuroVolley (\d{4}).*"), r"Mistrzostwa Europy \1"),
    (re.compile(r"FIVB Volleyball Men's World Championship (\d{4})"), r"Mistrzostwa Świata \1"),
    (re.compile(r"Men's Olympic Games (\d{4}).*"), r"Igrzyska Olimpijskie \1"),
    (re.compile(r"CEV Volleyball European League (\d{4}).*"), r"Liga Europejska \1"),
    (re.compile(r"FIVB Volleyball Men's Club World Championship (\d{4})"), r"KMŚ \1"),
]

COUNTRIES_PL = {
    "Poland": "Polska", "Argentina": "Argentyna", "Australia": "Australia",
    "Austria": "Austria", "Azerbaijan": "Azerbejdżan", "Belgium": "Belgia",
    "Brazil": "Brazylia", "Bulgaria": "Bułgaria", "Canada": "Kanada",
    "Chile": "Chile", "China": "Chiny", "Colombia": "Kolumbia", "Croatia": "Chorwacja",
    "Cuba": "Kuba", "Czechia": "Czechy", "Czech Republic": "Czechy", "Denmark": "Dania",
    "Egypt": "Egipt", "Estonia": "Estonia", "Finland": "Finlandia", "France": "Francja",
    "Germany": "Niemcy", "Greece": "Grecja", "Hungary": "Węgry", "Iran": "Iran",
    "Islamic Republic of Iran": "Iran", "Israel": "Izrael", "Italy": "Włochy",
    "Japan": "Japonia", "Latvia": "Łotwa", "Libya": "Libia", "Lithuania": "Litwa",
    "Mexico": "Meksyk", "Montenegro": "Czarnogóra", "Morocco": "Maroko",
    "Netherlands": "Holandia", "North Macedonia": "Macedonia Północna",
    "Norway": "Norwegia", "Portugal": "Portugalia", "Puerto Rico": "Portoryko",
    "Qatar": "Katar", "Romania": "Rumunia", "Serbia": "Serbia", "Slovakia": "Słowacja",
    "Slovenia": "Słowenia", "South Korea": "Korea Południowa", "Korea": "Korea Południowa",
    "Spain": "Hiszpania", "Sweden": "Szwecja", "Switzerland": "Szwajcaria",
    "Tunisia": "Tunezja", "Türkiye": "Turcja", "Turkey": "Turcja", "Ukraine": "Ukraina",
    "United States": "USA", "USA": "USA", "Venezuela": "Wenezuela",
}


def pl_country(name: str) -> str:
    return COUNTRIES_PL.get(name.strip(), name.strip())


def pl_competition(name: str) -> str:
    for pattern, repl in COMPETITION_NAMES:
        if pattern.fullmatch(name):
            return pattern.sub(repl, name)
    return name


# --- model -----------------------------------------------------------------

@dataclass
class Match:
    source: str           # "vis" | "pzps" | "manual"
    source_id: str
    start: datetime       # zawsze świadomy strefy
    title: str            # "Polska – Bułgaria"
    competition: str
    venue: str = ""
    url: str = ""
    tv: str = ""
    score: str = ""
    extra: list[str] = field(default_factory=list)
    provisional: bool = False   # wpis ręczny, który ma ustąpić danym z API

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def uid(self) -> str:
        return hashlib.sha1(self.key.encode()).hexdigest()[:20] + "@polska-siatkowka"

    @property
    def summary(self) -> str:
        score = f" {self.score}" if self.score else ""
        return f"🏐 {self.title}{score} · {self.competition}"


# --- HTTP ------------------------------------------------------------------

def fetch(url: str, params: dict | None = None, timeout: int = 45) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"BŁĄD: nie udało się pobrać {url.split('?')[0]}: {exc}")


# --- źródło 1: FIVB VIS ----------------------------------------------------

def vis_request(request_xml: str) -> ET.Element:
    return ET.fromstring(fetch(VIS_URL, {"Request": request_xml}))


def vis_tournaments(first_year: int, last_year: int) -> dict[str, dict]:
    seasons = " ".join(str(year) for year in range(first_year, last_year + 1))
    request = (
        '<Requests><Request Type="GetVolleyTournamentList" '
        'Fields="No Name Season Gender StartDate EndDate CountryCode">'
        f'<Filter Seasons="{seasons}"/></Request></Requests>'
    )
    tournaments = {
        t.get("No"): t.attrib
        for t in vis_request(request).iter("VolleyballTournament")
    }
    if not tournaments:
        raise SystemExit("BŁĄD: VIS nie zwrócił żadnego turnieju — API zmieniło się albo jest niedostępne.")
    return tournaments


def vis_matches(start: datetime, end: datetime) -> list[dict]:
    request = (
        '<Requests><Request Type="GetVolleyMatchList" '
        'Fields="No NoTournament DateTimeUtc TeamAName TeamBName '
        'MatchPointsA MatchPointsB Status City Hall LiveStreamUri BuyTicketsUrl">'
        f'<Filter FirstDate="{start:%Y-%m-%d}" LastDate="{end:%Y-%m-%d}"/>'
        "</Request></Requests>"
    )
    return [m.attrib for m in vis_request(request).iter("VolleyballMatch")]


def from_vis(start: datetime, end: datetime) -> list[Match]:
    # Sezon w VIS bywa przesunięty względem roku kalendarzowego, więc bierzemy z zapasem.
    tournaments = vis_tournaments(start.year - 1, end.year + 1)
    out: list[Match] = []

    for raw in vis_matches(start, end):
        team_a, team_b = raw.get("TeamAName", ""), raw.get("TeamBName", "")
        if TEAM_VIS not in (team_a, team_b) or not raw.get("DateTimeUtc"):
            continue

        tournament = tournaments.get(raw.get("NoTournament"), {})
        competition = tournament.get("Name", "")
        if not competition or SKIP_TOURNAMENT.search(competition):
            continue
        if tournament.get("Gender") != VIS_GENDER_MEN:
            continue

        moment = datetime.strptime(raw["DateTimeUtc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if not start <= moment <= end:
            continue

        points_a, points_b = raw.get("MatchPointsA"), raw.get("MatchPointsB")
        finished = raw.get("Status") == "25" and points_a and points_b

        out.append(Match(
            source="vis",
            source_id=raw["No"],
            start=moment,
            title=f"{pl_country(team_a)} – {pl_country(team_b)}",
            competition=pl_competition(competition),
            venue=", ".join(x for x in (raw.get("Hall"), raw.get("City")) if x),
            url=raw.get("LiveStreamUri") or raw.get("BuyTicketsUrl") or "",
            score=f"{points_a}:{points_b}" if finished else "",
        ))
    return out


# --- źródło 2: PZPS --------------------------------------------------------

def from_pzps(start: datetime, end: datetime) -> list[Match]:
    payload = json.loads(fetch(PZPS_URL, {
        "pagination[limit]": 1000,
        "locale": "pl-PL",
        "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "category": "",
    }))
    data = payload.get("data")
    if not isinstance(data, dict) or "matches" not in data:
        raise SystemExit("BŁĄD: PZPS zwrócił nieoczekiwany kształt odpowiedzi — API się zmieniło.")

    raw_matches = list(data["matches"])
    for event in data.get("events", []):
        raw_matches.extend(event.get("matches") or [])

    out: list[Match] = []
    seen: set = set()
    for raw in raw_matches:
        if raw["id"] in seen:
            continue
        seen.add(raw["id"])

        category = (raw.get("category") or {}).get("categoryType")
        if category != PZPS_CATEGORY_MEN:
            continue
        if TEAM_PZPS not in raw.get("title", "").lower():
            continue

        # UWAGA: PZPS zapisuje czas warszawski, ale dokleja "Z" (fałszywy UTC).
        # Zweryfikowane na 19 meczach VNL i ME 2026 — zawsze dokładnie +2 h względem VIS (CEST).
        naive = datetime.strptime(raw["startsAt"], "%Y-%m-%dT%H:%M:%S.%fZ")
        moment = naive.replace(tzinfo=WARSAW).astimezone(UTC)
        if not start <= moment <= end:
            continue

        # Tytuły bywają w formie "ćwierćfinał | Polska-Ukraina" — rozdzielamy,
        # a potem ujednolicamy myślnik ("Polska-Finlandia" → "Polska – Finlandia").
        parts = [p.strip() for p in re.sub(r"\s+", " ", raw["title"]).split("|")]
        title = max(parts, key=lambda p: p.lower().count("polska"))
        stage = ", ".join(p for p in parts if p != title)
        title = re.sub(r"\s*[-–]\s*", " – ", title)

        out.append(Match(
            source="pzps",
            source_id=str(raw["id"]),
            start=moment,
            title=title,
            competition=raw.get("tournamentTitle") or "Mecz reprezentacji",
            venue=raw.get("place") or "",
            url=raw.get("transmissionLink") or raw.get("ticketsLink") or "",
            tv=raw.get("channel") or "",
            extra=[stage] if stage else [],
        ))
    return out


# --- źródło 3: overrides.toml ---------------------------------------------

def load_overrides(path: Path) -> tuple[list[Match], set[str]]:
    if not path.exists():
        return [], set()

    config = tomllib.loads(path.read_text(encoding="utf-8"))

    added: list[Match] = []
    for entry in config.get("add", []):
        local = datetime.strptime(entry["start"], "%Y-%m-%d %H:%M").replace(tzinfo=WARSAW)
        # Identyfikator z treści, nie z kolejności w pliku — przestawienie wpisów
        # nie może zmienić UID-a, bo kalendarz skasowałby i dodał je od nowa.
        slug = re.sub(r"[^a-z0-9]+", "-", entry["title"].lower().replace("ł", "l")).strip("-")
        added.append(Match(
            source="manual",
            source_id=entry.get("id") or f"{local:%Y%m%d}-{slug}",
            start=local.astimezone(UTC),
            title=entry["title"],
            competition=entry["competition"],
            venue=entry.get("venue", ""),
            url=entry.get("url", ""),
            tv=entry.get("tv", ""),
            score=entry.get("score", ""),
            extra=[entry["stage"]] if entry.get("stage") else [],
            provisional=bool(entry.get("provisional", False)),
        ))

    dropped = {f"{d['source']}:{d['id']}" for d in config.get("drop", [])}
    return added, dropped


# --- scalanie --------------------------------------------------------------

SOURCE_PRIORITY = {"manual": 0, "vis": 1, "pzps": 2}
PROVISIONAL_PRIORITY = 9   # niżej niż każde źródło — ustępuje, gdy API dogoni


def priority(match: Match) -> int:
    return PROVISIONAL_PRIORITY if match.provisional else SOURCE_PRIORITY[match.source]


def merge(*groups: list[Match]) -> list[Match]:
    """Przy duplikacie wygrywa źródło o wyższym priorytecie, ale zabiera z przegranego
    to, czego samo nie ma (kanał TV, link, miejsce)."""
    everything = sorted(
        (m for group in groups for m in group),
        key=lambda m: (m.start, priority(m)),
    )

    merged: list[Match] = []
    for candidate in everything:
        twin = next(
            (m for m in merged if abs(m.start - candidate.start) < DUPLICATE_WINDOW),
            None,
        )
        if twin is None:
            merged.append(candidate)
            continue
        twin.tv = twin.tv or candidate.tv
        twin.url = twin.url or candidate.url
        twin.venue = twin.venue or candidate.venue
        for note in candidate.extra:
            if note not in twin.extra:
                twin.extra.append(note)
    return merged


# --- zapis ICS -------------------------------------------------------------

def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", r"\;")
                .replace(",", r"\,").replace("\n", r"\n"))


def fold(line: str) -> str:
    """Zawijanie linii do 75 oktetów (RFC 5545), bez rozcinania znaków UTF-8."""
    chunks, data = [], line.encode("utf-8")
    while len(data) > 73:
        cut = 73
        while cut > 0 and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        chunks.append(data[:cut].decode("utf-8"))
        data = b" " + data[cut:]
    chunks.append(data.decode("utf-8"))
    return "\r\n".join(chunks)


def build_ics(matches: list[Match]) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//polska-siatkowka-kalendarz//PL",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        fold(f"X-WR-CALNAME:{escape(CALNAME)}"),
        fold(f"X-WR-CALDESC:{escape(CALDESC)}"),
        "X-WR-TIMEZONE:Europe/Warsaw",
        "REFRESH-INTERVAL;VALUE=DURATION:PT4H",
        "X-PUBLISHED-TTL:PT4H",
    ]

    for match in sorted(matches, key=lambda m: m.start):
        start = match.start.astimezone(UTC)
        description = " · ".join(filter(None, [
            match.competition,
            *match.extra,
            f"TV: {match.tv}" if match.tv else "",
            f"źródło: {match.source.upper()}",
        ]))

        lines += [
            "BEGIN:VEVENT",
            f"UID:{match.uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
            f"DTEND:{start + MATCH_DURATION:%Y%m%dT%H%M%SZ}",
            fold(f"SUMMARY:{escape(match.summary)}"),
            fold(f"DESCRIPTION:{escape(description)}"),
            "TRANSP:TRANSPARENT",
        ]
        if match.venue:
            lines.append(fold(f"LOCATION:{escape(match.venue)}"))
        if match.url:
            lines.append(fold(f"URL:{match.url}"))
        lines += [
            "BEGIN:VALARM",
            f"TRIGGER:-PT{ALARM_MINUTES}M",
            "ACTION:DISPLAY",
            fold(f"DESCRIPTION:{escape(match.title)} — start za {ALARM_MINUTES} min"),
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# --- main ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=Path("public/kadra-mezczyzn.ics"))
    parser.add_argument("--overrides", type=Path, default=Path("overrides.toml"))
    parser.add_argument("--history-days", type=int, default=365,
                        help="ile dni wstecz trzymać w kalendarzu (domyślnie 365)")
    parser.add_argument("--future-days", type=int, default=730,
                        help="ile dni w przód pobierać (domyślnie 730)")
    parser.add_argument("--min-events", type=int, default=5,
                        help="poniżej tylu wydarzeń build kończy się błędem i nic nie publikuje")
    args = parser.parse_args()

    now = datetime.now(UTC)
    start, end = now - timedelta(days=args.history_days), now + timedelta(days=args.future_days)
    print(f"Okno: {start:%Y-%m-%d} … {end:%Y-%m-%d}", file=sys.stderr)

    vis = from_vis(start, end)
    pzps = from_pzps(start, end)
    manual, dropped = load_overrides(args.overrides)
    print(f"Pobrano — VIS: {len(vis)}, PZPS: {len(pzps)}, ręczne: {len(manual)}, "
          f"do pominięcia: {len(dropped)}", file=sys.stderr)

    kept = [m for m in vis + pzps + manual
            if m.key not in dropped and start <= m.start <= end]
    matches = merge(kept)

    if len(matches) < args.min_events:
        raise SystemExit(
            f"BŁĄD: tylko {len(matches)} wydarzeń (próg: {args.min_events}). "
            "Nie publikuję — poprzednia wersja kalendarza zostaje nietknięta."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_ics(matches), encoding="utf-8", newline="")

    print(f"\nZapisano {len(matches)} meczów do {args.output}\n", file=sys.stderr)
    for match in sorted(matches, key=lambda m: m.start):
        local = match.start.astimezone(WARSAW)
        flag = " (tymczasowy)" if match.provisional else ""
        print(f"  {local:%Y-%m-%d %a %H:%M}  [{match.key:>10}]  {match.summary}{flag}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
