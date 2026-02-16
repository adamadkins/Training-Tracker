from flask_mail import Message
from flask import render_template, current_app, url_for
from app import mail


def send_email(to, subject, template, **kwargs):
    """Core function to send stylized emails."""
    msg = Message(
        subject=f"Training Tracker: {subject}",
        recipients=[to],
        sender=current_app.config['MAIL_DEFAULT_SENDER']
    )
    # We use both HTML (for styling) and Plain Text (for compatibility)
    msg.body = render_template(f"email/{template}.txt", **kwargs)
    msg.html = render_template(f"email/{template}.html", **kwargs)

    # In a real app, you'd use a background task here (like Celery)
    # For now, we'll send it directly
    mail.send(msg)


def send_notification_email(user, title, body):
    """Sends a copy of an in-app notification to the user's email."""
    send_email(
        to=user.email,
        subject=title,
        template="notification",
        user=user,
        title=title,
        body=body
    )