"""Match a hospital's county name and state, as CMS writes them, to a county FIPS code.

Pure Python, no Spark: the inputs are small (about 5,400 hospitals, 3,222 counties per ACS
vintage) and the rules read better as plain code. The county list is the NAME column of the ACS
response for one vintage, so a hospital is matched against that vintage's geography.

Matching, in order, within the hospital's state:
1. Exact, on a key that ignores case, accents, punctuation and spacing, spells Saint as St.,
   expands a leading direction (`E. BATON ROUGE`), drops "the", and drops the county type
   (County, Parish, Borough, Census Area, City and Borough, Municipality, Municipio, Planning
   Region). An independent city keeps its "city": `BALTIMORE CITY` is Baltimore city (24510),
   `BALTIMORE` is Baltimore County (24005), and `JAMES CITY` is James City County.
2. Aliases for names no normalization reaches (CMS typos, "THE DISTRICT"), keyed by state.
3. Connecticut: from the 2022 vintage the county list is planning regions, so the hospital's
   town picks the region through the town list (`ct_county_subdivision`).
4. Alaska's Valdez-Cordova Census Area split in 2019; the town picks Chugach or Copper River.
5. Island areas (Guam, US Virgin Islands, Northern Mariana Islands, American Samoa) are not in
   the ACS; they get their Census FIPS code from a fixed table and no ACS row.
6. A fuzzy match within the state, accepted only when one county is clearly closest.
"""

import difflib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}  # fmt: skip

