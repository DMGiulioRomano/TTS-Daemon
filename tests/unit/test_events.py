"""EventBus: delivery, unsubscribe, and handler isolation."""

from __future__ import annotations

from tts_gateway.core.events import Event, EventBus


def test_publish_delivers_payload_and_timestamp() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(seen.append)
    event = bus.publish("utterance.queued", {"id": "abc"})
    assert seen == [event]
    assert event.type == "utterance.queued"
    assert event.data == {"id": "abc"}
    assert event.timestamp > 0
    assert event.to_dict()["type"] == "utterance.queued"


def test_publish_with_no_data_defaults_to_empty_dict() -> None:
    bus = EventBus()
    assert bus.publish("queue.cleared").data == {}


def test_unsubscribe_stops_delivery_and_is_idempotent() -> None:
    bus = EventBus()
    seen: list[Event] = []
    unsubscribe = bus.subscribe(seen.append)
    bus.publish("one")
    unsubscribe()
    unsubscribe()  # second call must not raise
    bus.publish("two")
    assert [event.type for event in seen] == ["one"]
    assert bus.subscriber_count == 0


def test_failing_handler_does_not_break_others() -> None:
    bus = EventBus()
    seen: list[str] = []

    def bad_handler(event: Event) -> None:
        raise RuntimeError("observer bug")

    bus.subscribe(bad_handler)
    bus.subscribe(lambda event: seen.append(event.type))
    bus.publish("still.delivered")
    assert seen == ["still.delivered"]


def test_multiple_subscribers_all_receive() -> None:
    bus = EventBus()
    counts = [0, 0]
    bus.subscribe(lambda _: counts.__setitem__(0, counts[0] + 1))
    bus.subscribe(lambda _: counts.__setitem__(1, counts[1] + 1))
    bus.publish("x")
    assert counts == [1, 1]
