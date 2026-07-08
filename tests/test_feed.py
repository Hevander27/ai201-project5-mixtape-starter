"""
tests/test_feed.py — Mixtape

Regression tests for the "Friends Listening Now" feed.

These tests would have caught Issue #2 ("Friends Listening Now shows people
from yesterday"): before the fix, the feed used a rolling 24-hour window, so
a friend's listen from last night stayed visible the next morning. The feed
should only include friends who listened during the current calendar day.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """Two friends and a song to listen to."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([me, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=me.id))

        song = Song(title="Neon City", artist="Skyline", shared_by=me.id)
        db.session.add(song)
        db.session.commit()
        yield {"me": me, "friend": friend, "song": song}


def _add_event(user_id, song_id, listened_at):
    db.session.add(
        ListeningEvent(user_id=user_id, song_id=song_id, listened_at=listened_at)
    )
    db.session.commit()


def test_listen_from_last_night_is_excluded(app, seed):
    """A friend whose only listen was late yesterday should NOT appear today."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        # 11pm "yesterday": before the start of today, but well within 24h of a
        # morning fetch — exactly the case that leaked through the old window.
        start_of_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        last_night = start_of_today - timedelta(hours=1)
        _add_event(seed["friend"].id, seed["song"].id, last_night)

        names = [e["friend"]["username"] for e in get_friends_listening_now(seed["me"].id)]
        assert "friend" not in names  # Bug caused this to be present


def test_listen_earlier_today_is_included(app, seed):
    """A friend who listened earlier today SHOULD still appear."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        start_of_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        _add_event(seed["friend"].id, seed["song"].id, start_of_today + timedelta(minutes=1))

        names = [e["friend"]["username"] for e in get_friends_listening_now(seed["me"].id)]
        assert "friend" in names
