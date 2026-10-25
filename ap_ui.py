"""AP and IB credit panel (Streamlit). The logic lives in ap.py."""
import time

import pandas as pd
import streamlit as st

import ap
from assist import fetch_agreement
from store import get_agreement, cache_agreement, add_exam_credit, list_exam_credits

KINDS = ("ap", "ib")


def _ago(ts):
    secs = max(0, time.time() - ts)
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    if secs < 129600:
        return f"{int(secs // 3600)} hours ago"
    return f"{int(secs // 86400)} days ago"


def ap_context(major_key, profile_code):
    """What the rest of the app needs: the AP and IB charts (from the local cache, no network) and the
    student's saved exams. Never downloads anything by itself."""
    ss = st.session_state
    if ss.get("ap_major") != major_key:  # a different major may link different charts
        ss["ap_major"] = major_key
        ss["ap_urls"] = {}
        ss["ap_chart_infos"] = {}
    urls, infos = ss.setdefault("ap_urls", {}), ss.setdefault("ap_chart_infos", {})
    if major_key:
        agreement = None
        for kind in KINDS:
            if infos.get(kind) is None and urls.get(kind) is None:
                if agreement is None:
                    agreement, _ = get_agreement(major_key)
                if not agreement:
                    break
                url = ap.find_policy_links(agreement).get(kind) or ""
                urls[kind] = url
                if url and ap.safe_pdf_url(url):
                    hit = ap.cached_chart(url)
                    if hit:
                        infos[kind] = hit
    exams = ([e for e in list_exam_credits(profile_code) if str(e["exam_type"]).upper() in ("AP", "IB")]
             if profile_code else [])
    return {"infos": {k.upper(): infos.get(k) for k in KINDS},
            "charts": {k.upper(): (infos.get(k) or {}).get("chart") for k in KINDS},
            "exams": exams, "allow_unverified": bool(ss.get("apw_allow_unverified", False))}


def _load(major_key, force_live, kind):
    agreement, _ = get_agreement(major_key)
    if agreement is None:
        out = fetch_agreement(major_key)
        if out["status"] != "ok":
            return {"status": "assist_failed",
                    "message": f"Could not load the ASSIST agreement to find the chart link ({out['status']})."}
        agreement = out["data"]
        cache_agreement(major_key, agreement)
    return ap.load_chart(agreement, force_live=force_live, kind=kind)


def _result_text(fx, kind="ap"):
    s = fx["status"]
    if s == "exempts":
        return "Exempts " + ", ".join(fx["courses"])
    if s == "unverified":
        return "Chart rule could not be matched to the PDF text, so it is not applied: " + ", ".join(fx["unverified"])
    if s == "no_major_exemption":
        return "No major exemption in the chart (elective or general-education credit only)"
    if s == "no_credit":
        return f"Score below {ap.KINDS[kind]['min_credit']}: no credit"
    return "Not found in the chart"


