import streamlit as st
import vertexai
from vertexai.generative_models import GenerativeModel
import json
from pypdf import PdfReader
from google.oauth2 import service_account
from ics import Calendar, Event, DisplayAlarm
from datetime import datetime, timedelta # Added timedelta for date math
# --- PASSWORD PROTECTION ---
def check_password():
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == st.secrets["app_password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't keep password in memory
        else:
            st.session_state["password_correct"] = False

    # Return True if the user has already verified their password
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password
    st.text_input(
        "🔒 Please enter the Password to access this tool:", 
        type="password", 
        on_change=password_entered, 
        key="password"
    )
    
    if "password_correct" in st.session_state:
        st.error("😕 Password incorrect. Please ask the administrator.")
    return False

# STOP EVERYTHING if password is not correct
if not check_password():
    st.stop()
# --- CONFIGURATION ---
PROJECT_ID = "cc-claims" 
LOCATION = "us-central1"
KEY_PATH = "key.json"

# --- AUTHENTICATION ---
try:
    # 1. Try to load from Streamlit Cloud Secrets (Production)
    if "gcp_service_account" in st.secrets:
        # We parse the JSON string we pasted in Secrets
        key_info = json.loads(st.secrets["gcp_service_account"]["info"])
        credentials = service_account.Credentials.from_service_account_info(key_info)
        vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)
    
    # 2. Try to load from local file (Development/Localhost)
    else:
        credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
        vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)

except Exception as e:
    st.error(f"Authentication Failed: {e}")

# --- SYSTEM PROMPTS ---
ANALYST_PROMPT = """
ROLE: Senior Contract Administrator (Canadian Construction Law).
OBJECTIVE: Scan the contract and extract TWO things:
1. Project Metadata (Owner Name, Project Name, Contract Number).
2. Notification Requirements (The clauses).

OUTPUT FORMAT:
Return a SINGLE VALID JSON object with this structure:
{
  "metadata": {
    "owner_name": "Name of Owner/Client",
    "project_name": "Name of Project",
    "contract_number": "Contract Ref Number or 'TBD'"
  },
  "clauses": [
    {
      "clause_id": "GC 6.5.1",
      "topic": "Delays",
      "trigger_event": "Delay by Owner",
      "time_limit": "10 Working Days",
      "risk_level": "High"
    }
  ]
}
Output ONLY the JSON. No markdown.
"""

DRAFTER_PROMPT = """
ROLE: Expert Construction Claims Consultant (Canada).
OBJECTIVE: Draft a formal contractual notice.
INPUT DATA:
- Date: {date_str}
- Owner: {owner}
- Attention: {recipient}
- Project: {project}
- Contract #: {contract_num}
- Clause: {clause_id}
- User Cause: {cause}
- User Effect: {effect}

RULES:
1. TONE: Professional, firm, but collaborative. Avoid overly litigious language. Use "Please be advised..."
2. FORMAT: Standard Business Letter.
3. STRUCTURE:
   - Header: {date_str}
   - To: {owner}
   - Attention: {recipient}
   - Re: Notice of {topic} - {project} ({contract_num})
   - Opening: State clearly that on {date_str}, an issue was identified.
   - Body Paragraph 1 (The Facts): Describe what happened versus what was in the contract.
   - Body Paragraph 2 (The Impact): Use bullet points for Schedule/Cost impacts.
   - Contractual Reference: Cite {clause_id} as the basis for the notice.
   - Closing: "We request your direction..." and mention that detailed costs are being tracked.
"""

# --- HELPER FUNCTIONS ---
def extract_text_from_pdf(uploaded_file):
    try:
        reader = PdfReader(uploaded_file)
        text = ""
        for i, page in enumerate(reader.pages):
            if i > 50: break 
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
        return None

def analyze_contract(contract_text):
    model = GenerativeModel("gemini-2.0-flash-001") 
    prompt = f"{ANALYST_PROMPT}\nINPUT CONTRACT TEXT:\n{contract_text}"
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        st.error(f"AI Analysis Failed: {e}")
        return {"metadata": {}, "clauses": []}

def generate_notice_draft(clause, inputs, meta):
    model = GenerativeModel("gemini-2.0-flash-001")
    prompt = DRAFTER_PROMPT.format(
        date_str=inputs['date'],
        owner=meta['owner'],
        recipient=meta['recipient'],
        project=meta['project'],
        contract_num=meta['contract_num'],
        clause_id=clause['clause_id'],
        topic=clause['topic'],
        cause=inputs['cause'],
        effect=inputs['effect']
    )
    response = model.generate_content(prompt)
    return response.text

