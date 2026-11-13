"""Message bus abstraction (blueprint §4).

* `servicebus` - Azure Service Bus topics/subscriptions with Managed Identity (production).
* `http`       - local fan-out: POST the CloudEvent to each subscriber's /v1/bus/{topic} endpoint.
* `inproc`     - in-process dispatch used by the test-suite (all apps in one interpreter).

Handlers are registered per (topic, subscription) and must be idempotent (at-least-once delivery).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from .config import Settings
from .util import iso, new_id, utcnow

log = logging.getLogger("tfso.bus")

Handler = Callable[[dict[str, Any]], Awaitable[None]]


def cloud_event(event_type: str, source: str, subject: str | None, data: dict[str, Any],
                correlation_id: str | None = None, event_id: str | None = None) -> dict[str, Any]:
    return {
        "specversion": "1.0",
        "id": event_id or new_id(),
        "source": source,
        "type": event_type,
        "subject": subject,
        "time": iso(utcnow()),
        "datacontenttype": "application/json",
        "correlationid": correlation_id,
        "data": data,
    }


class Broker:
    """Base broker. `publish` must raise on failure so the outbox can retry."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.handlers: dict[str, list[tuple[str, Handler]]] = {}

    def subscribe(self, topic: str, subscription: str, handler: Handler) -> None:
        self.handlers.setdefault(topic, []).append((subscription, handler))

    async def publish(self, topic: str, envelope: dict[str, Any]) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def deliver_local(self, topic: str, envelope: dict[str, Any]) -> int:
        """Run the handlers this process registered for `topic` (used by HTTP bus endpoint)."""
        count = 0
        for _sub, handler in self.handlers.get(topic, []):
            await handler(envelope)
            count += 1
        return count

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class InProcBroker(Broker):
    """Shared, process-wide registry so separate FastAPI apps in one interpreter can talk (tests)."""
    registry: dict[str, list[tuple[str, Handler]]] = {}
    queue: list[tuple[str, dict]] = []

    def subscribe(self, topic: str, subscription: str, handler: Handler) -> None:
        subs = InProcBroker.registry.setdefault(topic, [])
        subs[:] = [s for s in subs if s[0] != subscription]
        subs.append((subscription, handler))
        super().subscribe(topic, subscription, handler)

    async def publish(self, topic: str, envelope: dict[str, Any]) -> None:
        InProcBroker.queue.append((topic, envelope))

    @classmethod
    async def drain(cls, max_rounds: int = 100) -> int:
        delivered = 0
        for _ in range(max_rounds):
            if not cls.queue:
                break
            topic, env = cls.queue.pop(0)
            for _sub, handler in list(cls.registry.get(topic, [])):
                await handler(json.loads(json.dumps(env)))
                delivered += 1
        return delivered

    @classmethod
    def reset(cls) -> None:
        cls.registry.clear()
        cls.queue.clear()


class HttpBroker(Broker):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.client = httpx.AsyncClient(timeout=15)

    async def publish(self, topic: str, envelope: dict[str, Any]) -> None:
        urls = self.settings.bus_http_routes.get(topic, [])
        headers = {"content-type": "application/json", "x-correlation-id": envelope.get("correlationid") or ""}
        if self.settings.api_keys:
            headers["x-api-key"] = self.settings.api_keys[0]
        for url in urls:
            resp = await self.client.post(url, json=envelope, headers=headers)
            if resp.status_code >= 300:
                raise RuntimeError(f"HTTP bus delivery to {url} failed: {resp.status_code} {resp.text[:200]}")

    async def stop(self) -> None:
        await self.client.aclose()


class ServiceBusBroker(Broker):
    """Azure Service Bus. Receivers run as background tasks; completion only after the handler succeeds,
    otherwise the message is abandoned and redelivered (max delivery count → dead-letter queue)."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._client = None
        self._tasks: list[asyncio.Task] = []
        self._senders: dict[str, Any] = {}

    def _get_client(self):
        if self._client is None:
            from azure.servicebus.aio import ServiceBusClient
            if self.settings.servicebus_connection:
                self._client = ServiceBusClient.from_connection_string(self.settings.servicebus_connection)
            else:
                from azure.identity.aio import DefaultAzureCredential
                self._client = ServiceBusClient(self.settings.servicebus_fqdn, DefaultAzureCredential())
        return self._client

    async def publish(self, topic: str, envelope: dict[str, Any]) -> None:
        from azure.servicebus import ServiceBusMessage
        sender = self._senders.get(topic)
        if sender is None:
            sender = self._get_client().get_topic_sender(topic_name=topic)
            self._senders[topic] = sender
        msg = ServiceBusMessage(
            json.dumps(envelope), content_type="application/cloudevents+json", message_id=envelope["id"],
            subject=envelope.get("type"), correlation_id=envelope.get("correlationid"),
            application_properties={"orderId": envelope.get("subject") or "", "type": envelope.get("type")},
        )
        await sender.send_messages(msg)

    async def start(self) -> None:
        for topic, subs in self.handlers.items():
            for subscription, handler in subs:
                self._tasks.append(asyncio.create_task(self._receive_loop(topic, subscription, handler)))

    async def _receive_loop(self, topic: str, subscription: str, handler: Handler) -> None:
        while True:
            try:
                receiver = self._get_client().get_subscription_receiver(topic_name=topic, subscription_name=subscription,
                                                                        max_wait_time=30)
                async with receiver:
                    async for msg in receiver:
                        try:
                            await handler(json.loads(str(msg)))
                            await receiver.complete_message(msg)
                        except Exception:  # noqa: BLE001
                            log.exception("handler failed topic=%s sub=%s id=%s", topic, subscription, msg.message_id)
                            await receiver.abandon_message(msg)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("service bus receiver crashed; restarting in 5s")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for s in self._senders.values():
            await s.close()
        if self._client:
            await self._client.close()


def make_broker(settings: Settings) -> Broker:
    if settings.bus_mode == "servicebus":
        return ServiceBusBroker(settings)
    if settings.bus_mode == "inproc":
        return InProcBroker(settings)
    return HttpBroker(settings)


class EventStream:
    """Optional Event Hubs stream for Databricks (order-status-stream). No-op when not configured."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._producer = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.eventhub_fqdn)

    async def send(self, envelopes: list[dict[str, Any]]) -> None:
        if not self.enabled or not envelopes:
            return
        from azure.eventhub import EventData
        if self._producer is None:
            from azure.eventhub.aio import EventHubProducerClient
            from azure.identity.aio import DefaultAzureCredential
            self._producer = EventHubProducerClient(self.settings.eventhub_fqdn, self.settings.eventhub_name,
                                                    DefaultAzureCredential())
        by_key: dict[str, list] = {}
        for env in envelopes:
            by_key.setdefault(env.get("subject") or "none", []).append(env)
        for key, items in by_key.items():
            batch = await self._producer.create_batch(partition_key=key)
            for env in items:
                batch.add(EventData(json.dumps(env)))
            await self._producer.send_batch(batch)

    async def close(self) -> None:
        if self._producer:
            await self._producer.close()
