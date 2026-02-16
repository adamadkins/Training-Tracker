import pdfplumber
import re


# Day names used in HotSchedules header rows
DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


def parse_schedule_pdf(file_path, target_day_index):
    """
    Parse HotSchedules Extended Schedule Report PDF to extract employee
    shifts for a specific day.

    Uses pdfplumber's table extraction which correctly handles the
    HotSchedules grid layout, then dynamically detects which column
    corresponds to which day of the week.

    Args:
        file_path: Path to the PDF file
        target_day_index: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat

    Returns:
        List of dicts with 'name', 'start', and 'end' keys
    """
    target_day = DAY_NAMES[target_day_index]
    extracted_data = []

    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Find the target day's column index by scanning for a
                    # header row that contains day names like "Sunday,",
                    # "Monday,\nFebruary..." etc.
                    day_col = _find_day_column(table, target_day)

                    if day_col is None:
                        # No header on this table/page — it's a continuation
                        # from the previous page. For continuation tables the
                        # column layout matches the *last seen* header. We
                        # try to infer by checking if column count matches a
                        # known layout (9-col standard).
                        day_col = _infer_day_column_continuation(table, target_day_index)

                    if day_col is None:
                        continue

                    # Process each data row
                    for row in table:
                        if not row or len(row) <= day_col:
                            continue

                        # Extract the employee name (first non-None column)
                        name = _extract_name(row)
                        if not name:
                            continue

                        # Get the shift cell for the target day
                        cell = row[day_col]
                        if not cell:
                            continue

                        cell_text = cell.replace('\n', ' ').strip()

                        # Skip empty, off, or non-shift cells
                        if not cell_text:
                            continue
                        if any(skip in cell_text.upper() for skip in ['N/A', 'OFF', '---', 'TOTAL', 'STATUS', 'POSTED']):
                            continue

                        # Skip header-like cells that contain day/month names
                        if any(d in cell_text for d in DAY_NAMES):
                            continue
                        if 'February' in cell_text or 'January' in cell_text or '2026' in cell_text:
                            continue

                        # Extract time range from the cell
                        shift = _extract_shift(cell_text)
                        if shift:
                            extracted_data.append({
                                'name': name,
                                'start': shift[0],
                                'end': shift[1]
                            })

    except Exception as e:
        print(f"Error parsing PDF: {str(e)}")
        raise

    # Deduplicate (same name can appear if rows span across pages)
    seen = set()
    unique = []
    for entry in extracted_data:
        key = (entry['name'], entry['start'], entry['end'])
        if key not in seen:
            seen.add(key)
            unique.append(entry)

    return unique


def _find_day_column(table, target_day):
    """
    Scan the table for a header row containing day-of-week names and return
    the column index that corresponds to target_day.
    """
    for row in table:
        if not row:
            continue

        # Check each cell for day names
        day_columns = {}
        for col_idx, cell in enumerate(row):
            if not cell:
                continue
            cell_clean = cell.replace('\n', ' ').strip()
            for day in DAY_NAMES:
                # Header cells look like "Sunday,\nFebruary\n15, 2026" or
                # "Sunday, February 15, 2026"
                if cell_clean.startswith(day):
                    day_columns[day] = col_idx
                    break

        # If we found at least 3 day names, this is likely the header row
        if len(day_columns) >= 3:
            if target_day in day_columns:
                return day_columns[target_day]
            # Target day might not be in this section's header (e.g. it
            # only covers a sub-range). Return None.
            return None

    return None


def _infer_day_column_continuation(table, target_day_index):
    """
    For continuation tables on subsequent pages that have no header row,
    try to infer the column mapping based on column count.

    Standard HotSchedules layout:
      9 cols: [Name, Total/Day, Sun, Mon, Tue, Wed, Thu, Fri, Sat]
      -> day_col = target_day_index + 2

    For pages where pdfplumber finds a different column count due to merged
    cells or different sections, we try the 9-col mapping if the table has
    enough columns.
    """
    if not table or not table[0]:
        return None

    num_cols = len(table[0])

    # 9-col is the standard layout
    if num_cols == 9:
        col = target_day_index + 2
        if col < num_cols:
            return col

    # 12-col can appear on pages with multiple sections. The top section
    # (continuation) may follow a different layout. Check if the rows
    # have "Total\nDay" to identify which are data rows, and look at cell
    # content to figure out the mapping. We fall back to looking for
    # a header in this table.
    return None


def _extract_name(row):
    """
    Extract and clean the employee name from a table row.
    The name is typically in the first column (index 0).
    """
    if not row or not row[0]:
        return None

    name = row[0].replace('\n', ' ').strip()

    # Remove common non-name content
    for noise in ['Total', 'Day', 'Front of House', 'Training', 'Status', 'Posted',
                  'Support and', 'schedules:', 'technical', 'emergencies']:
        if noise.lower() in name.lower() and len(name) < 60:
            # If the cell is mostly noise, skip it
            cleaned = name
            for n in ['Total', 'Day']:
                cleaned = cleaned.replace(n, '').strip()
            if len(cleaned) < 3:
                return None

    # Clean up name
    name = name.replace('Total', '').replace('Day', '').strip()
    name = re.sub(r'\s+', ' ', name).strip()

    # Skip if too short or contains schedule metadata
    if len(name) < 3:
        return None
    if any(skip in name.upper() for skip in ['STATUS', 'POSTED', 'SCHEDULE', 'EMPLOYEE',
                                              'FRONT OF', 'SUPPORT', 'LEADERSHIP']):
        return None

    return name


def _extract_shift(text):
    """
    Extract start and end times from a cell like "7:00 AM - 4:15 PM"
    or "7:00 AM -\n4:15 PM".

    Returns (start_normalized, end_normalized) or None.
    """
    # Remove common prefixes like "Leadership" or "[Shift Released]"
    text = re.sub(r'\[.*?\]', '', text).strip()
    text = re.sub(r'^(Leadership|Training)\s*', '', text, flags=re.IGNORECASE).strip()

    # Match time range: "HH:MM AM/PM - HH:MM AM/PM"
    # The dash can be -, –, —, or surrounded by whitespace/newlines
    match = re.search(
        r'(\d{1,2}:\d{2})\s*(AM|PM)\s*[-–—]\s*(\d{1,2}:\d{2})\s*(AM|PM)',
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    start_str = f"{match.group(1)} {match.group(2).upper()}"
    end_str = f"{match.group(3)} {match.group(4).upper()}"

    start_norm = normalize_time(start_str)
    end_norm = normalize_time(end_str)

    if start_norm and end_norm:
        return (start_norm, end_norm)
    return None


def normalize_time(time_str):
    """
    Normalize time string to 'H:MM AM/PM' format.

    Args:
        time_str: Time string like "7:00 AM" or "7:00AM" or "7AM"

    Returns:
        Normalized time string or None if invalid
    """
    try:
        time_str = time_str.strip().upper()

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