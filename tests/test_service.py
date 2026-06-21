"""Tests for src.service.StarmapService — intake, queueing and replies.

The MQTT client is replaced with a recorder and the Renderer with a fake, so no
broker connection or real rendering happens.
"""

import json
import queue

import pytest

from src import config, service
from src.errors import ValidationError


class FakeClient:
    """Records publishes/subscribes instead of touching a broker."""

    def __init__(self):
        self.published = []  # list of dicts: topic, payload(parsed), qos, retain
        self.subscribed = []

    def will_set(self, *args, **kwargs):
        pass

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": json.loads(payload), "qos": qos, "retain": retain})
        return _PubInfo()

    def by_status(self, status):
        return [p for p in self.published if p["payload"].get("status") == status]


class _PubInfo:
    def wait_for_publish(self, timeout=None):
        pass


class FakeRenderer:
    def __init__(self, image=b"PNGBYTES", exc=None):
        self.image = image
        self.exc = exc
        self.calls = []

    def render(self, request):
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        return self.image


@pytest.fixture
def svc(monkeypatch):
    """A service wired with a fake renderer and a fake client."""
    monkeypatch.setattr(service, "Renderer", lambda: FakeRenderer())
    monkeypatch.setattr(config, "QUEUE_MAX_SIZE", 5)
    s = service.StarmapService()
    s.client = FakeClient()
    return s


def _results(svc, status):
    return [p for p in svc.client.published if p["payload"].get("status") == status]


# ---------------------------------------------------------------------------
# Intake: _handle_command
# ---------------------------------------------------------------------------
def test_valid_command_enqueues_and_acks_queued(svc):
    payload = json.dumps({"request_id": "r1", "map_type": "full"}).encode()
    svc._handle_command(payload)
    assert svc._queue.qsize() == 1
    queued = _results(svc, "queued")
    assert len(queued) == 1
    assert queued[0]["payload"]["request_id"] == "r1"
    assert queued[0]["payload"]["position"] == 0


def test_queued_position_counts_waiting(svc):
    for i in range(3):
        svc._handle_command(json.dumps({"request_id": f"r{i}", "map_type": "full"}).encode())
    positions = [p["payload"]["position"] for p in _results(svc, "queued")]
    assert positions == [0, 1, 2]


def test_invalid_json_is_dropped(svc):
    svc._handle_command(b"not json {")
    assert svc._queue.qsize() == 0
    assert svc.client.published == []


def test_non_object_json_is_dropped(svc):
    svc._handle_command(json.dumps([1, 2, 3]).encode())
    assert svc._queue.qsize() == 0
    assert svc.client.published == []


def test_missing_request_id_is_dropped(svc):
    svc._handle_command(json.dumps({"map_type": "full"}).encode())
    assert svc._queue.qsize() == 0
    assert svc.client.published == []


def test_blank_request_id_is_dropped(svc):
    svc._handle_command(json.dumps({"request_id": "   ", "map_type": "full"}).encode())
    assert svc._queue.qsize() == 0
    assert svc.client.published == []


def test_validation_error_replies_error(svc):
    svc._handle_command(json.dumps({"request_id": "r1", "map_type": "nope"}).encode())
    errors = _results(svc, "error")
    assert len(errors) == 1
    assert "unknown map_type" in errors[0]["payload"]["error"]
    assert svc._queue.qsize() == 0


def test_queue_full_rejected(svc, monkeypatch):
    # Shrink the queue to capacity 1 and pre-fill it.
    svc._queue = queue.Queue(maxsize=1)
    svc._handle_command(json.dumps({"request_id": "a", "map_type": "full"}).encode())
    svc._handle_command(json.dumps({"request_id": "b", "map_type": "full"}).encode())
    errors = _results(svc, "error")
    assert any("queue full" in e["payload"]["error"] for e in errors)


# ---------------------------------------------------------------------------
# Worker: _render_and_reply
# ---------------------------------------------------------------------------
def _request(map_type="full"):
    from src.request import parse_command

    return parse_command({"request_id": "rid", "map_type": map_type})


def test_render_success_base64_reply(svc, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_MODE", "base64")
    svc.renderer = FakeRenderer(image=b"HELLO")
    svc._render_and_reply("rid", _request())
    ok = _results(svc, "ok")
    assert len(ok) == 1
    import base64

    assert base64.b64decode(ok[0]["payload"]["image_base64"]) == b"HELLO"


def test_render_success_file_reply(svc, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_MODE", "file")
    monkeypatch.setattr(service.storage, "save_chart", lambda image, rid: f"/out/{rid}.png")
    svc.renderer = FakeRenderer(image=b"HELLO")
    svc._render_and_reply("rid", _request())
    ok = _results(svc, "ok")
    assert ok[0]["payload"]["image_path"] == "/out/rid.png"
    assert "image_base64" not in ok[0]["payload"]


def test_render_not_implemented_replies_error(svc):
    svc.renderer = FakeRenderer(exc=NotImplementedError("zenith not ready"))
    svc._render_and_reply("rid", _request())
    errors = _results(svc, "error")
    assert errors[0]["payload"]["error"] == "zenith not ready"


def test_render_validation_error_passes_message(svc):
    svc.renderer = FakeRenderer(exc=ValidationError("target below horizon"))
    svc._render_and_reply("rid", _request())
    errors = _results(svc, "error")
    assert errors[0]["payload"]["error"] == "target below horizon"


def test_render_unexpected_error_is_masked(svc):
    svc.renderer = FakeRenderer(exc=RuntimeError("boom: secret stacktrace"))
    svc._render_and_reply("rid", _request())
    errors = _results(svc, "error")
    assert errors[0]["payload"]["error"] == "internal render error"
    assert "secret" not in errors[0]["payload"]["error"]


# ---------------------------------------------------------------------------
# Connection & status
# ---------------------------------------------------------------------------
def test_on_connect_subscribes_and_announces_online(svc):
    svc._on_connect(svc.client, None, None, 0)
    assert svc.client.subscribed == [(config.TOPICS["command"], 1)]
    online = svc.client.by_status("online")
    assert len(online) == 1
    assert online[0]["retain"] is True


def test_on_connect_failure_does_not_subscribe(svc):
    svc._on_connect(svc.client, None, None, 1)
    assert svc.client.subscribed == []
    assert svc.client.published == []


def test_publish_status_is_retained(svc):
    svc._publish_status("offline")
    offline = svc.client.by_status("offline")
    assert offline[0]["retain"] is True
    assert offline[0]["topic"] == config.TOPICS["status"]


def test_replies_go_to_result_topic(svc):
    svc._reply_error("rid", "oops")
    assert svc.client.published[0]["topic"] == config.TOPICS["result"]
