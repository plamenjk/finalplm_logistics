from app import create_app

app = create_app()

if __name__ == "__main__":
    # dev сървър (debug) – 1:1 със стария app.py
    app.run(debug=True, host="127.0.0.1", port=5000)
