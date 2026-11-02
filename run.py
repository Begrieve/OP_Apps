"""Start the Insurance Broker Tracker: python run.py, then open http://127.0.0.1:5000"""
import os

from insurance_tracker.app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Insurance Tracker running at http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
