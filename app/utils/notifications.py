"""
Notifications: in-app + email. Email is sent via RQ (Redis) when available, else a background thread.
"""
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import current_app, render_template

from app import db
from app.models import Notification


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


def _enqueue_or_send_email(to_email, title, body, category='general', link_url=None):
    """Use RQ if Redis is configured; otherwise send in a daemon thread (no Redis)."""
    try:
        redis_url = current_app.config.get('REDIS_URL')
    except Exception:
        redis_url = None
    if redis_url:
        try:
            from redis import Redis
            from rq import Queue
            redis_conn = Redis.from_url(redis_url)
            q = Queue(connection=redis_conn, default_timeout='2m')
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
    timeout = current_app.config.get('MAIL_TIMEOUT', 15)
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
    msg['From'] = current_app.config.get('MAIL_DEFAULT_SENDER')
    msg['To'] = to_email
    msg.attach(MIMEText(body, 'plain'))
    if html_content:
        msg.attach(MIMEText(html_content, 'html'))

    try:
        with smtplib.SMTP_SSL(
            current_app.config['MAIL_SERVER'],
            current_app.config['MAIL_PORT'],
            timeout=timeout,
        ) as server:
            server.login(
                current_app.config['MAIL_USERNAME'],
                current_app.config['MAIL_PASSWORD']
            )
            server.send_message(msg)
    except Exception as e:
        print(f"SMTP Failure: {e}")


def batch_enqueue_emails(email_list, title, body, category='general', link_url=None):
    """Enqueue emails for multiple recipients using a single Redis connection (or one thread for all)."""
    if not email_list:
        return
    try:
        redis_url = current_app.config.get('REDIS_URL')
    except Exception:
        redis_url = None
    if redis_url:
        try:
            from redis import Redis
            from rq import Queue
            redis_conn = Redis.from_url(redis_url)
            q = Queue(connection=redis_conn, default_timeout='2m')
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
