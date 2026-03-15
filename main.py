import streamlit as st
import pdfplumber
import re

# ==========================================
# 1. PDF PARSING LOGIC (Team-specific percentages)
# ==========================================

def clean_team_name(raw_name):
    # Removes time (e.g. "Blackburn 20:45" -> "Blackburn")
    return re.sub(r'\s*\d{1,2}:\d{2}$', '', raw_name).strip()

def extract_data_from_pdf(pdf_file):
    """
    Reads PDF and extracts individual team stats for 2.5+ and 3.5+.
    Assume the last two percentages on a team's line are 2.5+ and 3.5+.
    """
    structured_data = []
    current_league = "Unknown League"
    pending_home = None 
    
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text: continue
            
            for line in text.split('\n'):
                line = line.strip()
                
                # 1. Detect League Header
                if "Goals per match:" in line:
                    current_league = line.split("stats")[0].strip()
                    pending_home = None
                    continue

                # 2. Detect Match Data (Look for "last [number]")
                if re.search(r"\s+last\s+\d+", line):
                    
                    # Find all numbers followed by % on this specific line
                    percentages = [int(p) for p in re.findall(r'(\d+)%', line)]
                    
                    parts = re.split(r"\s+last\s+\d+", line, maxsplit=1)
                    if len(parts) < 2: continue
                    
                    team_name = clean_team_name(parts[0])
                    
                    # Grab the last two percentages (usually 2.5+ and 3.5+ columns at the end of the table)
                    o25_val = percentages[-2] if len(percentages) >= 2 else 0
                    o35_val = percentages[-1] if len(percentages) >= 1 else 0
                    
                    if pending_home is None:
                        # This is the Home Team line
                        pending_home = {
                            "name": team_name,
                            "o25": o25_val,
                            "o35": o35_val
                        }
                    else:
                        # This is the Away Team line -> Combine into one match
                        fixture = {
                            "league": current_league,
                            "home_team": pending_home["name"],
                            "home_25": pending_home["o25"],
                            "home_35": pending_home["o35"],
                            "away_team": team_name,
                            "away_25": o25_val,
                            "away_35": o35_val
                        }
                        
                        structured_data.append(fixture)
                        pending_home = None # Reset for the next match
                            
    return structured_data

# ==========================================
# 2. STREAMLIT UI & FILTERING
# ==========================================

st.set_page_config(page_title="Over 2.5/3.5 Filter", layout="wide")

st.title("⚽ PDF Filter: Over 2.5 & 3.5 Goals")
st.markdown("Extracts individual team percentages and filters them based on your exact numbers.")

# --- SIDEBAR UI ---
st.sidebar.header("1. Base Criteria")
st.sidebar.write("Set the minimum percentage required for a team to get a ✅ checkmark.")

# Replaced hardcoded numbers with number input boxes
base_25_min = st.sidebar.number_input("Minimum 2.5+ (%)", value=60, min_value=0, max_value=100, step=1)
base_35_min = st.sidebar.number_input("Minimum 3.5+ (%)", value=50, min_value=0, max_value=100, step=1)

st.sidebar.header("2. Filter Mode")
filter_mode = st.sidebar.radio(
    "How should matches be filtered?",
    options=[
        "Show All Matches",
        "At least ONE team meets base criteria",
        "BOTH teams meet base criteria",
        "One team's stats > Other team's by a specific gap"
    ],
    index=1
)

# Conditional inputs: Only show the "Gap" inputs if they select the 4th filter option
gap_25 = 0
gap_35 = 0
if filter_mode == "One team's stats > Other team's by a specific gap":
    st.sidebar.subheader("Set the Gap (Difference)")
    st.sidebar.write("How much higher does the dominating team's % need to be?")
    gap_25 = st.sidebar.number_input("2.5+ must be greater by at least (%)", value=10, min_value=1, max_value=100, step=1)
    gap_35 = st.sidebar.number_input("3.5+ must be greater by at least (%)", value=10, min_value=1, max_value=100, step=1)

# Helper function to check if a single team meets the base criteria
def team_meets_criteria(o25, o35):
    return o25 >= base_25_min and o35 >= base_35_min

# --- MAIN AREA ---
uploaded_file = st.file_uploader("Upload PDF File", type="pdf")

if uploaded_file is not None:
    with st.spinner("Extracting team stats from PDF..."):
        
        all_matches = extract_data_from_pdf(uploaded_file)
        filtered_matches = []
        
        # Apply the logic
        for m in all_matches:
            # Check Base Criteria
            home_pass_default = team_meets_criteria(m['home_25'], m['home_35'])
            away_pass_default = team_meets_criteria(m['away_25'], m['away_35'])
            
            # Check if one team is strictly greater than the other by the chosen GAP
            home_dominates = (m['home_25'] >= m['away_25'] + gap_25) and (m['home_35'] >= m['away_35'] + gap_35)
            away_dominates = (m['away_25'] >= m['home_25'] + gap_25) and (m['away_35'] >= m['home_35'] + gap_35)
            one_team_dominates = home_dominates or away_dominates
            
            # Filter matches based on dropdown selection
            if filter_mode == "Show All Matches":
                filtered_matches.append(m)
            elif filter_mode == "At least ONE team meets base criteria" and (home_pass_default or away_pass_default):
                filtered_matches.append(m)
            elif filter_mode == "BOTH teams meet base criteria" and (home_pass_default and away_pass_default):
                filtered_matches.append(m)
            elif filter_mode == "One team's stats > Other team's by a specific gap" and one_team_dominates:
                filtered_matches.append(m)
        
        # Display Results
        st.divider()
        st.subheader(f"Results: {len(filtered_matches)} matches found")
        
        if not filtered_matches:
            st.warning("No matches met the selected criteria. Try lowering your numbers.")
        
        for m in filtered_matches:
            with st.container():
                st.caption(m['league'])
                
                col1, col2 = st.columns([1, 1])
                
                # Determine Icons (Using > 0 for stars just to show who is mathematically higher overall)
                home_is_higher = (m['home_25'] > m['away_25']) and (m['home_35'] > m['away_35'])
                away_is_higher = (m['away_25'] > m['home_25']) and (m['away_35'] > m['home_35'])
                
                # HOME TEAM COLUMN
                with col1:
                    h_icon = "✅" if team_meets_criteria(m['home_25'], m['home_35']) else "➖"
                    h_star = "⭐" if home_is_higher else ""
                    
                    st.markdown(f"**{h_icon} {m['home_team']} {h_star}**")
                    st.write(f"2.5+: **{m['home_25']}%** | 3.5+: **{m['home_35']}%**")
                
                # AWAY TEAM COLUMN
                with col2:
                    a_icon = "✅" if team_meets_criteria(m['away_25'], m['away_35']) else "➖"
                    a_star = "⭐" if away_is_higher else ""
                    
                    st.markdown(f"**{a_icon} {m['away_team']} {a_star}**")
                    st.write(f"2.5+: **{m['away_25']}%** | 3.5+: **{m['away_35']}%**")
                
                st.markdown("---")