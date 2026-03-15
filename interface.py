import streamlit as st
import pdfplumber
import re
import math

# ============================================================
# 1. POISSON HELPER
# ============================================================

def poisson_probability(k, lam):
    """P(X=k) for Poisson distribution with mean lam."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


# ============================================================
# 2. FOUR-LAYER PREDICTION ENGINE
# ============================================================

def predict_match(m, league_stats):
    """
    Runs the 4-layer prediction model on a single match dict.

    Layer 1 — Base xG          : average of attacker GF vs defender GA
    Layer 2 — CS / FTS adjust  : clean-sheet penalty + failed-to-score boost
    Layer 3 — O2.5 / BTS vibe  : historic scoring pattern multiplier
    Layer 4 — PPG mentality    : redistribute probability based on points-per-game

    Returns a dict with all intermediate values + final probabilities.
    """

    lh = league_stats.get("avg_home_goals", 1.50)
    la = league_stats.get("avg_away_goals", 1.20)
    if lh == 0: lh = 1.50
    if la == 0: la = 1.20

    hs = m["home_stats"]
    as_ = m["away_stats"]

    # ── STEP 1: Base xG ──────────────────────────────────────
    # Average the attacker's scoring rate with the defender's conceding rate
    base_hxg = (hs["gf"] + as_["ga"]) / 2
    base_axg = (as_["gf"] + hs["ga"]) / 2

    # ── STEP 2: CS / FTS Attack Reliability ──────────────────
    # Away CS% → penalises home attack (defender is tight)
    h_cs_penalty = (as_["cs"] / 100) * 0.25
    # Home CS% → penalises away attack
    a_cs_penalty = (hs["cs"] / 100) * 0.25
    # Home low FTS% → attacker is reliable scorer
    h_fts_boost  = ((100 - hs["fts"]) / 100) * 0.15
    # Away low FTS% → attacker is reliable scorer
    a_fts_boost  = ((100 - as_["fts"]) / 100) * 0.15

    adj_hxg = max(0.20, base_hxg * (1 - h_cs_penalty) * (1 + h_fts_boost))
    adj_axg = max(0.20, base_axg * (1 - a_cs_penalty) * (1 + a_fts_boost))

    # ── STEP 3: O2.5 / BTS Vibe Check ────────────────────────
    avg_o25  = (hs["o25"] + as_["o25"]) / 2
    avg_bts  = (hs["bts"] + as_["bts"]) / 2
    vibe     = (avg_o25 + avg_bts) / 2

    if vibe < 35:
        vibe_mult = 0.85   # historically boring → cut xG 15%
    elif vibe > 65:
        vibe_mult = 1.05   # historically high-scoring → add 5%
    else:
        vibe_mult = 1.00

    vibe_hxg = adj_hxg * vibe_mult
    vibe_axg = adj_axg * vibe_mult

    # ── POISSON distribution on vibe-adjusted xG ─────────────
    hw, d, aw = 0.0, 0.0, 0.0
    best_score, best_prob = "0-0", 0.0

    for i in range(7):
        for j in range(7):
            p = poisson_probability(i, vibe_hxg) * poisson_probability(j, vibe_axg)
            if   i > j: hw += p
            elif i == j: d += p
            else:        aw += p
            if p > best_prob:
                best_prob  = p
                best_score = f"{i}-{j}"

    # ── STEP 4: PPG Mentality Shift ───────────────────────────
    # Normalise PPG (max theoretical = 3.0)
    h_ppg_n   = hs["ppg"] / 3.0
    a_ppg_n   = as_["ppg"] / 3.0
    ppg_diff  = h_ppg_n - a_ppg_n   # positive = home stronger mentality

    shift = min(0.08, abs(ppg_diff) * 0.12)

    if ppg_diff > 0.05:       # home mentality edge
        fhw = hw + shift * 0.6
        fd  = d  - shift * 0.3
        faw = aw - shift * 0.3
    elif ppg_diff < -0.05:    # away mentality edge
        faw = aw + shift * 0.6
        fd  = d  - shift * 0.3
        fhw = hw - shift * 0.3
    else:
        fhw, fd, faw = hw, d, aw

    fhw = max(0.01, fhw)
    fd  = max(0.01, fd)
    faw = max(0.01, faw)

    # Re-normalise so probabilities sum to 1
    total = fhw + fd + faw
    fhw /= total
    fd  /= total
    faw /= total

    # ── Confidence Score (0–100) ──────────────────────────────
    margin    = abs(fhw - faw)
    ppg_conf  = min(1.0, abs(ppg_diff) * 1.5)
    vibe_conf = vibe / 100
    conf = round(
        (margin   * 0.45 +
         ppg_conf * 0.30 +
         best_prob * 0.15 +
         vibe_conf * 0.10) * 100
    )

    return {
        "league"       : m.get("league", ""),
        "home_team"    : m["home_team"],
        "away_team"    : m["away_team"],
        # raw inputs (for debug panel)
        "h_gf": hs["gf"],  "h_ga": hs["ga"],
        "h_fts": hs["fts"], "h_cs": hs["cs"],
        "h_bts": hs["bts"], "h_o25": hs["o25"], "h_ppg": hs["ppg"], "h_w": hs["w"],
        "a_gf": as_["gf"],  "a_ga": as_["ga"],
        "a_fts": as_["fts"], "a_cs": as_["cs"],
        "a_bts": as_["bts"], "a_o25": as_["o25"], "a_ppg": as_["ppg"], "a_w": as_["w"],
        # step-by-step intermediates
        "base_hxg"  : round(base_hxg,  3),
        "base_axg"  : round(base_axg,  3),
        "adj_hxg"   : round(adj_hxg,   3),
        "adj_axg"   : round(adj_axg,   3),
        "vibe"      : round(vibe,       1),
        "vibe_mult" : vibe_mult,
        "vibe_hxg"  : round(vibe_hxg,  3),
        "vibe_axg"  : round(vibe_axg,  3),
        "ppg_diff"  : round(ppg_diff,  3),
        # final outputs
        "home_xg"   : round(vibe_hxg, 2),
        "away_xg"   : round(vibe_axg, 2),
        "home_prob" : round(fhw * 100, 1),
        "draw_prob" : round(fd  * 100, 1),
        "away_prob" : round(faw * 100, 1),
        "top_score" : best_score,
        "top_score_pct": round(best_prob * 100, 1),
        "conf"      : conf,
    }


# ============================================================
# 3. PDF PARSER
# ============================================================

def parse_league_header(line):
    """Extract league name + home/away avg goals from header line."""
    pattern = r"Goals per match:\s*([\d\.]+)\s*\(([\d\.]+)\s*at home,\s*([\d\.]+)\s*away\)"
    m = re.search(pattern, line)
    name = line.split("stats")[0].strip()
    if m:
        return name, float(m.group(2)), float(m.group(3))
    return name, 1.50, 1.20


def clean_team_name(raw):
    return re.sub(r'\s*\d{1,2}:\d{2}$', '', raw).strip()


def extract_data_from_pdf(pdf_file):
    """
    Parse the SoccerSTATS PDF and return:
    [
      {
        "league"       : str,
        "league_stats" : { avg_home_goals, avg_away_goals },
        "fixtures"     : [ { home_team, away_team, home_stats, away_stats }, ... ]
      },
      ...
    ]

    Column order from PDF (after 'last N  N'):
      W%  FTS  CS  BTS  TG  GF  GA  1.5+  2.5+  3.5+  PPG
      [0] [1]  [2] [3]  [4] [5] [6] [7]   [8]   [9]   [10]
    """
    structured = []
    current_league = None
    pending_home   = None

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split('\n'):
                line = line.strip()

                # ── League header ──────────────────────────────
                if "Goals per match:" in line:
                    name, lh, la = parse_league_header(line)
                    current_league = {
                        "league"       : name,
                        "league_stats" : {"avg_home_goals": lh, "avg_away_goals": la},
                        "fixtures"     : [],
                    }
                    structured.append(current_league)
                    pending_home = None
                    continue

                # ── Match data row ─────────────────────────────
                if current_league and re.search(r"\s+last\s+\d+", line):
                    parts = re.split(r"\s+last\s+\d+\s+\d+\s+", line, maxsplit=1)
                    if len(parts) < 2:
                        continue

                    raw_name  = parts[0]
                    stats_str = parts[1].replace('%', '')
                    tokens    = stats_str.split()

                    # Need at least 11 tokens: W FTS CS BTS TG GF GA 1.5 2.5 3.5 PPG
                    if len(tokens) < 11:
                        continue

                    try:
                        stats = {
                            "w"   : float(tokens[0]),
                            "fts" : float(tokens[1]),
                            "cs"  : float(tokens[2]),
                            "bts" : float(tokens[3]),
                            "tg"  : float(tokens[4]),
                            "gf"  : float(tokens[5]),
                            "ga"  : float(tokens[6]),
                            "o15" : float(tokens[7]),
                            "o25" : float(tokens[8]),
                            "o35" : float(tokens[9]),
                            "ppg" : float(tokens[10]),
                        }
                    except (ValueError, IndexError):
                        continue

                    if pending_home is None:
                        pending_home = {
                            "name" : clean_team_name(raw_name),
                            "stats": stats,
                        }
                    else:
                        fixture = {
                            "league"    : current_league["league"],
                            "home_team" : pending_home["name"],
                            "away_team" : clean_team_name(raw_name),
                            "home_stats": pending_home["stats"],
                            "away_stats": stats,
                        }
                        current_league["fixtures"].append(fixture)
                        pending_home = None

    return structured


# ============================================================
# 4. STREAMLIT UI
# ============================================================

st.set_page_config(page_title="Advanced Match Predictor", layout="wide")

st.title("⚽ Advanced Match Predictor")
st.caption("4-layer model: xG base → CS/FTS adjustment → O2.5 vibe check → PPG mentality shift")

# ── Sidebar filters ────────────────────────────────────────────
st.sidebar.header("Filters")

min_conf = st.sidebar.slider(
    "Min confidence score (0–100)",
    min_value=0, max_value=90, value=0, step=1,
    help="Composite score blending probability margin, PPG diff, vibe, and top-score likelihood."
)

min_top_pct = st.sidebar.slider(
    "Min top-score probability (%)",
    min_value=0.0, max_value=30.0, value=0.0, step=0.5
)

min_win_prob = st.sidebar.slider(
    "Min win probability for either team (%)",
    min_value=0, max_value=80, value=0, step=1
)

xg_mode = st.sidebar.radio(
    "xG filter mode",
    options=["Any", "Both teams above threshold", "Either team above threshold", "Total xG above threshold"],
    index=0
)

xg_threshold = 1.5
if xg_mode != "Any":
    xg_threshold = st.sidebar.slider("xG threshold", 0.5, 4.0, 1.5, 0.1)

sort_by = st.sidebar.selectbox(
    "Sort results by",
    options=["Confidence score", "Total xG", "Top score %", "Home win prob", "Away win prob"]
)

show_steps = st.sidebar.checkbox("Show model steps for each match", value=False)

# ── File upload ────────────────────────────────────────────────
uploaded = st.file_uploader("Upload SoccerSTATS PDF", type="pdf")

if uploaded:
    with st.spinner("Parsing PDF and running predictions…"):

        raw_leagues = extract_data_from_pdf(uploaded)

        all_matches = []
        for league_data in raw_leagues:
            l_stats = league_data["league_stats"]
            for fixture in league_data["fixtures"]:
                result = predict_match(fixture, l_stats)
                all_matches.append(result)

        if not all_matches:
            st.error("No matches could be parsed from this PDF. Check the file format.")
            st.stop()

        # ── Apply filters ──────────────────────────────────────
        filtered = []
        for m in all_matches:
            if m["conf"] < min_conf:
                continue
            if m["top_score_pct"] < min_top_pct:
                continue
            if max(m["home_prob"], m["away_prob"]) < min_win_prob:
                continue

            hxg, axg = m["home_xg"], m["away_xg"]
            if xg_mode == "Both teams above threshold":
                if not (hxg >= xg_threshold and axg >= xg_threshold):
                    continue
            elif xg_mode == "Either team above threshold":
                if not (hxg >= xg_threshold or axg >= xg_threshold):
                    continue
            elif xg_mode == "Total xG above threshold":
                if hxg + axg < xg_threshold:
                    continue

            filtered.append(m)

        # ── Sort ───────────────────────────────────────────────
        sort_key_map = {
            "Confidence score" : lambda x: x["conf"],
            "Total xG"         : lambda x: x["home_xg"] + x["away_xg"],
            "Top score %"      : lambda x: x["top_score_pct"],
            "Home win prob"    : lambda x: x["home_prob"],
            "Away win prob"    : lambda x: x["away_prob"],
        }
        filtered.sort(key=sort_key_map[sort_by], reverse=True)

        # ── Summary banner ─────────────────────────────────────
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Matches parsed",  len(all_matches))
        c2.metric("After filters",   len(filtered))
        c3.metric("Leagues found",   len(raw_leagues))
        st.divider()

        if not filtered:
            st.warning("No matches pass the current filters.")
            st.stop()

        # ── Render match cards ─────────────────────────────────
        for m in filtered:
            conf       = m["conf"]
            conf_color = "🟢" if conf >= 60 else "🟡" if conf >= 35 else "⚪"

            with st.container():
                # Header row
                col_title, col_conf = st.columns([5, 1])
                with col_title:
                    st.caption(m["league"])
                    st.markdown(f"### {m['home_team']} vs {m['away_team']}")
                with col_conf:
                    st.metric(f"{conf_color} Confidence", f"{conf}/100")

                # Four stat columns
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.markdown("**xG (home / away)**")
                    st.write(f"{m['home_xg']:.2f}  —  {m['away_xg']:.2f}")
                with c2:
                    st.markdown("**Top score**")
                    st.info(f"{m['top_score']}  ({m['top_score_pct']:.1f}%)")
                with c3:
                    winner = (m["home_team"] if m["home_prob"] > m["away_prob"]
                              else m["away_team"] if m["away_prob"] > m["home_prob"]
                              else "Draw")
                    win_pct = max(m["home_prob"], m["away_prob"])
                    st.markdown("**Likely outcome**")
                    st.write(f"{winner}  {win_pct:.0f}%")
                with c4:
                    st.markdown("**Draw probability**")
                    st.write(f"{m['draw_prob']:.1f}%")

                # Probability bar
                prob_html = f"""
                <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin:6px 0">
                  <div style="flex:{m['home_prob']:.0f};background:#378ADD"></div>
                  <div style="flex:{m['draw_prob']:.0f};background:#888780"></div>
                  <div style="flex:{m['away_prob']:.0f};background:#D85A30"></div>
                </div>
                <div style="display:flex;justify-content:space-between;font-size:12px;color:#888">
                  <span>{m['home_team']} {m['home_prob']:.0f}%</span>
                  <span>Draw {m['draw_prob']:.0f}%</span>
                  <span>{m['away_team']} {m['away_prob']:.0f}%</span>
                </div>
                """
                st.markdown(prob_html, unsafe_allow_html=True)

                # Model steps (optional)
                if show_steps:
                    with st.expander("Model steps"):
                        st.markdown(f"""
