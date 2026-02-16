from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User
from app import db
# ADDED: Import your notification helper
from app.utils.notifications import send_notification_email

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/")
def home():
    if current_user.is_authenticated:
        if current_user.role == "manager":
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("employee.dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and user.password_hash is None:
            flash("Your account is not set up yet. Please use the link sent to your email.")
            return redirect(url_for("auth.login"))

        if not user or not user.check_password(password):
            flash("Invalid email or password.")
            return render_template("login.html"), 401

        login_user(user)

        if user.role == "manager":
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("employee.dashboard"))

    return render_template("login.html")


# --- New: Forgot Password Request ---

@auth_bp.route("/forgot_password", methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('auth.home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()

        # We show success regardless of whether the email exists for security
        if user:
            token = user.get_reset_token()
            # Note: Using 'auth.reset_token' to match your function name below
            reset_url = url_for('auth.reset_token', token=token, _external=True)

            title = "Password Reset Request"
            body = f"Click the link below to reset your password:\n\n{reset_url}\n\nIf you didn't request this, ignore this email."

            try:
                send_notification_email(user, title, body)
            except Exception as e:
                print(f"SMTP Error: {e}")

        flash("If an account exists with that email, a reset link has been sent.")
        return redirect(url_for('auth.login'))

    return render_template("auth/forgot_password.html")


# --- Account Setup / Reset via Token ---

@auth_bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('auth.home'))

    user = User.verify_reset_token(token)
    if user is None:
        flash('That is an invalid or expired token', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Passwords do not match.")
            return render_template("auth/reset_token.html", token=token)

        user.set_password(password)
        db.session.commit()

        flash('Your password has been updated! You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template("auth/reset_token.html", token=token)


@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change_password", methods=["POST"])
@login_required
def change_password():
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    if not new_password or new_password != confirm_password:
        flash("Passwords must match and cannot be empty.")
        return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))

    current_user.set_password(new_password)
    db.session.commit()

    flash("Your password has been updated successfully!")
    return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))