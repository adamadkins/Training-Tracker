"""
Notifications: in-app + email. Email is sent via RQ (Redis) when available, else a background thread.
"""
import logging
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import current_app, render_template

from app import db
from app.models import Notification

log = logging.getLogger(__name__)


def notify(user, title, body, category='general', link_url=None, email_only=False):
    """
    In-app notification (sync) + email. Email is enqueued (RQ) or sent in a thread so the request returns immediately.
    """
    settings = getattr(user, 'settings', None)
    wants_in_app = getattr(settings, 'notify_in_app', True) if settings else True
    wants_email = getattr(settings, 'notify_email', True) if settings else True

    if wants_in_app and not email_only:
        db.session.add(Notification(
            user_id=user.id,
            title=title,
            body=body,
            category=category,
            link_url=link_url,
        ))

    if wants_email and user.email:
        to_email = str(user.email)
        _enqueue_or_send_email(to_email, title, body, category, link_url)


def _get_redis_queue():
    """Get an RQ Queue with a short socket timeout, or None if Redis is unavailable."""
    try:
        redis_url = current_app.config.get('REDIS_URL')
    except Exception:
        return None
    if not redis_url:
        return None
    try:
        from redis import Redis
        from rq import Queue
        redis_conn = Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        redis_conn.ping()  # fail fast if unreachable
        return Queue(connection=redis_conn, default_timeout='2m')
    except Exception:
        return None


def _enqueue_or_send_email(to_email, title, body, category='general', link_url=None):
    """Use RQ if Redis is configured; otherwise send in a daemon thread (no Redis)."""
    q = _get_redis_queue()
    if q:
        try:
            q.enqueue(
                'app.tasks.send_email_task',
                to_email, title, body, category, link_url,
                job_timeout=60,
            )
            return
        except Exception:
            pass
    # Fallback: background thread (no Redis or enqueue failed)
    app = current_app._get_current_object()
    t = threading.Thread(
        target=_send_email_background,
        args=(app, to_email, title, body, category, link_url),
        daemon=True,
    )
    t.start()


def _send_email_background(app, to_email, title, body, category='general', link_url=None):
    with app.app_context():
        _send_html_email_impl(to_email, title, body, category, link_url)


def _send_html_email_impl(to_email, title, body, category='general', link_url=None):
    username = current_app.config.get('MAIL_USERNAME', '')
    password = current_app.config.get('MAIL_PASSWORD', '')
    server_host = current_app.config.get('MAIL_SERVER', '')
    sender = current_app.config.get('MAIL_DEFAULT_SENDER', '') or username

    if not all([username, password, server_host]):
        log.warning("Email not configured (MAIL_USERNAME/MAIL_PASSWORD/MAIL_SERVER missing) — skipping email to %s", to_email)
        return

    timeout = current_app.config.get('MAIL_TIMEOUT', 15)
    port = int(current_app.config.get('MAIL_PORT', 587))
    use_ssl = current_app.config.get('MAIL_USE_SSL', False)
    use_tls = current_app.config.get('MAIL_USE_TLS', True)

    try:
        html_content = render_template(
            'email/notification.html',
            title=title,
            body=body,
            category=category,
            link_url=link_url,
        )
    except Exception:
        html_content = None

    msg = MIMEMultipart('alternative')
    msg['Subject'] = title
    msg['From'] = sender
    msg['To'] = to_email
    msg.attach(MIMEText(body, 'plain'))
    if html_content:
        msg.attach(MIMEText(html_content, 'html'))

    try:
        if use_ssl:
            # SSL wraps the connection immediately (typically port 465)
            server = smtplib.SMTP_SSL(server_host, port, timeout=timeout)
        else:
            # Plain connection + optional STARTTLS upgrade (typically port 587)
            server = smtplib.SMTP(server_host, port, timeout=timeout)
            if use_tls:
                server.starttls()

        with server:
            server.login(username, password)
            server.send_message(msg)
        log.info("Email sent to %s: %s", to_email, title)
    except Exception as e:
        log.error("SMTP Failure sending to %s: %s", to_email, e, exc_info=True)


def batch_enqueue_emails(email_list, title, body, category='general', link_url=None):
    """Enqueue emails for multiple recipients using a single Redis connection (or one thread for all)."""
    if not email_list:
        return
    q = _get_redis_queue()
    if q:
        try:
            for to_email in email_list:
                q.enqueue(
                    'app.tasks.send_email_task',
                    to_email, title, body, category, link_url,
                    job_timeout=60,
                )
            return
        except Exception:
            pass
    # Fallback: single background thread sends all emails sequentially
    app = current_app._get_current_object()
    t = threading.Thread(
        target=_send_batch_emails_background,
        args=(app, email_list, title, body, category, link_url),
        daemon=True,
    )
    t.start()


def _send_batch_emails_background(app, email_list, title, body, category='general', link_url=None):
    with app.app_context():
        for to_email in email_list:
            _send_html_email_impl(to_email, title, body, category, link_url)


def send_notification_email(user, title, body):
    """Legacy: email only, no in-app record. Uses queue or thread."""
    if not user.email:
        return
    _enqueue_or_send_email(str(user.email), title, body, 'general', None)
