import pdfplumber
import re
import json
from flask import current_app
from openai import OpenAI

# Day names used for target selection
DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

def parse_schedule_pdf(file_path, target_day_index):
    """
    Parse a schedule PDF using an LLM to extract employee shifts for a specific day.
    
    Uses pdfplumber to extract raw text, then passes it to OpenAI GPT-4o-mini 
    to extract a structured JSON list of shifts. This allows the system to support 
    many popular formats (HotSchedules, 7Shifts, WhenIWork, etc.) without hardcoded 
    column heuristics.

    Args:
        file_path: Path to the PDF file
        target_day_index: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat

    Returns:
        List of dicts with 'name', 'start', and 'end' keys
    """
    target_day = DAY_NAMES[target_day_index]
    
    # 1. Extract raw text from the PDF
    pdf_text = ""
    try:
        with pdfplumber.open(file_path) as pdf:
            # We can extract text directly instead of reading tables
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pdf_text += text + "\n"
    except Exception as e:
        print(f"Error reading PDF: {str(e)}")
        raise
        
    if not pdf_text.strip():
        return []

    # 2. Extract using OpenAI
    api_key = current_app.config.get('OPENAI_API_KEY')
    if not api_key:
        print("Warning: OPENAI_API_KEY is not configured. Cannot process flexible schedules.")
        return []
        
    client = OpenAI(api_key=api_key)
    
    prompt = f"""
You are an AI assistant designed to extract work schedules from raw text parsed from a PDF.

The following text contains a weekly or daily work schedule. Your task is to extract ALL the working shifts 
assigned to employees specifically for {target_day}.

Output a JSON object with a single key "shifts" containing an array of objects.
Each object in the array must have exactly these three keys:
- "name": The employee's full name (e.g. "John Doe"). Clean up any extra tags like "Training" or "Total".
- "start": The start time in "H:MM AM/PM" format (e.g., "7:00 AM", "4:15 PM").
- "end": The end time in "H:MM AM/PM" format (e.g., "3:00 PM").

If the text has no shifts for {target_day}, return an empty array for "shifts".

--- START OF PDF TEXT ---
{pdf_text}
--- END OF PDF TEXT ---
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful data extraction assistant that outputs strictly structured JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        
        shifts = data.get("shifts", [])
        if not isinstance(shifts, list):
            shifts = []
            
        # 3. Clean and deduplicate the extracted data
        unique = []
        seen = set()
        for shift in shifts:
            name = shift.get('name')
            start = shift.get('start')
            end = shift.get('end')
            if not name or not start or not end:
                continue
                
            # Use fallback regex normalizer just to ensure exact format compliance
            start_norm = normalize_time(start) or start
            end_norm = normalize_time(end) or end
            
            key = (name, start_norm, end_norm)
            if key not in seen:
                seen.add(key)
                unique.append({
                    'name': name.strip(),
                    'start': start_norm,
                    'end': end_norm
                })
        return unique

    except Exception as e:
        print(f"Error querying OpenAI for schedule extraction: {e}")
        return []


def normalize_time(time_str):
    """
    Normalize time string to 'H:MM AM/PM' format to ensure UI consistency.

    Args:
        time_str: Time string like "7:00 AM" or "7:00AM" or "7AM"

    Returns:
        Normalized time string or None if invalid
    """
    try:
        if not time_str:
            return None
        time_str = str(time_str).strip().upper()

        match = re.match(r'(\d{1,2}):?(\d{2})?\s*(AM|PM)', time_str)
        if not match:
            return None

        hour = int(match.group(1))
        minute = match.group(2) or "00"
        period = match.group(3)

        if hour < 1 or hour > 12:
            return None
        if int(minute) > 59:
            return None

        return f"{hour}:{minute} {period}"

    except Exception:
        return None