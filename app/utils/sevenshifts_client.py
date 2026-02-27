"""
7Shifts API client.

Fetches published shift schedules using the 7Shifts REST API v2
and returns them in the same format the Smart Builder expects:
    { "0": [...shifts...], "1": [...], ..., "6": [...] }

Docs: https://developers.7shifts.com
Auth: OAuth 2.0 Client Credentials → Bearer token
Endpoint: GET /v2/company/{company_id}/shifts
"""

import requests
from datetime import datetime, timedelta


def fetch_weekly_shifts(client_id, client_secret, company_id, week_start_str):
    """
    Pull one week of shifts from the 7Shifts API.

    Args:
        client_id:      OAuth Client ID
        client_secret:  OAuth Client Secret
        company_id:     7Shifts Company (location group) ID
        week_start_str: ISO date for Sunday of the target week ('2026-02-15')

    Returns:
        dict mapping day-index strings '0'-'6' to lists of shift dicts
        [{'name': '...', 'start': '...', 'end': '...'}]
    """
    empty_week = {str(i): [] for i in range(7)}

    try:
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        print(f"Invalid week_start_str: {week_start_str}")
        return empty_week

    week_end = week_start + timedelta(days=6)

    # --- Step 1: Get OAuth access token ---
    try:
        token_resp = requests.post(
            'https://api.7shifts.com/oauth2/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': 'read:shifts',
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get('access_token')
        if not access_token:
            print("7Shifts: no access_token in response")
            return empty_week
    except requests.RequestException as e:
        print(f"7Shifts token error: {e}")
        return empty_week

    # --- Step 2: Fetch shifts ---
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {
        'start[gte]': week_start.strftime('%Y-%m-%d'),
        'start[lte]': week_end.strftime('%Y-%m-%d'),
        'limit': 500,
    }

    try:
        resp = requests.get(
            f'https://api.7shifts.com/v2/company/{company_id}/shifts',
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"7Shifts shifts error: {e}")
        return empty_week

    shifts_list = data.get('data', [])
    weekly = {str(i): [] for i in range(7)}

    for shift in shifts_list:
        # Extract employee name
        user = shift.get('user', {})
        first = user.get('first_name', '')
        last = user.get('last_name', '')
        name = f"{first} {last}".strip()
        if not name:
            name = shift.get('user_name', '')
        if not name:
            continue

        # Parse start/end times
        start_dt = _parse_dt(shift.get('start'))
        end_dt = _parse_dt(shift.get('end'))
        if not start_dt or not end_dt:
            continue

        # Map to day index (0=Sun..6=Sat)
        day_of_week = start_dt.weekday()      # Mon=0 … Sun=6
        day_idx = (day_of_week + 1) % 7       # convert to Sun=0 … Sat=6

        weekly[str(day_idx)].append({
            'name': name,
            'start': _fmt_time(start_dt),
            'end': _fmt_time(end_dt),
        })

    return weekly


def _parse_dt(val):
    """Parse an ISO-8601 datetime string into a datetime."""
    if not val:
        return None
    try:
        val = str(val).replace('Z', '+00:00')
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _fmt_time(dt):
    """Format a datetime into 'H:MM AM/PM' (cross-platform)."""
    if not hasattr(dt, 'strftime'):
        return str(dt)
    return dt.strftime('%I:%M %p').lstrip('0')
