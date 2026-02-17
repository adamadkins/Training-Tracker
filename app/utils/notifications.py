import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import current_app, render_template

from app import db
from app.models import Notification


def notify(user, title, body, category='general', link_url=None, email_only=False):
    """
    Central notification dispatcher.

    - Creates an in-app Notification record (unless user disabled or email_only)
    - Sends an HTML email in a background thread (unless user disabled) so the request doesn't block
    - Respects user.settings preferences for notify_in_app / notify_email
    """
    settings = getattr(user, 'settings', None)
    wants_in_app = getattr(settings, 'notify_in_app', True) if settings else True
    wants_email = getattr(settings, 'notify_email', True) if settings else True

    # In-app notification
    if wants_in_app and not email_only:
        db.session.add(Notification(
            user_id=user.id,
            title=title,
            body=body,
            category=category,
            link_url=link_url,
        ))

    # Email notification: send in background so we don't hit worker timeout
    if wants_email and user.email:
        app = current_app._get_current_object()
        to_email = str(user.email)
        thread = threading.Thread(
            target=_send_email_background,
            args=(app, to_email, title, body, category, link_url),
            daemon=True,
        )
        thread.start()


def _send_email_background(app, to_email, title, body, category='general', link_url=None):
    """Run in background thread with app context so we have config and templates."""
    with app.app_context():
        _send_html_email_impl(to_email, title, body, category, link_url)


def _send_html_email_impl(to_email, title, body, category='general', link_url=None):
    """Send a branded HTML email. Uses MAIL_TIMEOUT so SMTP doesn't hang the worker."""
    print(f"\n--- EMAIL OUTBOUND ---")
    print(f"To: {to_email}")
    print(f"Subject: {title}")
    print(f"Body: {body}")
    print(f"----------------------\n")

    try:
        html_content = render_template(
            'email/notification.html',
            title=title,
            body=body,
            category=category,
            link_url=link_url,
        )
    except Exception as e:
        print(f"Template render failed, falling back to plain text: {e}")
        html_content = None

    msg = MIMEMultipart('alternative')
    msg['Subject'] = title
    msg['From'] = current_app.config.get('MAIL_DEFAULT_SENDER')
    msg['To'] = to_email

    msg.attach(MIMEText(body, 'plain'))
    if html_content:
        msg.attach(MIMEText(html_content, 'html'))

    timeout = current_app.config.get('MAIL_TIMEOUT', 15)
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
        print(f"SMTP Success: Email sent to {to_email}")
    except Exception as e:
        print(f"SMTP Failure: {str(e)}")


def _send_html_email(user, title, body, category='general', link_url=None):
    """Send email synchronously (used by legacy send_notification_email)."""
    _send_html_email_impl(user.email, title, body, category, link_url)


def send_notification_email(user, title, body):
    """Legacy wrapper -- send email in background with email_only semantics (no in-app record)."""
    if not user.email:
        return
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_send_email_background,
        args=(app, str(user.email), title, body, 'general', None),
        daemon=True,
    )
    thread.start()
