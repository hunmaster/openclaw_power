"""CommentBoost 랜딩 페이지 서버."""
import os
from flask import Flask, render_template, send_from_directory

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/guide")
def guide():
    return render_template("guide.html")


@app.route("/download/<path:filename>")
def download(filename):
    """다운로드 파일 서빙 (releases/ 디렉토리)."""
    releases_dir = os.path.join(os.path.dirname(__file__), "releases")
    return send_from_directory(releases_dir, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
