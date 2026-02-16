from app import create_app, db

app = create_app()

if __name__ == "__main__":
    # This block ensures the database tables are created before the app starts
    with app.app_context():
        db.create_all()
        print("Database tables verified/created.")

    # Added host='0.0.0.0' to allow network access
    app.run(host='0.0.0.0', port=5000, debug=True)