| Step | Detail | Home xG | Away xG |
|------|--------|---------|---------|
| 1 — Base xG | avg(GF, opp GA) | `{m['base_hxg']}` | `{m['base_axg']}` |
| 2 — CS/FTS adjust | CS penalty + FTS boost | `{m['adj_hxg']}` | `{m['adj_axg']}` |
| 3 — Vibe check | avg O2.5/BTS = {m['vibe']:.0f}% → ×{m['vibe_mult']} | `{m['vibe_hxg']}` | `{m['vibe_axg']}` |
| 4 — PPG shift | PPG diff = {m['ppg_diff']:+.3f} | **{m['home_prob']}%** | **{m['away_prob']}%** |
                        """)
                        st.markdown(f"""
**Raw inputs — home:** W%={m['h_w']} · GF={m['h_gf']} · GA={m['h_ga']} · FTS={m['h_fts']} · CS={m['h_cs']} · BTS={m['h_bts']} · O2.5={m['h_o25']} · PPG={m['h_ppg']}

**Raw inputs — away:** W%={m['a_w']} · GF={m['a_gf']} · GA={m['a_ga']} · FTS={m['a_fts']} · CS={m['a_cs']} · BTS={m['a_bts']} · O2.5={m['a_o25']} · PPG={m['a_ppg']}
                        """)

                st.markdown("---")