# Longest first, so "CITY AND BOROUGH" goes before "BOROUGH".
COUNTY_TYPES = (
    "CITY AND BOROUGH",
    "PLANNING REGION",
    "CENSUS AREA",
    "MUNICIPALITY",
    "MUNICIPIO",
    "BOROUGH",
    "COUNTY",
    "PARISH",
)
DIRECTIONS = {"E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}

# (state, key of the CMS name) to the key of the Census name. Typos in Hospital General
# Information (checked 2026-10-21), names no rule reaches, and counties renamed in 2015 under
# the same FIPS code, so a vintage before the rename still finds them.
ALIASES = {
    ("AK", "NORTHSLOPEBOROUH"): "NORTHSLOPE",
    ("AK", "NORTHWESTARTIC"): "NORTHWESTARCTIC",
    ("DC", "DISTRICT"): "DISTRICTOFCOLUMBIA",
    ("LA", "JEFFRSONDAVIS"): "JEFFERSONDAVIS",
    ("AK", "KUSILVAK"): "WADEHAMPTON",
    ("SD", "OGLALALAKOTA"): "SHANNON",
}

# CMS writes a village or section for some Connecticut hospitals; the town that contains it.
CT_VILLAGES = {
    "MANSFIELDCENTER": "MANSFIELD",
    "STAFFORDSPRINGS": "STAFFORD",
    "WILLIMANTIC": "WINDHAM",
}

# Valdez-Cordova (02261) became Chugach (02063) and Copper River (02066) in 2019.
VALDEZ_CORDOVA = "VALDEZCORDOVA"
VALDEZ_CORDOVA_TOWNS = {"VALDEZ": "CHUGACH", "CORDOVA": "CHUGACH", "GLENNALLEN": "COPPERRIVER"}

# Island areas: Census FIPS codes (2020 Island Areas census). No ACS county row exists.
ISLAND_AREAS = {
    ("GU", "GUAM"): ("66010", "Guam"),
    ("VI", "STCROIXISLAND"): ("78010", "St. Croix Island"),
    ("VI", "STCROIX"): ("78010", "St. Croix Island"),
    ("VI", "STJOHNISLAND"): ("78020", "St. John Island"),
    ("VI", "STJOHN"): ("78020", "St. John Island"),
    ("VI", "STTHOMASISLAND"): ("78030", "St. Thomas Island"),
    ("VI", "STTHOMAS"): ("78030", "St. Thomas Island"),
    ("MP", "ROTA"): ("69100", "Rota Municipality"),
    ("MP", "SAIPAN"): ("69110", "Saipan Municipality"),
    ("MP", "TINIAN"): ("69120", "Tinian Municipality"),
    ("AS", "EASTERNDISTRICT"): ("60010", "Eastern District"),
    ("AS", "MANUADISTRICT"): ("60020", "Manu'a District"),
    ("AS", "WESTERNDISTRICT"): ("60050", "Western District"),
}

FUZZY_CUTOFF = 0.85
FUZZY_MARGIN = 0.05


@dataclass(frozen=True)
class County:
    fips: str
    name: str


@dataclass(frozen=True)
class Match:
    county_fips: str | None
    county_name: str | None
    method: str


UNMATCHED = Match(None, None, "unmatched")


def _tokens(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[.'’]", "", ascii_name.upper())
    tokens = re.sub(r"[^A-Z0-9]+", " ", cleaned).split()
    if tokens and tokens[0] in DIRECTIONS:
        tokens[0] = DIRECTIONS[tokens[0]]
    return ["ST" if t == "SAINT" else t for t in tokens if t != "THE"]


def county_key(name: str) -> str:
    """`St. Louis city` -> STLOUISCITY, `Prince George's County` -> PRINCEGEORGES."""
    text = " ".join(_tokens(name))
    for county_type in COUNTY_TYPES:
        if text.endswith(" " + county_type):
            text = text[: -len(county_type) - 1]
            break
    return text.replace(" ", "")


def town_key(name: str) -> str:
    """A Connecticut town from the subdivision list (`Andover town`) or a CMS city."""
    text = " ".join(_tokens(name))
    text = re.sub(r" (TOWN|CITY)$", "", text)
    return text.replace(" ", "")


def place_name(census_name: str) -> str:
    """`Autauga County, Alabama` -> `Autauga County`."""
    return census_name.split(",")[0].strip()


class CountyMatcher:
    """Indexes one vintage's counties (and Connecticut towns) by state."""

    def __init__(self, counties: Iterable[dict], ct_towns: Iterable[dict] = ()):
        """`counties` and `ct_towns` are ACS rows: NAME, state and county codes (the town rows
        name the county that contains each town)."""
        self.by_state: dict[str, dict[str, County]] = {}
        for row in counties:
            county = County(row["state"] + row["county"], place_name(row["NAME"]))
            self.by_state.setdefault(row["state"], {})[county_key(county.name)] = county
        self.ct_town_county: dict[str, str] = {
            town_key(place_name(row["NAME"])): row["state"] + row["county"] for row in ct_towns
        }

    def _county(self, state_fips: str, key: str) -> County | None:
        return self.by_state.get(state_fips, {}).get(key)

    def _by_fips(self, fips: str) -> County | None:
        for county in self.by_state.get(fips[:2], {}).values():
            if county.fips == fips:
                return county
        return None

    def match(self, state: str, county_name: str, city: str = "") -> Match:
        state = state.strip().upper()
        key = county_key(county_name or "")
        if (state, key) in ISLAND_AREAS:
            return Match(*ISLAND_AREAS[(state, key)], "island_area")
        state_fips = STATE_FIPS.get(state)
        if state_fips is None or not key:
            return UNMATCHED
        if county := self._county(state_fips, key):
            return Match(county.fips, county.name, "exact")
        if (state, key) in ALIASES and (county := self._county(state_fips, ALIASES[state, key])):
            return Match(county.fips, county.name, "alias")
        town = town_key(city or "")
        if state == "CT":
            fips = self.ct_town_county.get(CT_VILLAGES.get(town, town))
            if fips and (county := self._by_fips(fips)):
                return Match(county.fips, county.name, "ct_town")
        if (
            state == "AK"
            and key == VALDEZ_CORDOVA
            and (county := self._county(state_fips, VALDEZ_CORDOVA_TOWNS.get(town, "")))
        ):
            return Match(county.fips, county.name, "town")
        return self._fuzzy(state_fips, key)

    def _fuzzy(self, state_fips: str, key: str) -> Match:
        candidates = self.by_state.get(state_fips, {})
        scored = sorted(
            ((difflib.SequenceMatcher(None, key, k).ratio(), k) for k in candidates),
            reverse=True,
        )
        if not scored or scored[0][0] < FUZZY_CUTOFF:
            return UNMATCHED
        if len(scored) > 1 and scored[0][0] - scored[1][0] < FUZZY_MARGIN:
            return UNMATCHED
        county = candidates[scored[0][1]]
        return Match(county.fips, county.name, "fuzzy")
