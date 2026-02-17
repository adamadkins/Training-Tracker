"""
Run once per deploy to ensure tables exist and empty DBs are seeded.
Use in Railway start command: flask db upgrade && python init_db.py && exec gunicorn ...
So only one process runs init; then Gunicorn starts (no init in workers).
"""
from app import create_app, db
from app.seed import seed_if_empty


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print("Database tables verified/created.")
    seed_if_empty(app)
    print("Init complete.")


if __name__ == "__main__":
    main()
