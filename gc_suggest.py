"""Claude suggests ONE real course for each Cal-GETC placeholder card, from the college's official list.

Claude chooses; Python checks. Claude only sees courses that are on the official list for that card's area,
and every answer is checked afterwards: a course that is not on that area's list, or that is picked for two
cards, is thrown away. So a suggestion can never be a made-up or unapproved course.
"""
import json
import re

import calgetc_list

MODEL = "claude-haiku-5-5"
MAX_PER_AREA = 80          # keeps the request small; the student can still pick any course by hand


def candidates_for(cards, listing, used_keys=()):
    """{card code: [course dicts from the official list]} for each placeholder card."""
    out = {}
    for c in cards:
        area = c.get("area_id") or c["code"].split()[1]
        out[c["code"]] = calgetc_list.courses_for_area(listing, area, used_keys)[:MAX_PER_AREA]
    return out


def _prompt(cards, cands, context):
    lines = []
    for c in cards:
        area = c.get("area_id") or c["code"].split()[1]
        opts = "\n".join(f'    {x["code"]} | {x["title"]} | {x["units"]:g} units'
                         + (" | includes lab" if x["lab"] and area in ("5A", "5B") else "")
                         + (f" | approval ends {x['removed']}" if x["removed"] else "")
                         for x in cands[c["code"]])
        lines.append(f'CARD {c["code"]} (Cal-GETC area {area}: {c.get("generic_title") or c.get("title") or c["code"]})\n{opts}')
    return (
        "You help a community college student choose general education courses for Cal-GETC.\n"
        f"Student's major: {context.get('major', 'unknown')}. Transfer target: {context.get('campus', 'unknown')}.\n"
        f"Courses they already have or plan for their major: {', '.join(context.get('major_courses', [])[:40]) or 'none given'}.\n"
        f"Notes from the student: {context.get('notes') or 'none'}.\n\n"
        "For EACH card below, choose exactly one course from that card's own list. Rules:\n"
        "- Only use a course code printed under that card. Never invent a course.\n"
        "- Never pick the same course for two cards.\n"
        "- Avoid a course whose approval ends soon unless nothing else fits.\n"
        "- Prefer courses that fit the student's major or interests, are widely accepted for transfer, "
        "and (for areas 5A/5B) include the lab when that saves a separate lab course.\n"
        "- Give a short, plain reason (under 20 words). Do not claim anything you cannot tell from the list.\n\n"
        + "\n\n".join(lines) +
        '\n\nAnswer with JSON only: {"picks": [{"card": "<card code>", "course": "<course code>", "why": "<reason>"}]}')


def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def verify(raw, cards, cands):
    """Keep only picks that are on that card's list and not used twice. Returns (picks, dropped)."""
    picks, dropped, taken = {}, [], set()
    valid = {c["code"] for c in cards}
    for p in (raw or {}).get("picks", []):
        card, code = str(p.get("card", "")).strip(), str(p.get("course", "")).strip()
        match = next((x for x in cands.get(card, []) if x["code"].upper() == code.upper()), None) if card in valid else None
        if not match:
            dropped.append(f"{card}: {code or 'nothing'} is not on that area's official list")
        elif match["code"] in taken or card in picks:
            dropped.append(f"{card}: {code} was already chosen")
        else:
            taken.add(match["code"])
            picks[card] = {"course": match["code"], "title": match["title"], "units": match["units"],
                           "why": str(p.get("why", "")).strip()[:200]}
    return picks, dropped


def suggest(cards, listing, context, used_keys=(), client=None):
    """Returns {"picks": {card code: {...}}, "dropped": [...], "error": str|None}."""
    cands = candidates_for(cards, listing, used_keys)
    cards = [c for c in cards if cands.get(c["code"])]
    if not cards:
        return {"picks": {}, "dropped": [], "error": "No approved courses were found on the list for these cards."}
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        resp = client.messages.create(model=MODEL, max_tokens=4000,
                                      messages=[{"role": "user", "content": _prompt(cards, cands, context)}])
        raw = _json("".join(b.text for b in resp.content if getattr(b, "text", None)))
    except Exception as e:                                   # network, key, or unreadable answer
        return {"picks": {}, "dropped": [], "error": f"Could not get suggestions: {str(e)[:150]}"}
    picks, dropped = verify(raw, cards, cands)
    return {"picks": picks, "dropped": dropped, "error": None}
