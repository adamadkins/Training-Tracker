"""
7Shifts API client — OAuth 2.0 (client credentials) edition.

Adam registers once as a 7Shifts partner → CLIENT_ID / CLIENT_SECRET go in .env.
Each client org authorizes via a redirect; the callback stores their company_id.
This module exchanges creds for an access token and fetches shifts.

Token endpoint:  POST https://api.7shifts.com/oauth2/token
Shifts endpoint: GET  https://api.7shifts.com/v2/company/{company_id}/shifts
"""

import requests
from datetime import datetime, timedelta, timezone
from flask import current_app


def get_access_token(client_id, client_secret):
    """
    Get a fresh access token using client_credentials grant.
    Returns (access_token, expires_at_utc) or raises.
    """
    resp = requests.post(
        'https://api.7shifts.com/oauth2/token',
        data={
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get('access_token')
    expires_in = data.get('expires_in', 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    return token, expires_at


def ensure_valid_token(org, db_session):
    """
    Make sure the org has a valid (non-expired) access token.
    If expired (or missing), fetch a new one and persist it.
    Returns the access_token string.
    """
    now = datetime.now(timezone.utc)
    expires = org.sevenshifts_token_expires_at
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if org.sevenshifts_access_token and expires and now < expires:
        return org.sevenshifts_access_token

    client_id = current_app.config.get('SEVENSHIFTS_CLIENT_ID', '')
    client_secret = current_app.config.get('SEVENSHIFTS_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        raise RuntimeError("7Shifts CLIENT_ID / CLIENT_SECRET not configured in .env")

    token, expires_at = get_access_token(client_id, client_secret)
    org.sevenshifts_access_token = token
    org.sevenshifts_token_expires_at = expires_at
    db_session.commit()
    return token


def fetch_weekly_shifts(org, db_session, week_start_str):
    """
    Pull one week of shifts from the 7Shifts API.

    Args:
        org:            Organization model instance (must have sevenshifts_company_id)
        db_session:     SQLAlchemy session (for persisting refreshed tokens)
        week_start_str: ISO date for Sunday of the target week ('2026-02-15')

    Returns:
        dict mapping day-index strings '0'-'6' to lists of shift dicts
    """
    empty_week = {str(i): [] for i in range(7)}

    if not org.sevenshifts_company_id:
        print("7Shifts: no company_id on org")
        return empty_week

    try:
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return empty_week

    week_end = week_start + timedelta(days=6)

    access_token = ensure_valid_token(org, db_session)

    params = {
        'start[gte]': week_start.strftime('%Y-%m-%d'),
        'start[lte]': week_end.strftime('%Y-%m-%d'),
        'limit': 500,
    }

    try:
        resp = requests.get(
            f'https://api.7shifts.com/v2/company/{org.sevenshifts_company_id}/shifts',
            headers={'Authorization': f'Bearer {access_token}'},
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
        user = shift.get('user', {})
        first = user.get('first_name', '')
        last = user.get('last_name', '')
        name = f"{first} {last}".strip()
        if not name:
            name = shift.get('user_name', '')
        if not name:
            continue

        start_dt = _parse_dt(shift.get('start'))
        end_dt = _parse_dt(shift.get('end'))
        if not start_dt or not end_dt:
            continue

        day_of_week = start_dt.weekday()
        day_idx = (day_of_week + 1) % 7

        weekly[str(day_idx)].append({
            'name': name,
            'start': _fmt_time(start_dt),
            'end': _fmt_time(end_dt),
        })

    return weekly


def _parse_dt(val):
    if not val:
        return None
    try:
        val = str(val).replace('Z', '+00:00')
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _fmt_time(dt):
    if not hasattr(dt, 'strftime'):
        return str(dt)
    return dt.strftime('%I:%M %p').lstrip('0')
