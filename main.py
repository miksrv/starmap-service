"""Entry point for starmap-service.

Loads astronomical data once and serves chart-render requests over MQTT.
See ROADMAP.md for the full design and CLAUDE.md for the concept.
"""

from src.service import main

if __name__ == "__main__":
    main()
