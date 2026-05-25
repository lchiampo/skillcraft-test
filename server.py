import os
import sqlite3
import uuid
from flask import Flask, request, jsonify, g, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)
DB_PATH = "skillcraft.db"
UPLOAD_DIR = "uploads"
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}
FILE_LINK_BASE = "https://s3.placeholder.com/songs/"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                artist     TEXT NOT NULL,
                duration   INTEGER,
                project    TEXT NOT NULL DEFAULT 'my projects',
                file_path  TEXT,
                file_link  TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add file_path column if upgrading from a previous version
        try:
            db.execute("ALTER TABLE songs ADD COLUMN file_path TEXT")
        except sqlite3.OperationalError:
            pass
        # Add file_link column if upgrading from a previous version
        try:
            db.execute("ALTER TABLE songs ADD COLUMN file_link TEXT")
        except sqlite3.OperationalError:
            pass
        db.execute(
            "UPDATE songs SET file_link = ? || id || '.mp3' WHERE file_link IS NULL",
            (FILE_LINK_BASE,),
        )
        db.commit()


@app.route("/api/songs", methods=["GET"])
def list_songs():
    project = request.args.get("project", "my projects")
    rows = get_db().execute(
        "SELECT * FROM songs WHERE project = ? ORDER BY created_at DESC", (project,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/songs", methods=["POST"])
def create_song():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    artist = data.get("artist", "").strip()
    duration = data.get("duration")
    project = data.get("project", "my projects").strip()

    if not title or not artist:
        return jsonify({"error": "title and artist are required"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO songs (title, artist, duration, project) VALUES (?, ?, ?, ?)",
        (title, artist, duration, project),
    )
    db.execute(
        "UPDATE songs SET file_link=? WHERE id=?",
        (f"{FILE_LINK_BASE}{cur.lastrowid}.mp3", cur.lastrowid),
    )
    db.commit()
    row = db.execute("SELECT * FROM songs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/songs/<int:song_id>", methods=["GET"])
def get_song(song_id):
    row = get_db().execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.route("/api/songs/<int:song_id>", methods=["PUT"])
def update_song(song_id):
    db = get_db()
    row = db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True)
    title = data.get("title", row["title"]).strip()
    artist = data.get("artist", row["artist"]).strip()
    duration = data.get("duration", row["duration"])
    project = data.get("project", row["project"]).strip()

    db.execute(
        "UPDATE songs SET title=?, artist=?, duration=?, project=? WHERE id=?",
        (title, artist, duration, project, song_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    return jsonify(dict(updated))


@app.route("/api/songs/<int:song_id>", methods=["DELETE"])
def delete_song(song_id):
    db = get_db()
    row = db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    if row["file_path"] and os.path.exists(row["file_path"]):
        os.remove(row["file_path"])

    db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    db.commit()
    return "", 204


@app.route("/api/songs/<int:song_id>/upload", methods=["POST"])
def upload_file(song_id):
    db = get_db()
    row = db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "no file provided"}), 400

    f = request.files["file"]
    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"unsupported file type; allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    # Remove old file if replacing
    if row["file_path"] and os.path.exists(row["file_path"]):
        os.remove(row["file_path"])

    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(UPLOAD_DIR, filename)
    f.save(save_path)

    db.execute("UPDATE songs SET file_path=? WHERE id=?", (save_path, song_id))
    db.commit()
    updated = db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    return jsonify(dict(updated))


@app.route("/api/projects", methods=["GET"])
def list_projects():
    rows = get_db().execute(
        "SELECT DISTINCT project FROM songs ORDER BY project"
    ).fetchall()
    return jsonify([r["project"] for r in rows])


@app.route("/api/songs/<int:song_id>/file", methods=["GET"])
def download_file(song_id):
    row = get_db().execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    if not row["file_path"] or not os.path.exists(row["file_path"]):
        return jsonify({"error": "no file attached"}), 404

    ext = os.path.splitext(row["file_path"])[1]
    download_name = secure_filename(f"{row['title']} - {row['artist']}{ext}")

    return send_from_directory(
        os.path.abspath(UPLOAD_DIR),
        os.path.basename(row["file_path"]),
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
