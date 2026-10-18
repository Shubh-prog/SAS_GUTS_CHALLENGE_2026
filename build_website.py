"""
Run the investigation and write index.html.

Everything shown on the page (people, flags, meetings, timeline range) is
derived from the data, then injected into site_template.html as JSON.

    python build_website.py
"""

import json
import math
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import pandas as pd

from anomaly_detection import run_full_audit
from data_loader import load_all_data
from mastermind import find_mastermind

ROOT = Path(__file__).parent
TEMPLATE = ROOT / "site_template.html"
OUTPUT = ROOT / "index.html"
PLACEHOLDER = "/*CASE_DATA*/"
TARGET_BUILDING = "Boyd Orr Building"

# Walking routes come from OpenStreetMap's public foot router. Each one is
# fetched once and kept in ROUTES_CACHE, so later builds work offline.
ROUTES_CACHE = ROOT / "routes_cache.json"
ROUTING_URL = ("https://routing.openstreetmap.de/routed-foot/route/v1/foot/"
               "{a[lon]},{a[lat]};{b[lon]},{b[lat]}?overview=full&geometries=geojson")


def build_buildings(locations: pd.DataFrame) -> dict:
    return {
        row["Building Name"]: {
            "name": row["Building Name"],
            "lat": float(row["Latitude"]),
            "lon": float(row["Longitude"]),
            "hours": "24 hours" if row["Is_24h"] else row["Opening Times"],
            "is_target": row["Building Name"] == TARGET_BUILDING,
        }
        for _, row in locations.iterrows()
    }


def build_person(student: pd.Series, visits: pd.DataFrame, evidence: list,
                 meetings: list, is_mastermind: bool) -> dict:
    return {
        "id": student["Student ID"],
        "name": student["Name"],
        "subject": student["Subject"],
        "age": int(student["Age"]),
        "sex": student["Sex"],
        "year": int(student["Year of Study"]),
        "height": int(student["Height"]),
        "color": student["Hair colour"],
        "societies": list(student["Societies_List"]),
        "is_mastermind": is_mastermind,
        "evidence": evidence,
        "meetings": sorted(meetings, key=lambda m: m["start"]),
        "visits": [
            {"location": v["Location"], "start": int(v["Start_Minutes"]), "end": int(v["End_Minutes"])}
            for _, v in visits.sort_values("Start_Minutes").iterrows()
        ],
    }


def fetch_route(a: dict, b: dict):
    """Footpath from building a to building b as [[lat, lon], ...], or None."""
    request = urllib.request.Request(ROUTING_URL.format(a=a, b=b),
                                     headers={"User-Agent": "operation-boyd-orr"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    if data.get("code") != "Ok":
        return None
    path = [[lat, lon] for lon, lat in data["routes"][0]["geometry"]["coordinates"]]
    # The router snaps to the nearest path; start and end at the buildings themselves.
    return [[a["lat"], a["lon"]], *path, [b["lat"], b["lon"]]]


def build_routes(persons: list, buildings: dict) -> dict:
    """Route for every walk between consecutive visits, keyed "From|To".
    Walks the router can't serve are left out and drawn as straight lines."""
    cache = json.loads(ROUTES_CACHE.read_text(encoding="utf-8")) if ROUTES_CACHE.exists() else {}
    routes = {}
    for person in persons:
        visits = person["visits"]
        for here, there in zip(visits, visits[1:]):
            if here["location"] == there["location"] or there["start"] <= here["end"]:
                continue  # no walk in between
            key = f"{here['location']}|{there['location']}"
            if key not in cache:
                route = fetch_route(buildings[here["location"]], buildings[there["location"]])
                if route is None:
                    print(f"No walking route for {key}; using a straight line")
                    continue
                cache[key] = route
                time.sleep(0.5)  # go easy on the free service
            routes[key] = cache[key]
    ROUTES_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return routes


def build_case() -> dict:
    locations, people, _, merged = load_all_data()
    audit = run_full_audit(people, merged)
    conspirators = audit[audit["Anomaly_Count"] > 0]
    mastermind, _ = find_mastermind(audit, merged)

    students = people.set_index("Student ID", drop=False)
    visits_by_id = dict(tuple(merged.groupby("Student ID")))

    persons, events = [], []
    # Each meeting is listed under both people involved.
    meetings_by_id = defaultdict(list)

    if mastermind:
        met = mastermind["Rendezvous"]
        for con_id, info in met.items():
            for m in info["Meetings"]:
                span = {"location": m["Location"], "start": m["Start_Minutes"], "end": m["End_Minutes"]}
                meetings_by_id[mastermind["Student ID"]].append({**span, "with": info["Conspirator_Name"]})
                meetings_by_id[con_id].append({**span, "with": mastermind["Name"]})

        persons.append(build_person(
            students.loc[mastermind["Student ID"]],
            visits_by_id[mastermind["Student ID"]],
            [f"No irregularities in their own record, but met all {len(met)} conspirators"],
            meetings_by_id[mastermind["Student ID"]],
            is_mastermind=True,
        ))
        for info in met.values():
            for m in info["Meetings"]:
                events.append({
                    "type": "meeting",
                    "time": m["Start_Minutes"],
                    "location": m["Location"],
                    "title": f"{mastermind['Name']} meets {info['Conspirator_Name']}",
                    "detail": f"{m['Location']}, {m['Start_HHMM']}-{m['End_HHMM']} ({m['Duration_Minutes']} min)",
                })

    for _, row in conspirators.iterrows():
        flags = row["Flags"]
        persons.append(build_person(
            students.loc[row["Student ID"]],
            visits_by_id[row["Student ID"]],
            [f.message for f in flags],
            meetings_by_id[row["Student ID"]],
            is_mastermind=False,
        ))
        for flag in flags:
            events.append({
                "type": "flag",
                "time": flag.time_min,
                "location": flag.location,
                "title": row["Name"],
                "detail": flag.message,
            })

    events.sort(key=lambda e: e["time"])

    # Replay from the hour before the first log entry to the hour after the last.
    all_visits = [v for p in persons for v in p["visits"]]
    start = math.floor(min(v["start"] for v in all_visits) / 60) * 60
    end = math.ceil(max(v["end"] for v in all_visits) / 60) * 60

    buildings = build_buildings(locations)
    return {
        "stats": {"students": len(people), "conspirators": len(conspirators)},
        "center": [float(locations["Latitude"].mean()), float(locations["Longitude"].mean())],
        "timeline": {"start": start, "end": end},
        "buildings": buildings,
        "routes": build_routes(persons, buildings),
        "people": persons,
        "events": events,
    }


def main() -> None:
    case = build_case()
    # Escape "</" so data can never close the surrounding <script> tag.
    payload = json.dumps(case, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, payload)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT.name}: {len(case['people'])} people, {len(case['events'])} events")


if __name__ == "__main__":
    main()
