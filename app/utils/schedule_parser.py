import pdfplumber
import re
import json
from flask import current_app
from openai import OpenAI

# Day names used for target selection
DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


def parse_schedule_pdf(file_path, week_start_date):
    """
    Parse a schedule PDF to extract employee shifts for the ENTIRE week.

    Strategy:
      1. Try structured table extraction first (fast, accurate for grid PDFs
         like HotSchedules Extended Schedule Report).
      2. Fall back to LLM-based extraction for free-form / unstructured PDFs.

    Args:
        file_path: Path to the PDF file
        week_start_date: ISO date string for the week's Sunday (e.g. '2026-02-15')

    Returns:
        Dict mapping day index strings '0'-'6' to lists of shift dicts
        with 'name', 'start', and 'end' keys.
    """
    empty_week = {str(i): [] for i in range(7)}

    try:
        with pdfplumber.open(file_path) as pdf:
            # --- Attempt 1: structured table extraction ---
            result = _try_table_extraction(pdf)
            if result is not None:
                return result

            # --- Attempt 2: LLM-based extraction ---
            pdf_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pdf_text += text + "\n"

            if not pdf_text.strip():
                return empty_week

            return _llm_extraction(pdf_text, week_start_date)

    except Exception as e:
        print(f"Error in parse_schedule_pdf: {e}")
        return empty_week


# ---------------------------------------------------------------------------
#  Strategy 1 – Table-based extraction (grid PDFs)
# ---------------------------------------------------------------------------

def _try_table_extraction(pdf):
    """
    Attempt to parse a grid-style schedule PDF by extracting tables.

    Returns a week dict if successful, or None if the PDF does not appear
    to be a grid schedule.
    """
    weekly_shifts = {str(i): [] for i in range(7)}

    # Gather every table from every page
    all_tables = []
    for page in pdf.pages:
        tables = page.extract_tables()
        if tables:
            all_tables.extend(tables)

    if not all_tables:
        return None  # no tables → fall back to LLM

    found_schedule_grid = False

    for table in all_tables:
        if not table or len(table) < 2:
            continue

        # ---- Detect the column layout ----
        header = table[0]
        if header is None:
            continue

        day_col_map = _detect_day_columns(header)
        if not day_col_map:
            continue  # not a schedule grid

        found_schedule_grid = True
        name_col = 0  # employee name is always the first column

        # ---- Walk data rows ----
        for row in table[1:]:
            if not row:
                continue

            # Extract and clean the employee name
            raw_name = row[name_col] if name_col < len(row) else None
            if not raw_name:
                continue
            name = _clean_name(raw_name)
            if not name:
                continue

            # Extract shifts for each mapped day column
            for day_idx, col_idx in day_col_map.items():
                if col_idx >= len(row):
                    continue
                cell = row[col_idx]
                if not cell or not cell.strip():
                    continue

                shifts = _parse_cell_shifts(cell)
                for start, end in shifts:
                    start_n = normalize_time(start) or start
                    end_n = normalize_time(end) or end
                    weekly_shifts[str(day_idx)].append({
                        'name': name,
                        'start': start_n,
                        'end': end_n,
                    })

    return weekly_shifts if found_schedule_grid else None


def _detect_day_columns(header_row):
    """
    Scan a table header row to find which column indices correspond to
    which day of the week (0=Sun … 6=Sat).

    Returns dict  {day_index: col_index}  or empty dict if not a schedule.
    """
    mapping = {}
    for col_idx, cell in enumerate(header_row):
        if not cell:
            continue
        cell_upper = cell.upper()
        for day_idx, day_name in enumerate(DAY_NAMES):
            if day_name.upper() in cell_upper:
                mapping[day_idx] = col_idx
                break
    return mapping


def _clean_name(raw):
    """
    Clean an employee name cell: collapse newlines, strip tags like
    'Total', 'Day', 'Training', etc.
    """
    # Collapse newlines into a single space
    name = re.sub(r'\s+', ' ', raw).strip()

    # Remove trailing / leading noise tokens
    noise = ['Total', 'Day', 'Training', 'Leadership']
    for n in noise:
        name = re.sub(rf'\b{n}\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name).strip()

    # If the name is empty or just a number, skip
    if not name or name.replace(' ', '').isdigit():
        return None
    return name


def _parse_cell_shifts(cell_text):
    """
    Parse one or more 'start - end' time ranges from a table cell.
    Handles newlines, 'Leadership' prefixes, '[Shift Released]' tags, etc.

    Returns list of (start, end) tuples.
    """
    # Flatten and clean
    text = cell_text.replace('\n', ' ').strip()

    # Remove tags like [Shift Released] or Leadership prefix
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'(?i)\bleadership\b', '', text).strip()

    # Match patterns like  "4:15 PM - 9:00 PM"
    pattern = r'(\d{1,2}:\d{2}\s*[APap][Mm])\s*-\s*(\d{1,2}:\d{2}\s*[APap][Mm])'
    return re.findall(pattern, text)


# ---------------------------------------------------------------------------
#  Strategy 2 – LLM-based extraction (free-form PDFs)
# ---------------------------------------------------------------------------

def _llm_extraction(pdf_text, week_start_date):
    """
    Send the raw PDF text to OpenAI gpt-4o-mini and ask it to return
    a structured week of shifts.
    """
    empty_week = {str(i): [] for i in range(7)}

    api_key = current_app.config.get('OPENAI_API_KEY')
    if not api_key:
        print("Warning: OPENAI_API_KEY is not configured.")
        return empty_week

    client = OpenAI(api_key=api_key)

    prompt = f"""
You are an AI assistant designed to extract work schedules from raw text parsed from a PDF.

The following text contains a weekly or daily work schedule. The first day of the week (Sunday) falls on {week_start_date}.
Your task is to extract ALL the working shifts assigned to ALL employees for the ENTIRE week.

Output a JSON object perfectly mapping the 7 day integers (as strings "0" through "6") to an array of objects representing their shifts.
0 = Sunday, 1 = Monday, 2 = Tuesday, 3 = Wednesday, 4 = Thursday, 5 = Friday, 6 = Saturday.

Each object in the array must have exactly these three keys:
- "name": The employee's full name (e.g. "John Doe"). Clean up any extra tags like "Training" or "Total".
- "start": The start time in "H:MM AM/PM" format (e.g., "7:00 AM", "4:15 PM").
- "end": The end time in "H:MM AM/PM" format (e.g., "3:00 PM").

If a day has no shifts, return an empty array for that day's integer key.

--- START OF PDF TEXT ---
{pdf_text}
--- END OF PDF TEXT ---
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful data extraction assistant that outputs strictly structured JSON mapping days 0-6 to arrays of shifts."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        data = json.loads(content)

        weekly_shifts = {str(i): [] for i in range(7)}
        for day_str in weekly_shifts.keys():
            shifts = data.get(day_str, [])
            if not isinstance(shifts, list):
                shifts = []
            unique = []
            seen = set()
            for shift in shifts:
                name = shift.get('name')
                start = shift.get('start')
                end = shift.get('end')
                if not name or not start or not end:
                    continue
                start_norm = normalize_time(start) or start
                end_norm = normalize_time(end) or end
                key = (name, start_norm, end_norm)
                if key not in seen:
                    seen.add(key)
                    unique.append({
                        'name': str(name).strip(),
                        'start': start_norm,
                        'end': end_norm
                    })
            weekly_shifts[day_str] = unique
        return weekly_shifts

    except Exception as e:
        print(f"Error querying OpenAI for schedule extraction: {e}")
        return empty_week


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

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