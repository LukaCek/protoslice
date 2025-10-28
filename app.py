from flask import Flask, redirect
from versions.v0_1.routes import app as v0_1

app = Flask(__name__)

# ✅ Register versioned routes
app.register_blueprint(v0_1, url_prefix="/v0.1")

@app.route("/")
def index():
    return redirect("/v0.1/")

@app.errorhandler(404)
def page_not_found(e):
    return "API version not found! Check for API version.", 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5252)
