import os

from aflatoon import create_app

app = create_app()

if __name__ == "__main__":
    # Never debug in production: the debugger console can execute code.
    # Locally this stays on unless FLASK_DEBUG=0 is set.
    app.run(host="0.0.0.0", port=5000,
            debug=os.environ.get("FLASK_DEBUG", "1") == "1")