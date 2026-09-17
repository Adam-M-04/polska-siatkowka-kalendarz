# Kalendarz reprezentacji Polski w siatkówce (mężczyźni)

Jeden kanał `.ics` ze **wszystkimi** meczami kadry — Liga Narodów, mistrzostwa
Europy i świata, igrzyska, Memoriał Wagnera i mecze towarzyskie. Odświeża się
sam co 4 godziny, trzyma 12 miesięcy historii i wszystko, co ogłoszone w przód.

Powstał dlatego, że oficjalne subskrypcje ECAL-a (te z Volleyball World) są
osobne dla każdego cyklu, nie obejmują ME ani towarzyskich, a poza sezonem
zwracają pusty plik — historia z nich znika.

---

## Subskrypcja w Apple Calendar

1. **Plik → Nowa subskrypcja kalendarza…**
2. Wklej adres:
   `https://<użytkownik>.github.io/polska-siatkowka-kalendarz/kadra-mezczyzn.ics`
3. **Odświeżanie: co godzinę.**
4. W sekcji „Usuń” **nie zaznaczaj „Powiadomienia”** — inaczej stracisz
   przypomnienie 15 minut przed pierwszą piłką, które jest wbudowane w feed.

Na iPhonie kalendarz dojedzie sam, jeśli w iCloud masz włączoną synchronizację
kalendarzy (subskrypcja dodana na Macu propaguje się na telefon).

Strona z gotowym przyciskiem subskrypcji: `https://<użytkownik>.github.io/polska-siatkowka-kalendarz/`

---

## Skąd biorą się dane

| Źródło | Co daje | Uwagi |
|---|---|---|
| **FIVB VIS** — `fivb.org/vis2009/XmlRequest.asmx` | Liga Narodów, MŚ, **EuroVolley** (CEV korzysta z tego samego systemu), igrzyska | Publiczne XML API, bez klucza. Zwraca czas w UTC i wyniki meczów. Źródło nadrzędne. |
| **PZPS** — `pzps.pl/strapi/api/events` | Mecze i turnieje towarzyskie, kanał TV, linki do biletów | Publiczne JSON API stojące za `pzps.pl/pl/kalendarium`. |
| **`overrides.toml`** | To, czego nie ma nigdzie indziej | Ręcznie, kilka wpisów rocznie. |

Przy duplikacie (ten sam mecz w obu API) wygrywa VIS, ale zabiera z wpisu PZPS
kanał TV i link do transmisji.

### Dwie pułapki, na które trzeba uważać

**PZPS zapisuje czas warszawski z doklejonym `Z`.** `startsAt` wygląda jak UTC,
ale nie jest — to lokalna godzina polska. Sprawdzone na 19 meczach Ligi Narodów
i ME 2026: zawsze dokładnie +2 h względem VIS (czyli CEST). Skrypt konwertuje
to przez `Europe/Warsaw`, więc zimą przesunięcie samo zmieni się na +1 h.

**W VIS każda kadra nazywa się „Poland”** — także U17, U21 i drużyny klubowe.
Dlatego mecze filtrujemy nie po nazwie drużyny, tylko po turnieju: płeć z pola
`Gender` plus czarna lista nazw (`U19`, `Boys`, `Club`, …).

### Czego nie ma w żadnym API

**Memoriał Wagnera.** Nie ma go w VIS (turniej towarzyski, poza strukturami
FIVB/CEV), a PZPS wpisał do swojego kalendarza edycję 2025, ale już nie 2026.
Trzy mecze rocznie — wpisuje się je ręcznie do `overrides.toml`.

Zdarzają się też błędy w danych PZPS: mecz VNL z Chicago mieli wpisany o dobę
za wcześnie. Takie wpisy wyrzuca się sekcją `[[drop]]`.

---

## Uruchomienie lokalnie

Wymaga tylko Pythona 3.11+ (żadnych zależności — `tomllib` i `zoneinfo` są
w bibliotece standardowej).

```bash
python3 generate.py --output public/kadra-mezczyzn.ics
```

Skrypt wypisuje na stderr pełną listę meczów z identyfikatorem źródła — to
z niego bierze się `id` do sekcji `[[drop]]`:

```
2026-07-20 Mon 03:00  [ vis:26541]  🏐 USA – Polska 0:3 · Liga Narodów 2026
```

Przydatne flagi:

| Flaga | Domyślnie | Znaczenie |
|---|---|---|
| `--history-days` | 365 | ile dni historii zostaje w kalendarzu |
| `--future-days` | 730 | jak daleko w przód pobierać |
| `--min-events` | 5 | poniżej tylu wydarzeń build pada i **nic nie publikuje** |

Ostatnia flaga jest zabezpieczeniem: gdyby któreś API zwróciło pustkę, lepiej
żeby build się wywalił, niż żeby Apple Calendar skasował wszystkie wydarzenia.

---

## Ręczne poprawki — `overrides.toml`

```toml
# dopisz mecz, którego nie ma w żadnym API
[[add]]
start = "2027-08-28 20:30"        # czas polski
title = "Polska – Słowenia"
competition = "Memoriał Wagnera 2027"
venue = "TAURON Arena Kraków"
tv = "Polsat Sport"

# wyrzuć błędny wpis ze źródła
[[drop]]
source = "pzps"
id = "826"
reason = "błędna data"
```

Wpisy starsze niż okno kalendarza są pomijane automatycznie — nie trzeba ich
kasować.

---

## Konfiguracja GitHuba (raz)

1. Wypchnij repo na GitHuba jako **publiczne** (GitHub Pages w prywatnym repo
   wymaga płatnego planu).
2. **Settings → Pages → Source: GitHub Actions.**
3. **Actions → Zbuduj i opublikuj kalendarz → Run workflow** — pierwszy build
   ręcznie, żeby nie czekać na harmonogram.

Dalej leci samo: cron co 4 godziny, build i publikacja na Pages.

### Dlaczego w workflow jest „keepalive”

GitHub wyłącza zaplanowane workflow w repo, w którym przez 60 dni nic się nie
działo. Między sezonami reprezentacji taka przerwa jest realna, więc jeśli
ostatni commit ma więcej niż 45 dni, workflow wypycha pusty commit i licznik
rusza od nowa.

---

## Czego świadomie nie ma

- **Kadry kobiet i młodzieżówek** — filtry są w `generate.py` (`VIS_GENDER_MEN`,
  `PZPS_CATEGORY_MEN`, `SKIP_TOURNAMENT`), więc dorobienie drugiego feedu to
  kwestia drugiego wywołania skryptu z innymi parametrami.
- **Meczów z nierozstrzygniętą drabinką.** Dopóki nie wiadomo, czy Polska gra
  ćwierćfinał, VIS ma tam puste nazwy drużyn i taki mecz nie trafia do
  kalendarza. Pojawi się w ciągu kilku godzin od losowania/awansu.
