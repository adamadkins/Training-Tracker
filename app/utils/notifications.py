"""
Notifications: in-app + email. Email is sent via RQ (Redis) when available, else a background thread.
"""
import logging
import socket
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

from flask import current_app, render_template

from app import db
from app.models import Notification, PushToken


def notify(user, title, body, category='general', link_url=None, email_only=False, send_email=True):
    """
    In-app notification (sync) + optional email + optional push.
    Pass send_email=False to create only an in-app notification without consuming an email credit.
    """
    settings = getattr(user, 'settings', None)
    wants_in_app = getattr(settings, 'notify_in_app', True) if settings else True
    wants_email = getattr(settings, 'notify_email', True) if settings else True
    wants_push = getattr(settings, 'notify_push', True) if settings else True

    if wants_in_app and not email_only:
        db.session.add(Notification(
            user_id=user.id,
            title=title,
            body=body,
            category=category,
            link_url=link_url,
        ))

    if send_email and wants_email and user.email:
        to_email = str(user.email)
        _enqueue_or_send_email(to_email, title, body, category, link_url)

    if wants_push and not email_only:
        _enqueue_or_send_push(user.id, title, body, link_url)


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
    # Use SendGrid if API key is set (works over HTTPS, no SMTP port needed)
    sendgrid_key = current_app.config.get('SENDGRID_API_KEY', '')
    sender = current_app.config.get('MAIL_DEFAULT_SENDER', '') or current_app.config.get('MAIL_USERNAME', '')
    if sendgrid_key and sender:
        try:
            html_content = None
            try:
                html_content = render_template(
                    'email/notification.html',
                    title=title, body=body, category=category, link_url=link_url,
                )
            except Exception:
                pass
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, Content
            sg_mail = Mail(
                from_email=sender,
                to_emails=to_email,
                subject=title,
                plain_text_content=body,
            )
            if html_content:
                sg_mail.add_content(Content('text/html', html_content))
            sg = SendGridAPIClient(sendgrid_key)
            response = sg.send(sg_mail)
            logger.info("SendGrid sent to %s: %s (status %s)", to_email, title, response.status_code)
            return
        except Exception as e:
            logger.exception("SendGrid failure sending to %s: %s", to_email, e)
            return

    username = current_app.config.get('MAIL_USERNAME', '')
    password = current_app.config.get('MAIL_PASSWORD', '')
    server_host = current_app.config.get('MAIL_SERVER', '')
    sender = sender or username

    if not all([username, password, server_host]):
        logger.warning("Email not configured (MAIL_USERNAME/MAIL_PASSWORD/MAIL_SERVER missing) — skipping email to %s", to_email)
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
        # Prefer IPv4 so we don't get "Network is unreachable" on hosts with no IPv6 (e.g. some VPS)
        _orig_getaddrinfo = socket.getaddrinfo
        def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
            if family in (0, socket.AF_UNSPEC):
                res = _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                if res:
                    return res
            return _orig_getaddrinfo(host, port, family, type, proto, flags)
        socket.getaddrinfo = _ipv4_first
        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(server_host, port, timeout=timeout)
            else:
                server = smtplib.SMTP(server_host, port, timeout=timeout)
                if use_tls:
                    server.starttls()
            with server:
                server.login(username, password)
                server.send_message(msg)
            logger.info("Email sent to %s: %s", to_email, title)
        finally:
            socket.getaddrinfo = _orig_getaddrinfo
    except Exception as e:
        logger.exception("SMTP failure sending to %s: %s", to_email, e)


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


def _get_firebase_app():
    """Return initialized Firebase app or None if not configured."""
    try:
        import firebase_admin
        from firebase_admin import credentials
        if firebase_admin.get_app():
            return firebase_admin.get_app()
        cred_json = current_app.config.get('FIREBASE_CREDENTIALS_JSON')
        cred_path = current_app.config.get('FIREBASE_CREDENTIALS_PATH')
        if cred_json:
            import json
            cred_dict = json.loads(cred_json)
            return firebase_admin.initialize_app(credentials.Certificate(cred_dict))
        if cred_path:
            return firebase_admin.initialize_app(credentials.Certificate(cred_path))
    except Exception as e:
        logger.debug("Firebase not configured or init failed: %s", e)
    return None


def _send_push_impl(user_id, title, body, link_url=None):
    """Send FCM messages to all push tokens for user_id. Remove invalid tokens."""
    from firebase_admin import messaging
    tokens = PushToken.query.filter_by(user_id=user_id).all()
    if not tokens:
        return
    app = _get_firebase_app()
    if not app:
        return
    for pt in tokens:
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={"url": link_url or "", "category": "general"},
                token=pt.token,
                android=messaging.AndroidConfig(priority='high'),
                apns=messaging.APNSConfig(headers={'apns-priority': '10'}, payload=messaging.APNSPayload(aps=messaging.Aps(sound='default'))),
            )
            messaging.send(msg)
        except Exception as e:
            logger.warning("Push send failed for token %s: %s", pt.token[:20] + "...", e)
            err = str(e).lower()
            if 'unregistered' in err or 'invalid' in err or 'not found' in err or 'invalidargument' in err:
                try:
                    db.session.delete(pt)
                    db.session.commit()
                except Exception:
                    pass


def _enqueue_or_send_push(user_id, title, body, link_url=None):
    """Send push in a background thread so the request returns immediately."""
    app = current_app._get_current_object()
    threading.Thread(
        target=_send_push_background,
        args=(app, user_id, title, body, link_url),
        daemon=True,
    ).start()


def _send_push_background(app, user_id, title, body, link_url=None):
    with app.app_context():
        _send_push_impl(user_id, title, body, link_url)


def send_notification_email(user, title, body):
    """Legacy: email only, no in-app record. Uses queue or thread."""
    if not user.email:
        return
    _enqueue_or_send_email(str(user.email), title, body, 'general', None)
