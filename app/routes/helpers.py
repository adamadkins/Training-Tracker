from functools import wraps
from flask import abort
from flask_login import current_user


def manager_required(f):
    """Restricts access to Users with the 'manager' role only."""

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'manager':
            abort(403)
        return f(*args, **kwargs)

    return wrapped


def staff_required(f):
    """Restricts access to Users with 'manager' or 'trainer' roles."""

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['manager', 'trainer']:
            abort(403)
        return f(*args, **kwargs)

    return wrapped


def employee_required(f):
    """Ensures the user is logged in. Used for general employee/trainee views."""

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        return f(*args, **kwargs)

    return wrapped
