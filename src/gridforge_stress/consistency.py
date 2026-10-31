"""Cross-document consistency: do the paid deliverables agree with the screen they were built from?

EVALUATES. For one intake, the facts a reader takes from the Density Screen (/v1/screen) must reappear unchanged in the Walkthrough deck
(/v1/deck), the Proposal, the Specification and the Study: the recommended architecture's racks, its time to full capacity and the wording that
says whether that time is a date or an assumption, the client identity, and the synthetic label. A deck that quietly drops "assumed" turns a
placeholder into a promise; a deck whose rack count differs from the screen sells a different number than the one computed.

DOES NOT EVALUATE. That the screen is right (see oracles.py), layout, wording quality, or any figure the documents print that the screen does
not carry. Strings are matched on the printed form a reader sees, so a harmless reformatting of the deck can fail a check: that is a cost
accepted for not importing the product's renderer.

INDEPENDENCE. The expected values come from the screen's structured result, never from the document under test. No product code is used.
"""

from __future__ import annotations

import html
import re
from typing import Any


def visible_text(document: str) -> str:
    """What a reader sees: tags and style blocks removed, entities decoded, whitespace collapsed."""
    doc = re.sub(r"<(style|script).*?</\1>", " ", document, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", doc))).strip()


def recommended(screen: dict[str, Any]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in screen["scenarios"]}
    return by_id[screen["recommended"]]


def check_deck(screen: dict[str, Any], deck_document: str) -> list[str]:
    """Problems where the deck's text disagrees with the screen's recommended scenario. Empty means consistent on the checked facts."""
    text = visible_text(deck_document)
    s = recommended(screen)
    racks, ttp = s["after_ladder"]["racks"], s["time_to_power"]
    out: list[str] = []
    if f"After the ladder {racks} racks" not in text:
        out.append(
            f"deck does not state 'After the ladder {racks} racks' (the screen's recommended {s['id']})"
        )
    weeks = ttp.get("weeks_to_full")
    if weeks is not None and ttp.get("schedule_complete"):
        m = re.search(r"Time to full capacity (\d+(?:\.\d+)?) weeks", text)
        if not m:
            out.append("deck states no time to full capacity")
        elif float(m.group(1)) != float(weeks):
            out.append(f"deck says {m.group(1)} weeks, the screen computed {weeks:g}")
        setter = ttp.get("sets_the_date")
        if setter and f"set by {setter.lower()}" not in text.lower():
            out.append(f"deck does not name {setter!r} as setting the date")
    assumed = ttp.get("date_basis") == "assumed"
    says_assumed = "assumed" in text.lower() and "not a date" in text.lower()
    if assumed and not says_assumed:
        out.append(
            "the screen's date rests on an assumption but the deck presents it without saying so"
        )
    if not assumed and says_assumed:
        out.append("the deck calls the date an assumption but the screen says it is supported")
    if "modelled, not measured" not in text.lower():
        out.append("deck lacks the 'modelled, not measured' statement")
    return out


def check_study_against_screen(screen: dict[str, Any], study: dict[str, Any]) -> list[str]:
    """The Study's per-scenario capacity and schedule equal the screen's."""
    out: list[str] = []
    a = {s["id"]: s for s in screen["scenarios"]}
    b = {s["id"]: s for s in study["scenarios"]}
    if set(a) != set(b):
        return [f"scenario sets differ: screen {sorted(a)}, study {sorted(b)}"]
    for sid, sa in a.items():
        sb = b[sid]
        for part, key in (("as_found", "racks"), ("after_ladder", "racks")):
            if sa[part][key] != sb[part][key]:
                out.append(f"{sid}.{part}.{key}: screen {sa[part][key]}, study {sb[part][key]}")
        for key in ("weeks_to_full", "sets_the_date", "date_basis"):
            if sa["time_to_power"].get(key) != sb["time_to_power"].get(key):
                out.append(
                    f"{sid}.time_to_power.{key}: screen {sa['time_to_power'].get(key)!r}, study {sb['time_to_power'].get(key)!r}"
                )
    if screen.get("project") != study.get("project"):
        out.append("the project block differs between the screen and the study")
    return out


def check_identity(
    project: dict[str, Any], synthetic: bool, documents: dict[str, str]
) -> list[str]:
    """Every document names the client; the synthetic label appears if and only if the intake is synthetic."""
    out: list[str] = []
    client = str(project.get("client", ""))
    for name, doc in documents.items():
        text = visible_text(doc)
        if client and client not in text:
            out.append(f"{name}: does not name the client {client!r}")
        says_synthetic = "synthetic" in text.lower()
        if synthetic and not says_synthetic:
            out.append(f"{name}: the intake is synthetic but the document does not say so")
        if not synthetic and says_synthetic:
            out.append(
                f"{name}: the intake is a real client but the document calls itself synthetic"
            )
    return out
