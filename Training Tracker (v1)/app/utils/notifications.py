import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import current_app, render_template

from app import db
from app.models import Notification


def notify(user, title, body, category='general', link_url=None, email_only=False):
    """
    Central notification dispatcher.

    - Creates an in-app Notification record (unless user disabled or email_only)
    - Sends an HTML email (unless user disabled)
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

    # Email notification
    if wants_email and user.email:
        _send_html_email(user, title, body, category, link_url)


def _send_html_email(user, title, body, category='general', link_url=None):
    """Send a branded HTML email with plain-text fallback."""
    print(f"\n--- EMAIL OUTBOUND ---")
    print(f"To: {user.email}")
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
    msg['To'] = user.email

    msg.attach(MIMEText(body, 'plain'))
    if html_content:
        msg.attach(MIMEText(html_content, 'html'))

    try:
        with smtplib.SMTP_SSL(
            current_app.config['MAIL_SERVER'],
            current_app.config['MAIL_PORT']
        ) as server:
            server.login(
                current_app.config['MAIL_USERNAME'],
                current_app.config['MAIL_PASSWORD']
            )
            server.send_message(msg)
        print(f"SMTP Success: Email sent to {user.email}")
    except Exception as e:
        print(f"SMTP Failure: {str(e)}")


def send_notification_email(user, title, body):
    """Legacy wrapper -- delegates to notify() with email_only=True, no in-app record."""
    _send_html_email(user, title, body, category='general', link_url=None)
