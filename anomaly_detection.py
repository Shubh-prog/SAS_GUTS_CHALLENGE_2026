"""
Rule-based audit of every student's security log.

A student is flagged for:
  1. Missing their degree's mandatory lecture (or studying an unknown degree)
  2. Being inside a building outside its opening hours
  3. Entering an 18+ venue while under 18
  4. Being logged in two different places at the same time
"""

from dataclasses import dataclass
from typing import List

import pandas as pd

from data_loader import MANDATORY_TIMETABLE, load_all_data, minutes_to_hhmm

AGE_RESTRICTED_VENUES = {"The Hive"}
MINIMUM_AGE = 18


@dataclass
class Flag:
    message: str
    time_min: int   # when the irregularity happens, used to place it on the timeline
    location: str

    def __str__(self) -> str:
        return self.message


def _overlaps(start_a, end_a, start_b, end_b) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def _span(visit) -> str:
    return f"{visit['Start_HHMM']}-{visit['End_HHMM']}"


def check_mandatory_lecture(student: pd.Series, visits: pd.DataFrame) -> List[Flag]:
    subject = student["Subject"]
    lecture = MANDATORY_TIMETABLE.get(subject)

    if lecture is None:
        first = visits.sort_values("Start_Minutes").iloc[0] if not visits.empty else None
        return [Flag(
            f"Subject '{subject}' is not on the campus timetable",
            int(first["Start_Minutes"]) if first is not None else 0,
            first["Location"] if first is not None else "",
        )]

    during_lecture = visits[visits.apply(
        lambda v: _overlaps(v["Start_Minutes"], v["End_Minutes"],
                            lecture.start_min, lecture.end_min), axis=1
    )] if not visits.empty else visits

    if (during_lecture["Location"] == lecture.location).any():
        return []

    lecture_desc = (f"Missed {subject} lecture at {lecture.location} "
                    f"({minutes_to_hhmm(lecture.start_min)}-{minutes_to_hhmm(lecture.end_min)})")
    if during_lecture.empty:
        return [Flag(f"{lecture_desc}; no log entries at that time",
                     lecture.start_min, lecture.location)]

    elsewhere = ", ".join(f"{v['Location']} ({_span(v)})" for _, v in during_lecture.iterrows())
    return [Flag(f"{lecture_desc}; was at {elsewhere} instead",
                 lecture.start_min, during_lecture.iloc[0]["Location"])]


def check_opening_hours(visits: pd.DataFrame) -> List[Flag]:
    flags = []
    for _, v in visits.iterrows():
        if v["Is_24h"] or pd.isna(v["Open_Minutes"]):
            continue
        open_min, close_min = v["Open_Minutes"], v["Close_Minutes"]
        if v["Start_Minutes"] >= open_min and v["End_Minutes"] <= close_min:
            continue
        # The moment they were first inside a closed building.
        inside_while_open = open_min <= v["Start_Minutes"] < close_min
        when = close_min if inside_while_open else v["Start_Minutes"]
        flags.append(Flag(
            f"In {v['Location']} {_span(v)}, outside opening hours "
            f"({minutes_to_hhmm(open_min)}-{minutes_to_hhmm(close_min)})",
            int(when), v["Location"],
        ))
    return flags


def check_age_restrictions(student: pd.Series, visits: pd.DataFrame) -> List[Flag]:
    age = student["Age"]
    if pd.isna(age) or age >= MINIMUM_AGE:
        return []
    return [
        Flag(f"Entered {v['Location']} ({_span(v)}) aged {int(age)}",
             int(v["Start_Minutes"]), v["Location"])
        for _, v in visits.iterrows()
        if v["Location"] in AGE_RESTRICTED_VENUES
    ]


def check_simultaneous_locations(visits: pd.DataFrame) -> List[Flag]:
    ordered = visits.sort_values("Start_Minutes").to_dict("records")
    return [
        Flag(f"Logged at {a['Location']} ({_span(a)}) and {b['Location']} ({_span(b)}) at the same time",
             int(b["Start_Minutes"]), b["Location"])
        for a, b in zip(ordered, ordered[1:])
        if a["Location"] != b["Location"] and a["End_Minutes"] > b["Start_Minutes"]
    ]


def audit_student(student: pd.Series, merged: pd.DataFrame) -> dict:
    visits = merged[merged["Student ID"] == student["Student ID"]]
    flags = (
        check_mandatory_lecture(student, visits)
        + check_opening_hours(visits)
        + check_age_restrictions(student, visits)
        + check_simultaneous_locations(visits)
    )
    return {
        "Student ID": student["Student ID"],
        "Name": student["Name"],
        "Subject": student["Subject"],
        "Age": student["Age"],
        "Anomaly_Count": len(flags),
        "Flags": flags,
    }


def run_full_audit(people: pd.DataFrame, merged: pd.DataFrame) -> pd.DataFrame:
    """One row per student, most-flagged first."""
    results = pd.DataFrame([audit_student(s, merged) for _, s in people.iterrows()])
    return results.sort_values("Anomaly_Count", ascending=False, kind="stable")


if __name__ == "__main__":
    _, people, _, merged = load_all_data()
    audit = run_full_audit(people, merged)
    flagged = audit[audit["Anomaly_Count"] > 0]

    print(f"Students audited: {len(audit)}")
    print(f"Flagged:          {len(flagged)}")
    for _, row in flagged.iterrows():
        print(f"\n{row['Name']} ({row['Student ID']}), {row['Subject']}, age {row['Age']}")
        for flag in row["Flags"]:
            print(f"  [{minutes_to_hhmm(flag.time_min)}] {flag}")
