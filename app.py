from flask import Flask, render_template, jsonify
from config import load_config

app = Flask(__name__, template_folder="templates", static_folder="static")
config = load_config()

@app.route("/")
def index():
    return render_template("index.html", config=config)

@app.route("/api/config")
def api_config():
    return jsonify(config)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
