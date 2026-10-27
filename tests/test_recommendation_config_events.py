from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import psycopg2
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy

import recommendation_config_events as events


def watcher():
    application = QApplication.instance() or QApplication([])
    return application, events.RecommendationConfigEvents(
        "host=localhost user=synthetic"
    )


def test_receive_ignores_timeouts_and_other_channels(monkeypatch):
    con = SimpleNamespace(notifies=[SimpleNamespace(channel="other")], poll=Mock())
    monkeypatch.setattr(events.select, "select", lambda *_: ([], [], []))
    assert not events.receive_changes(con, 0)
    con.poll.assert_not_called()
    monkeypatch.setattr(events.select, "select", lambda *_: ([con], [], []))
    assert not events.receive_changes(con, 0)
    assert con.notifies == []


def test_listener_closes_connection_and_emits_only_after_listen_or_change(monkeypatch):
    application, worker = watcher()
    connection = MagicMock()
    monkeypatch.setattr(events.psycopg2, "connect", lambda **_: connection)

    def receive(_connection):
        worker.stop()
        return True

    monkeypatch.setattr(events, "receive_changes", receive)
    observed = QSignalSpy(worker.changed)
    worker._listen()
    assert observed.count() == 2
    assert connection.autocommit is True
    connection.close.assert_called_once()
    assert worker.shutdown()


def test_listener_failure_closes_connection(monkeypatch):
    application, worker = watcher()
    connection = Mock()
    connection.cursor.side_effect = psycopg2.OperationalError("synthetic")
    monkeypatch.setattr(events.psycopg2, "connect", lambda **_: connection)
    with pytest.raises(psycopg2.Error):
        worker._listen()
    connection.close.assert_called_once()


def test_connection_failure_uses_bounded_backoff_without_blocking_gui(monkeypatch):
    application, worker = watcher()
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.side_effect = [False, True]
    worker.stopping = stop
    monkeypatch.setattr(
        worker, "_listen", Mock(side_effect=psycopg2.OperationalError("synthetic"))
    )
    worker.run()
    assert [call.args[0] for call in stop.wait.call_args_list] == [5, 10]


def test_stopped_listener_does_not_connect(monkeypatch):
    application, worker = watcher()
    connect = Mock()
    monkeypatch.setattr(events.psycopg2, "connect", connect)
    worker.stop()
    worker.start()
    assert worker.wait(1000)
    application.processEvents()
    connect.assert_not_called()
    assert worker not in events._RUNNING_EVENTS


def test_successful_session_can_end_normally_without_reconnect(monkeypatch):
    application, worker = watcher()
    listen = Mock(side_effect=worker.stop)
    monkeypatch.setattr(worker, "_listen", listen)
    worker.run()
    listen.assert_called_once()


def test_idle_session_then_change_emits_one_refresh(monkeypatch):
    application, worker = watcher()
    connection = MagicMock()
    monkeypatch.setattr(events.psycopg2, "connect", lambda **_: connection)
    count = 0

    def receive(_con):
        nonlocal count
        count += 1
        if count == 2:
            worker.stop()
            return True
        return False

    monkeypatch.setattr(events, "receive_changes", receive)
    observed = QSignalSpy(worker.changed)
    worker._listen()
    assert count == 2 and observed.count() == 2


def test_listener_can_be_constructed_before_the_application(monkeypatch):
    monkeypatch.setattr(events.QApplication, "instance", lambda: None)
    worker = events.RecommendationConfigEvents("host=localhost user=synthetic")
    worker.stop()
    assert worker.stopping.is_set()


def test_explicit_ssl_verification_is_preserved():
    parameters = events.listener_parameters(
        "host=x.pooler.supabase.com port=6543 user=synthetic sslmode=verify-full"
    )
    assert parameters["sslmode"] == "verify-full"
