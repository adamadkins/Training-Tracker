"""
HotSchedules (Fourth) API client.

Fetches published shift schedules using the Fourth Schedules API
and returns them in the same format the Smart Builder expects:
    { "0": [...shifts...], "1": [...], ..., "6": [...] }

Docs: https://developers.fourth.com/docs/schedules-getting-started
Auth: Basic (username:password, Base64-encoded)
Endpoint: GET {root}/shifts?fromDate=YYYYMMDD&toDate=YYYYMMDD
"""

import requests
import re
from datetime import datetime, timedelta


DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday',
             'Thursday', 'Friday', 'Saturday']


def fetch_weekly_shifts(api_url, username, password, week_start_str):
    """
    Pull one week of shifts from the Fourth Schedules API.

    Args:
        api_url:        Root API URL (e.g. https://api.hotschedules.io/NAMESPACE)
        username:       Basic-auth username issued by Fourth
        password:       Basic-auth password issued by Fourth
        week_start_str: ISO date for Sunday of the target week ('2026-02-15')

    Returns:
        dict mapping day-index strings '0'-'6' to lists of shift dicts
        [{'name': '...', 'start': '...', 'end': '...'}]
    """
    empty_week = {str(i): [] for i in range(7)}

    # Build date range: Sunday → Saturday
    try:
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        print(f"Invalid week_start_str: {week_start_str}")
        return empty_week

    week_end = week_start + timedelta(days=6)
    from_date = week_start.strftime('%Y%m%d')
    to_date = week_end.strftime('%Y%m%d')

    # Normalize the root URL
    root = api_url.rstrip('/')
    url = f"{root}/shifts"

    try:
        resp = requests.get(
            url,
            params={'fromDate': from_date, 'toDate': to_date},
            auth=(username, password),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"HotSchedules API error: {e}")
        return empty_week

    # The API returns a list of shift objects.  Each shift has at minimum:
    #   - employeeName  (or firstName / lastName)
    #   - startDateTime (UTC ISO-8601)
    #   - endDateTime   (UTC ISO-8601)
    # Exact field names depend on Fourth's version; we handle common variants.
    shifts_list = data if isinstance(data, list) else data.get('shifts', data.get('data', []))

    weekly = {str(i): [] for i in range(7)}

    for shift in shifts_list:
        name = _extract_name(shift)
        start_dt = _parse_dt(shift.get('startDateTime') or shift.get('start_date_time') or shift.get('startDate'))
        end_dt = _parse_dt(shift.get('endDateTime') or shift.get('end_date_time') or shift.get('endDate'))

        if not name or not start_dt or not end_dt:
            continue

        # Map to the correct day index (0=Sun..6=Sat) based on END date
        # (per Fourth docs: a shift "belongs to the day on which it ends")
        day_of_week = end_dt.weekday()       # Mon=0 … Sun=6
        day_idx = (day_of_week + 1) % 7      # convert to Sun=0 … Sat=6

        weekly[str(day_idx)].append({
            'name': name,
            'start': _fmt_time(start_dt),
            'end': _fmt_time(end_dt),
        })

    return weekly


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _extract_name(shift):
    """Pull the employee's full name from various possible API field shapes."""
    if 'employeeName' in shift:
        return str(shift['employeeName']).strip()
    first = shift.get('firstName') or shift.get('first_name') or ''
    last = shift.get('lastName') or shift.get('last_name') or ''
    full = f"{first} {last}".strip()
    return full if full else None


def _parse_dt(val):
    """Parse an ISO-8601 datetime string (or epoch ms) into a local datetime."""
    if not val:
        return None
    if isinstance(val, (int, float)):
        return datetime.utcfromtimestamp(val / 1000)
    try:
        # Handle common ISO formats
        val = str(val).replace('Z', '+00:00')
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _fmt_time(dt):
    """Format a datetime into 'H:MM AM/PM' (cross-platform)."""
    if not hasattr(dt, 'strftime'):
        return str(dt)
    return dt.strftime('%I:%M %p').lstrip('0')
