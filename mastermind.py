"""
Identify the mastermind: the one student with a clean record who was in the
same place, at the same time, as every flagged conspirator.
"""

from typing import List, Optional, Tuple

import pandas as pd

from anomaly_detection import run_full_audit
from data_loader import load_all_data, minutes_to_hhmm


def find_meetings(visits_a: pd.DataFrame, visits_b: pd.DataFrame) -> List[dict]:
    """Every interval where A and B were logged in the same building."""
    meetings = []
    for _, a in visits_a.iterrows():
        for _, b in visits_b[visits_b["Location"] == a["Location"]].iterrows():
            start = max(a["Start_Minutes"], b["Start_Minutes"])
            end = min(a["End_Minutes"], b["End_Minutes"])
            if start < end:
                meetings.append({
                    "Location": a["Location"],
                    "Start_Minutes": int(start),
                    "End_Minutes": int(end),
                    "Start_HHMM": minutes_to_hhmm(start),
                    "End_HHMM": minutes_to_hhmm(end),
                    "Duration_Minutes": int(end - start),
                })
    return meetings


def find_mastermind(
    audit: pd.DataFrame, merged: pd.DataFrame
) -> Tuple[Optional[dict], pd.DataFrame]:
    """Return (mastermind, leaderboard).

    The leaderboard ranks every clean student by how many conspirators they
    met. The mastermind is the clean student who met all of them, or None.
    """
    conspirators = audit[audit["Anomaly_Count"] > 0]
    clean = audit[audit["Anomaly_Count"] == 0]
    visits_by_id = dict(tuple(merged.groupby("Student ID")))
    no_visits = merged.iloc[0:0]

    candidates = []
    for _, student in clean.iterrows():
        own_visits = visits_by_id.get(student["Student ID"], no_visits)
        rendezvous = {}
        for _, con in conspirators.iterrows():
            meetings = find_meetings(own_visits, visits_by_id.get(con["Student ID"], no_visits))
            if meetings:
                rendezvous[con["Student ID"]] = {"Conspirator_Name": con["Name"], "Meetings": meetings}
        candidates.append({
            "Student ID": student["Student ID"],
            "Name": student["Name"],
            "Subject": student["Subject"],
            "Age": student["Age"],
            "Conspirators_Met": len(rendezvous),
            "Rendezvous": rendezvous,
        })

    leaderboard = pd.DataFrame(candidates).sort_values("Conspirators_Met", ascending=False)
    top = leaderboard.iloc[0].to_dict() if not leaderboard.empty else None
    mastermind = top if top and top["Conspirators_Met"] == len(conspirators) else None
    return mastermind, leaderboard


if __name__ == "__main__":
    _, people, _, merged = load_all_data()
    audit = run_full_audit(people, merged)
    total = int((audit["Anomaly_Count"] > 0).sum())
    mastermind, leaderboard = find_mastermind(audit, merged)

    print("Clean students who met the most conspirators:")
    for _, row in leaderboard.head(5).iterrows():
        print(f"  {row['Name']} ({row['Student ID']}): {row['Conspirators_Met']}/{total}")

    if mastermind is None:
        print("\nNo clean student met every conspirator.")
    else:
        print(f"\nMastermind: {mastermind['Name']} ({mastermind['Student ID']}), "
              f"{mastermind['Subject']}, age {mastermind['Age']}")
        for con_id, info in mastermind["Rendezvous"].items():
            print(f"\n  with {info['Conspirator_Name']} ({con_id}):")
            for m in info["Meetings"]:
                print(f"    {m['Start_HHMM']}-{m['End_HHMM']}  {m['Location']} ({m['Duration_Minutes']} min)")