def render_ap_panel(ctx, major_key, reqs, ensure_profile):
    st.markdown("**AP and IB exams**")
    st.caption("The credit charts are found through the live ASSIST agreement for your major, read by "
               "Claude, and checked against each PDF's own text. Nothing is applied from a score alone.")
    if not major_key:
        st.info("Load your major's requirements first. The chart links come from its ASSIST agreement.")
        return

    label = st.radio("Exam type", ["AP", "IB"], horizontal=True, key="apw_kind")
    kind = label.lower()
    info = ctx["infos"][label]
    load = refresh = False
    if info:
        chart = info["chart"]
        v = chart["verification"]
        checked = (f"{v['verified']} of {v['rules']} rules matched the PDF text" if v["checked"]
                   else "could not be checked against the PDF text")
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"{label} chart {chart.get('chart_year') or ''} · downloaded {_ago(info['fetched_at'])} "
                    f"via {info.get('via', 'unknown')} · {len(chart['exams'])} exams · {checked} · "
                    f"[open the PDF]({info['url']})")
        refresh = c2.button("Refresh chart", key=f"apw_refresh_{kind}")
        if info.get("status") == "stale" and info.get("message"):
            st.warning(info["message"])
    else:
        load = st.button(f"Load UCSD's {label} credit chart from ASSIST", key=f"apw_load_{kind}")

    if load or refresh:
        with st.spinner("Finding the chart link, downloading the PDF, and reading it..."):
            res = _load(major_key, force_live=refresh, kind=kind)
        if res["status"] in ("ok", "cached", "stale"):
            st.session_state.setdefault("ap_chart_infos", {})[kind] = res
            st.rerun()
        else:
            st.error(res["message"])

    if info:
        # ---- add an exam ----
        top = ap.KINDS[kind]["max_score"]
        a1, a2, a3 = st.columns([3, 1, 1])
        exam = a1.selectbox(f"{label} exam", [e["exam"] for e in chart["exams"]], key=f"apw_exam_{kind}")
        score = a2.selectbox("Score", list(range(top, 0, -1)), key=f"apw_score_{kind}")
        year = a3.number_input("Exam year", min_value=1990, max_value=2100, value=2026, key="apw_year")
        if kind == "ib":
            st.caption("The UC system gives credit only for Higher Level IB exams. Standard Level exams earn none.")
        if st.button(f"Add {label} exam", key=f"apw_add_{kind}"):
            add_exam_credit(ensure_profile(), exam_type=label, subject=exam, score=str(score),
                            year=int(year), status="Matched to chart")
            st.rerun()
        if chart.get("duplicate_note_verified"):
            st.caption("Chart note: " + chart["duplicate_note"])

    st.checkbox("Also apply chart rules that could not be matched to the PDF text "
                "(I will check the chart myself)", key="apw_allow_unverified")
    allow = ctx["allow_unverified"]

    # ---- what the saved exams do (AP and IB together) ----
    exams = ctx["exams"]
    if not exams:
        st.caption("No exams saved yet.")
        return
    req_keys = {ap._k(r["course"]) for r in reqs}
    rows, ids = [], []
    for e in exams:
        etype = str(e["exam_type"]).upper()
        chart_e = ctx["charts"].get(etype)
        ids.append(e["id"])
        try:
            s = int(str(e["score"]).strip())
        except ValueError:
            rows.append({"Type": etype, "Exam": e["subject"], "Score": e["score"], "Result": "The score is not a number",
                         "Units": None, "Counts for your major": "", "What the chart says": ""})
            continue
        if not chart_e:
            rows.append({"Type": etype, "Exam": e["subject"], "Score": s,
                         "Result": f"Load the {etype} chart to see what this exam does",
                         "Units": None, "Counts for your major": "", "What the chart says": ""})
            continue
        fx = ap.exam_effects(chart_e, e["subject"], s, allow)
        rows.append({
            "Type": etype, "Exam": e["subject"], "Score": s, "Result": _result_text(fx, etype.lower()),
            "Units": fx["units"] if s >= ap.KINDS[etype.lower()]["min_credit"] else None,
            "Counts for your major": ", ".join(c for c in fx["courses"] if ap._k(c) in req_keys),
            "What the chart says": " ".join(fx["texts"])[:300],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)

    covered = [r for r in reqs if r.get("ap_met")]
    if covered:
        st.success("Exam credit covers these requirements in your major: "
                   + ", ".join(sorted({r["course"] for r in covered})))
    else:
        st.caption("None of your exams exempt a requirement in this major.")
    st.caption("Units come from the charts. They are not added up here, because the caps differ by exam. "
               "Confirm everything with a UC San Diego advisor.")

    pick = st.selectbox("Remove an exam", [None] + ids, key="apw_remove",
                        format_func=lambda i: "Select exam" if i is None else
                        f"{rows[ids.index(i)]['Type']} {rows[ids.index(i)]['Exam']} · score {rows[ids.index(i)]['Score']}")
    if st.button("Remove selected exam", key="apw_remove_btn", disabled=pick is None):
        ap.delete_exam(st.session_state.profile_code, pick)
        st.rerun()