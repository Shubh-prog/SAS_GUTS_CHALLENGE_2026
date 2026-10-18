"""
Load and normalise the hackathon datasets.

All times are converted to minutes after midnight. Intervals that cross
midnight (e.g. 2330-0115) have 1440 added to their end so that every interval
satisfies start <= end.
"""

import ast
import re
from pathlib import Path
from typing import NamedTuple, Optional, Tuple

import pandas as pd

DATA_DIR = Path(__file__).parent / "SAS_GUTS_HACKATHON_2026"
MINUTES_PER_DAY = 24 * 60


class Lecture(NamedTuple):
    location: str
    start_min: int
    end_min: int


# Mandatory lecture for each degree. Most entries come from the building
# descriptions in location_data.csv.
MANDATORY_TIMETABLE = {
    "Computing Science":   Lecture("Boyd Orr Building",        10 * 60, 12 * 60),
    "Engineering":         Lecture("James Watt Building",      10 * 60, 13 * 60),
    "Business":            Lecture("Adam Smith Building",      10 * 60, 11 * 60),
    "Philosophy":          Lecture("Main Building",             9 * 60, 11 * 60),
    "Veterinary Medicine": Lecture("Main Building",            10 * 60, 12 * 60),
    "Mathematics":         Lecture("Main Building",            11 * 60, 13 * 60),
    "Medicine":            Lecture("Wolfson Medical Building", 13 * 60, 15 * 60),
    "English Literature":  Lecture("St Andrews Building",       9 * 60, 12 * 60),
    "Chemistry":           Lecture("Joseph Black Building",    10 * 60, 11 * 60),
    "Physics":             Lecture("Kelvin Building",           9 * 60, 11 * 60),
}

# Corrections for known errors in security_logs.csv.
LOCATION_FIXES = {"Queen Maragret Building": "Queen Margaret Union"}
# Jonny Craig's ID is logged as 2912324G; the student record says 2712324G.
STUDENT_ID_FIXES_BY_NAME = {"Jonny Craig": "2712324G"}


def hhmm_to_minutes(hhmm: str) -> int:
    """'0930' or '930' -> 570."""
    digits = str(hhmm).strip().zfill(4)
    return int(digits[:2]) * 60 + int(digits[2:])


def minutes_to_hhmm(minutes: int) -> str:
    """570 -> '09:30'. Values past midnight wrap around."""
    minutes = int(minutes)
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def parse_interval(text: str) -> Tuple[Optional[int], Optional[int]]:
    """'2330-0115' -> (1410, 1515). Returns (None, None) if malformed."""
    parts = str(text).split("-")
    if len(parts) != 2:
        return None, None
    start, end = hhmm_to_minutes(parts[0]), hhmm_to_minutes(parts[1])
    if end < start:
        end += MINUTES_PER_DAY
    return start, end


def _parse_geolocation(text: str) -> Tuple[Optional[float], Optional[float]]:
    """'{55.87 -4.29}' -> (55.87, -4.29)."""
    parts = re.sub(r"[{}]", "", str(text)).split()
    if len(parts) != 2:
        return None, None
    return float(parts[0]), float(parts[1])


def _parse_societies(value) -> list:
    if pd.isna(value) or value == "N/A":
        return []
    text = str(value).strip()
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, SyntaxError):
        pass
    # Falls through to here for set-style entries like "{'A', 'B'}" too.
    cleaned = re.sub(r"[\[\]{}'\"]", "", text)
    return [s.strip() for s in cleaned.split(",") if s.strip()]


def _normalise_ids(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper()


def load_locations(path: Path = DATA_DIR / "location_data.csv") -> pd.DataFrame:
    df = pd.read_csv(path)

    df[["Latitude", "Longitude"]] = df["Geolocation"].apply(
        lambda g: pd.Series(_parse_geolocation(g))
    )

    df[["Open_Minutes", "Close_Minutes"]] = df["Opening Times"].apply(
        lambda t: pd.Series(parse_interval(t))
    )
    # "0000-0000" means the venue never closes (e.g. Kelvingrove Park).
    df["Is_24h"] = (df["Open_Minutes"] == 0) & (df["Close_Minutes"] == 0)
    df.loc[df["Is_24h"], "Close_Minutes"] = MINUTES_PER_DAY
    return df


def load_people(path: Path = DATA_DIR / "people_data.csv") -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Student ID"] = _normalise_ids(df["Student ID"])
    df["Societies_List"] = df["Societies"].apply(_parse_societies)
    return df


def load_security_logs(path: Path = DATA_DIR / "security_logs.csv") -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Student ID"] = _normalise_ids(df["Student ID"])
    df["Location"] = df["Location"].replace(LOCATION_FIXES)
    for name, student_id in STUDENT_ID_FIXES_BY_NAME.items():
        df.loc[df["Name"] == name, "Student ID"] = student_id

    df[["Start_Minutes", "End_Minutes"]] = df["Time"].apply(
        lambda t: pd.Series(parse_interval(t))
    )
    df["Duration_Minutes"] = df["End_Minutes"] - df["Start_Minutes"]
    df["Start_HHMM"] = df["Start_Minutes"].apply(minutes_to_hhmm)
    df["End_HHMM"] = df["End_Minutes"].apply(minutes_to_hhmm)
    return df


def load_all_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (locations, people, logs, merged), where merged is each log row
    joined with the student's profile and the venue's details."""
    locations = load_locations()
    people = load_people()
    logs = load_security_logs()

    merged = logs.merge(
        people[["Student ID", "Age", "Sex", "Year of Study", "Subject",
                "Height", "Hair colour", "Societies_List"]],
        on="Student ID",
        how="left",
    ).merge(
        locations[["Building Name", "Latitude", "Longitude",
                   "Open_Minutes", "Close_Minutes", "Is_24h"]],
        left_on="Location",
        right_on="Building Name",
        how="left",
    )
    return locations, people, logs, merged


if __name__ == "__main__":
    locations, people, logs, merged = load_all_data()
    print(f"Locations:      {len(locations)}")
    print(f"Students:       {len(people)}")
    print(f"Log entries:    {len(logs)}")
    print(f"Unmatched logs: {merged['Subject'].isna().sum()}")
    print()
    print(merged[["Student ID", "Name", "Subject", "Location",
                  "Start_HHMM", "End_HHMM", "Duration_Minutes"]].head())
