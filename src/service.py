"""MQTT service: receives render requests from the bot, returns charts.

Lifecycle:
  - on connect: subscribe to the command topic, publish retained "online".
  - on command: validate, enqueue, and acknowledge ("queued"); a single worker
    thread renders requests one at a time (FIFO) and publishes each result.
  - LWT: the broker publishes "offline" if the service dies unexpectedly.
  - on clean shutdown (SIGINT/SIGTERM): publish "offline", then disconnect.
"""

import base64
import json
import logging
import queue
import signal
import threading

import paho.mqtt.client as mqtt

from src import config, storage
from src.errors import ValidationError
from src.renderer import Renderer
from src.request import parse_command

logger = logging.getLogger(__name__)


class StarmapService:
    def __init__(self):
        self.renderer = Renderer()
        # matplotlib is not thread-safe and the Pi cannot handle parallel
        # renders, so a single worker thread renders one request at a time.
        # Extra requests wait here (FIFO). maxsize<=0 means unbounded.
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=max(0, config.QUEUE_MAX_SIZE))
        self._stop = threading.Event()
        # 1 while the worker is mid-render, else 0; counted into the queue
        # "position" so it reflects the in-flight job, not just those waiting.
        self._active = 0
        self._worker = threading.Thread(target=self._worker_loop, name="render-worker", daemon=True)

        self.client = mqtt.Client(client_id=config.MQTT_CLIENT_ID)
        # Last Will: broker sends this if the connection drops unexpectedly.
        self.client.will_set(
            config.TOPICS["status"],
            json.dumps({"status": "offline"}),
            qos=1,
            retain=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # ------------------------------------------------------------------ MQTT
    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            logger.error("Failed to connect to MQTT broker, rc=%s", rc)
            return
        logger.info("Connected to MQTT broker %s:%s", config.MQTT_BROKER, config.MQTT_PORT)
        client.subscribe(config.TOPICS["command"], qos=1)
        self._publish_status("online")

    def _on_message(self, client, userdata, msg):
        # Parse + enqueue off the network loop so keepalive pings are never
        # blocked. The actual render happens later, in the worker thread.
        threading.Thread(target=self._handle_command, args=(msg.payload,), daemon=True).start()

    # ------------------------------------------------------ intake (enqueue)
    def _handle_command(self, payload: bytes):
        """Validate a command and put it on the render queue.

        Runs on a short-lived intake thread (not the worker): it never renders,
        so it returns quickly and the queue keeps accepting requests.
        """
        # A command we cannot route (bad JSON / no request_id) is logged and
        # dropped — there is nowhere to send a reply.
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Received non-JSON command, cannot route")
            return
        if not isinstance(data, dict):
            logger.warning("Command is not a JSON object, ignoring")
            return

        request_id = str(data.get("request_id", "")).strip()
        if not request_id:
            logger.warning("Command without request_id, ignoring")
            return

        # Validate. Any failure becomes a structured error reply — never a crash.
        try:
            request = parse_command(data)
        except ValidationError as e:
            self._reply_error(request_id, str(e))
            return
        except Exception:
            logger.exception("Unexpected error parsing command %s", request_id)
            self._reply_error(request_id, "invalid request")
            return

        # Enqueue. If the line is full, reject instead of blocking the bot.
        try:
            self._queue.put_nowait((request_id, request))
        except queue.Full:
            self._reply_error(request_id, "queue full, try again later")
            return

        # Acknowledge: "accepted, please wait". `position` is the approximate
        # number of renders that must finish before this one starts: requests
        # waiting ahead in the queue (qsize includes the one we just added) plus
        # the in-flight render, if any. Best-effort hint; 0 = starts right away.
        position = max(0, self._queue.qsize() - 1) + self._active
        self._reply_queued(request_id, position)

    # ------------------------------------------------------- worker (render)
    def _worker_loop(self):
        """Render queued requests one at a time, in order, until stopped."""
        while not self._stop.is_set():
            try:
                request_id, request = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._active = 1
            try:
                self._render_and_reply(request_id, request)
            finally:
                self._active = 0
                self._queue.task_done()

    def _render_and_reply(self, request_id: str, request):
        try:
            image = self.renderer.render(request)
            self._reply_ok(request_id, image)
        except (NotImplementedError, ValidationError) as e:
            # NotImplementedError: a not-yet-supported map type.
            # ValidationError: a render-time, user-facing problem (e.g. the
            # optic target is below the horizon, or the field of view is too big).
            self._reply_error(request_id, str(e))
        except Exception:  # never let a render crash the service
            logger.exception("Render failed for request %s", request_id)
            self._reply_error(request_id, "internal render error")

    def _reply_ok(self, request_id: str, image_bytes: bytes):
        message = {"request_id": request_id, "status": "ok"}
        if config.OUTPUT_MODE == "file":
            path = storage.save_chart(image_bytes, request_id)
            message["image_path"] = str(path)
            logger.info("Result for %s written to %s", request_id, path)
        else:
            message["image_base64"] = base64.b64encode(image_bytes).decode("ascii")
            logger.info("Result for %s sent inline (%d bytes)", request_id, len(image_bytes))
        self.client.publish(config.TOPICS["result"], json.dumps(message), qos=1)

    def _reply_queued(self, request_id: str, position: int):
        logger.info("Request %s queued (position %d)", request_id, position)
        self.client.publish(
            config.TOPICS["result"],
            json.dumps({"request_id": request_id, "status": "queued", "position": position}),
            qos=1,
        )

    def _reply_error(self, request_id: str, error: str):
        logger.warning("Replying error for %s: %s", request_id, error)
        self.client.publish(
            config.TOPICS["result"],
            json.dumps({"request_id": request_id, "status": "error", "error": error}),
            qos=1,
        )

    def _publish_status(self, status: str):
        info = self.client.publish(
            config.TOPICS["status"],
            json.dumps({"status": status}),
            qos=1,
            retain=True,
        )
        logger.info("Published status: %s", status)
        return info

    # ------------------------------------------------------------- lifecycle
    def run(self):
        self._worker.start()  # render worker; drains the queue one at a time
        self.client.connect(config.MQTT_BROKER, config.MQTT_PORT, config.MQTT_KEEPALIVE)
        self.client.loop_start()  # network loop in a background thread
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._request_stop)
        logger.info(
            "starmap-service started; waiting for commands on '%s'",
            config.TOPICS["command"],
        )
        self._stop.wait()

        # The worker stops after its current render; anything still queued is
        # dropped (the bot will see "offline" / time out and can retry).
        pending = self._queue.qsize()
        if pending:
            logger.warning("Shutting down with %d request(s) still queued", pending)
        self._worker.join(timeout=2.0)

        # Graceful shutdown: announce offline and try to make sure it is
        # delivered before tearing down the connection. Best-effort: if the
        # client is mid-reconnect, the publish may fail — that's fine, the
        # broker's retained LWT still marks us offline.
        try:
            info = self._publish_status("offline")
            info.wait_for_publish(timeout=2.0)
        except (RuntimeError, ValueError) as e:
            logger.warning("Could not publish offline status on shutdown: %s", e)
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("starmap-service stopped")

    def _request_stop(self, signum, frame):
        logger.info("Received signal %s, shutting down", signum)
        self._stop.set()


def main():
    logging.basicConfig(
        level=getattr(logging, str(config.LOG_LEVEL).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    StarmapService().run()