# --- UPDATED SMART CALENDAR FUNCTION ---
def create_calendar_file(clauses):
    c = Calendar()
    for item in clauses:
        e = Event()
        e.name = f"⚠️ NOTICE DUE: {item['topic']} ({item['clause_id']})"
        
        # Smart Logic: Try to find the number of days in the text
        # If it finds "10", it adds 10 days. Default is 7 days.
        days_to_add = 7 
        limit_text = str(item['time_limit']).lower()
        
        if "10" in limit_text: days_to_add = 10
        elif "5" in limit_text: days_to_add = 5
        elif "3" in limit_text: days_to_add = 3
        elif "24" in limit_text: days_to_add = 1 # 24 hours = 1 day
        elif "immediately" in limit_text: days_to_add = 0
        
        # Calculate the actual deadline
        due_date = datetime.now() + timedelta(days=days_to_add)
        e.begin = due_date
        
        # Add rich details to the calendar invite body
        e.description = f"""
        PROJECT NOTICE DEADLINE
        -----------------------
        Topic: {item['topic']}
        Clause: {item['clause_id']}
        Trigger: {item['trigger_event']}
        Exact Rule: {item['time_limit']}
        
        ACTION REQUIRED:
        Draft and submit notice immediately to preserve claim entitlement.
        """
        
        # Add a pop-up alarm 1 day before the deadline
        e.alarms.append(DisplayAlarm(trigger=timedelta(days=-1)))
        
        c.events.add(e)
    return c.serialize()

# --- UI ---
st.set_page_config(page_title="Construction Claims Notification Copilot", layout="wide")
st.title("Construction Claims Notification Copilot")

# Sidebar
with st.sidebar:
    st.header("1. Project Ingestion")
    uploaded_file = st.file_uploader("Upload Contract (PDF)", type="pdf")
    
    if uploaded_file and st.button("Analyze Contract"):
        with st.spinner("Extracting Project Details & Risks..."):
            text = extract_text_from_pdf(uploaded_file)
            if text:
                data = analyze_contract(text)
                st.session_state['analysis'] = data
                st.success("Extraction Complete!")

# Main Dashboard
if 'analysis' in st.session_state:
    data = st.session_state['analysis']
    meta = data.get('metadata', {})
    clauses = data.get('clauses', [])

    # Display Extracted Metadata
    st.subheader(f"📂 Project: {meta.get('project_name', 'Unknown Project')}")
    st.caption(f"Owner: {meta.get('owner_name', 'Unknown')} | Contract #: {meta.get('contract_number', 'N/A')}")
    
    st.divider()

    # Clause List
    st.subheader("2. Notification Matrix")
    for index, item in enumerate(clauses):
        with st.expander(f"⚠️ {item['clause_id']}: {item['topic']} ({item['time_limit']})"):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"**Trigger:** {item['trigger_event']}")
            with col2:
                if st.button("Draft Notice", key=f"btn_{index}"):
                    st.session_state['selected_clause'] = item
                    st.session_state['draft_mode'] = True

    # Smart Calendar Export
    st.markdown("### 📅 Risk Management")
    if st.button("Download Deadlines to Outlook/Cal (.ics)"):
        ics_data = create_calendar_file(clauses)
        st.download_button(
            label="⬇️ Click to Save Calendar Events",
            data=ics_data,
            file_name="project_deadlines.ics",
            mime="text/calendar"
        )

# Drafting Section
if st.session_state.get('draft_mode') and 'selected_clause' in st.session_state:
    st.divider()
    target = st.session_state['selected_clause']
    st.header(f"3. Draft Notice: {target['clause_id']}")
    
    with st.form("draft_form"):
        # Section A: Review Auto-Filled Details
        st.markdown("### Step 1: Confirm Details")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            owner_in = st.text_input("To (Owner Organization)", value=meta.get('owner_name', ''))
            recipient_in = st.text_input("Attention (Recipient Name)", value="Project Manager")
        with col_m2:
            proj_in = st.text_input("Project Name", value=meta.get('project_name', ''))
            num_in = st.text_input("Contract #", value=meta.get('contract_number', ''))

        # Section B: The Claim
        st.markdown("### Step 2: Describe the Event")
        date_in = st.date_input("Date of Event")
        cause_in = st.text_area("CAUSE (The Facts)", height=100, help="What happened on site?")
        effect_in = st.text_area("EFFECT (Impact)", height=100, help="Is work stopped? Is it costing money?")
        
        if st.form_submit_button("Generate Professional Draft"):
            # Update meta with user inputs
            final_meta = {
                "owner": owner_in, 
                "recipient": recipient_in, 
                "project": proj_in, 
                "contract_num": num_in
            }
            inputs = {
                "cause": cause_in, 
                "effect": effect_in, 
                "date": str(date_in)
            }
            
            with st.spinner("Drafting letter..."):
                draft = generate_notice_draft(target, inputs, final_meta)
                st.session_state['current_draft'] = draft

    # Final Result
    if 'current_draft' in st.session_state:
        st.subheader("4. Final Output")
        draft_text = st.session_state['current_draft']
        st.text_area("Review Draft:", draft_text, height=500)
        
        # Download Button
        st.download_button(
            label="📄 Download as Text File",
            data=draft_text,
            file_name=f"Notice_{target['clause_id']}.txt",
            mime="text/plain"

        )

