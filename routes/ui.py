"""routes/ui.py — Mixtape demo UI (NOT part of the graded API surface).

This blueprint exists only to exercise the app in a browser. It serves the
single-page dashboard at ``/`` and a read-only ``/api/bootstrap`` endpoint that
lists seeded users/songs/playlists so the UI can populate its dropdowns — the
core API intentionally has no list endpoints. None of the bug-fix logic lives
here; the core routes/services are unchanged.
"""

import os

from flask import Blueprint, jsonify, send_from_directory

from models import User, Song, Playlist

ui_bp = Blueprint("ui", __name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@ui_bp.route("/")
def index():
    return send_from_directory(_STATIC_DIR, "index.html")


@ui_bp.route("/api/bootstrap")
def bootstrap():
    """Read-only lists used to populate the dashboard's dropdowns."""
    users = [
        {"id": u.id, "username": u.username, "streak": u.listening_streak}
        for u in User.query.order_by(User.username).all()
    ]
    songs = [
        {"id": s.id, "title": s.title, "artist": s.artist, "shared_by": s.shared_by}
        for s in Song.query.order_by(Song.title).all()
    ]
    playlists = [
        {"id": p.id, "name": p.name}
        for p in Playlist.query.order_by(Playlist.name).all()
    ]
    return jsonify({"users": users, "songs": songs, "playlists": playlists})
