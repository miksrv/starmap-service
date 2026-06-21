#!/usr/bin/env python3
"""Test client for the starmap MQTT API.

Publishes a request to `starmap/command`, waits for the matching reply on
`starmap/result`, and saves the returned chart to a PNG file (decoding base64,
or reporting the path when the service runs in file mode).

Examples
--------
From the host (broker port is published by docker-compose):

    python scripts/send_request.py --map-type full --lat 55.75 --lon 37.62 --out chart.png

Inside the running container (talks to the compose broker, writes to mounted output/):

    docker compose exec starmap \
        python scripts/send_request.py --broker mosquitto --map-type full --out output/chart.png
"""
import argparse
import base64
import json
import sys
import time
import uuid

import paho.mqtt.client as mqtt

COMMAND_TOPIC = "starmap/command"
RESULT_TOPIC = "starmap/result"
STATUS_TOPIC = "starmap/status"


def build_command(args) -> dict:
    command = {
        "request_id": uuid.uuid4().hex[:12],
        "map_type": args.map_type,
    }
    observer = {}
    if args.lat is not None:
        observer["lat"] = args.lat
    if args.lon is not None:
        observer["lon"] = args.lon
    if args.datetime:
        observer["datetime"] = args.datetime
    if observer:
        command["observer"] = observer

    # Target (optic, or to center a chart): object name or ra/dec.
    target = {}
    if args.object:
        target["object"] = args.object
    if args.ra is not None:
        target["ra"] = args.ra
    if args.dec is not None:
        target["dec"] = args.dec
    if target:
        command["target"] = target

    # Optic definition, e.g. --optic-type binoculars -O magnification=10 -O fov=65
    if args.optic_type:
        optic = {"type": args.optic_type}
        for pair in args.optic_param or []:
            key, _, value = pair.partition("=")
            try:
                optic[key] = float(value)
            except ValueError:
                optic[key] = value
        command["optic"] = optic

    options = {}
    if args.direction:
        options["direction"] = args.direction
    if options:
        command["options"] = options
    return command


def main():
    ap = argparse.ArgumentParser(description="Send a starmap/command and save the result.")
    ap.add_argument("--broker", default="localhost", help="MQTT broker host (default: localhost)")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--map-type", default="full", help="full | zenith | horizon | optic | galactic")
    ap.add_argument("--lat", type=float, default=55.75)
    ap.add_argument("--lon", type=float, default=37.62)
    ap.add_argument("--datetime", default=None, help="ISO 8601 observer time (default: server 'now')")
    ap.add_argument("--object", default=None, help="target object name (not yet supported for optic)")
    ap.add_argument("--ra", type=float, default=None, help="target right ascension in degrees (optic)")
    ap.add_argument("--dec", type=float, default=None, help="target declination in degrees (optic)")
    ap.add_argument("--direction", default=None, help="horizon facing: N/NE/E/SE/S/SW/W/NW")
    ap.add_argument("--optic-type", default=None, help="binoculars | telescope | refractor | reflector | camera")
    ap.add_argument(
        "-O",
        "--optic-param",
        action="append",
        metavar="KEY=VALUE",
        help="optic parameter, repeatable, e.g. -O magnification=10 -O fov=65",
    )
    ap.add_argument("--out", default="chart.png", help="where to save the returned PNG")
    ap.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for a reply")
    args = ap.parse_args()

    command = build_command(args)
    request_id = command["request_id"]
    result = {}

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            print(f"Failed to connect to broker, rc={rc}", file=sys.stderr)
            return
        client.subscribe(RESULT_TOPIC, qos=1)
        client.subscribe(STATUS_TOPIC, qos=1)
        print(f"Connected to {args.broker}:{args.port}. Sending request {request_id}:")
        print(f"  {json.dumps(command)}")
        client.publish(COMMAND_TOPIC, json.dumps(command), qos=1)

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.topic == STATUS_TOPIC:
            print(f"[status] {data}")
            return
        if str(data.get("request_id")) != request_id:
            return  # a reply to someone else's request
        result["data"] = data

    client = mqtt.Client(client_id=f"starmap-test-{request_id}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port, 60)
    client.loop_start()

    deadline = time.time() + args.timeout
    while "data" not in result and time.time() < deadline:
        time.sleep(0.2)

    client.loop_stop()
    client.disconnect()

    if "data" not in result:
        print(f"Timed out after {args.timeout:.0f}s waiting for a reply.", file=sys.stderr)
        sys.exit(1)

    data = result["data"]
    if data.get("status") != "ok":
        print(f"Service returned error: {data.get('error')}", file=sys.stderr)
        sys.exit(2)

    if "image_base64" in data:
        with open(args.out, "wb") as f:
            f.write(base64.b64decode(data["image_base64"]))
        print(f"OK — chart saved to {args.out}")
    elif "image_path" in data:
        print(f"OK — service wrote the chart to {data['image_path']}")
    else:
        print("Reply was 'ok' but contained no image.", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
