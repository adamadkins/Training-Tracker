from app import create_app, db
from app.models import User


def force_password():
    app = create_app()
    with app.app_context():
        # Change this to the email you used in the app
        email = "trainee@local"
        user = User.query.filter_by(email=email).first()

        if user:
            user.set_password("trainee123")
            db.session.commit()
            print(f"Success! Password for {email} is now: trainee123")
        else:
            print(f"Error: No user found with email {email}")


if __name__ == "__main__":
    force_password()
