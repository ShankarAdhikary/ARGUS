"""Which jurisdiction (district/division) owns each police station.

The FIR records carry a station but no jurisdiction, so scoping search and graph results
(PRD FR-10) needs this mapping. The assignments below are SYNTHETIC — they group the demo
dataset's stations into districts. In production, replace this with the police-station master
from the source system (e.g. CCTNS) and keep it in the database, not in code.

Stations that are not listed are "Unassigned": they are visible only to roles that are not
scoped by jurisdiction, so an unmapped record never leaks to a district officer.
"""

from __future__ import annotations

UNASSIGNED = "Unassigned"

_DISTRICTS: dict[str, list[str]] = {
    "Central District": [
        "Central Police Station", "Connaught Place Police Station", "Sector-7 Police Station",
        "Lajpat Nagar Police Station", "Hauz Khas Police Station", "Saket Police Station",
    ],
    "Harbor Division": [
        "Harbor Police Station", "Riverside Police Station", "Fort Police Station",
        "Old Town Police Station", "Airport Road Police Station",
    ],
    "East Market Zone": [
        "East Market Police Station", "Nehru Place PS", "Northgate Police Station",
        "Rohini Sector-18 PS", "Pitampura Police Station",
    ],
    "West Range": [
        "Janakpuri Police Station", "Palam Village Police Station", "Dwarka Sector-10 PS", "Bijwasan PS",
        "Kapashera Police Station", "Uttam Nagar Police Station", "Paschim Vihar PS", "Vasant Kunj Police Station",
    ],
    "Finance Quarter": ["High-Risk Financial Crimes Unit"],
}

STATION_JURISDICTION: dict[str, str] = {
    station: district for district, stations in _DISTRICTS.items() for station in stations
}


def jurisdiction_for_station(station: str | None) -> str:
    return STATION_JURISDICTION.get((station or "").strip(), UNASSIGNED)


def stations_in(jurisdiction: str) -> list[str]:
    return list(_DISTRICTS.get(jurisdiction, []))
