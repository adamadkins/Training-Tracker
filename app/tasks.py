"""
Background jobs (RQ). Run with: rq worker -u REDIS_URL
Jobs run in a separate process so the web request returns immediately.
"""
from app import create_app


def _get_app():
    from flask import has_app_context
    if has_app_context():
        from flask import current_app
        return current_app._get_current_object()
    return create_app()


def send_email_task(to_email, title, body, category='general', link_url=None):
    """RQ job: send one email. Called from notifications.enqueue_email()."""
    app = _get_app()
    with app.app_context():
        from app.utils.notifications import _send_html_email_impl
        _send_html_email_impl(to_email, title, body, category, link_url)
