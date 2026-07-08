"""
tests/test_notifications.py — Mixtape

Regression tests for notification creation.

These tests would have caught Issue #4 ("I got notified when a friend added
my song to a playlist but not when they rated it"): before the fix, rate_song
saved the rating but never created a notification for the song's sharer.
"""

import pytest
from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """A sharer who shared a song and a separate rater."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Neon City", artist="Skyline", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()
        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_a_song_notifies_the_sharer(app, seed):
    """Rating someone's shared song creates a notification for the sharer."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        rate_song(seed["rater"].id, seed["song"].id, 5)

        notifs = get_notifications(sharer_id)
        rating_notifs = [n for n in notifs if n["type"] == "song_rated"]
        assert len(rating_notifs) == 1  # Bug caused this to be 0
        assert "rated" in rating_notifs[0]["body"]


def test_rating_your_own_song_does_not_notify(app, seed):
    """A user rating their own shared song should not notify themselves."""
    with app.app_context():
        sharer = seed["sharer"]
        rate_song(sharer.id, seed["song"].id, 4)

        notifs = get_notifications(sharer.id)
        assert [n for n in notifs if n["type"] == "song_rated"] == []


def test_updating_a_rating_still_notifies(app, seed):
    """Changing an existing rating also produces a notification path."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        rate_song(seed["rater"].id, seed["song"].id, 3)
        rate_song(seed["rater"].id, seed["song"].id, 5)

        count = Notification.query.filter_by(
            user_id=sharer_id, notification_type="song_rated"
        ).count()
        assert count >= 1
