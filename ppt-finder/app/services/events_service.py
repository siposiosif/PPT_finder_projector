import json
import queue
import threading
import time
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """Pub/Sub event bus for Server-Sent Events (SSE)."""
    
    def __init__(self):
        self.listeners = set()
        self.lock = threading.Lock()

    def subscribe(self):
        """Subscribe a new client listener queue."""
        q = queue.Queue(maxsize=50)
        with self.lock:
            self.listeners.add(q)
        return q

    def unsubscribe(self, q):
        """Unsubscribe a client queue."""
        with self.lock:
            self.listeners.discard(q)

    def publish(self, event_type, data):
        """Publish an event to all connected listeners."""
        payload = {
            "type": event_type,
            "data": data,
            "timestamp": time.time()
        }
        raw_msg = f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        with self.lock:
            dead_queues = []
            for q in self.listeners:
                try:
                    q.put_nowait(raw_msg)
                except queue.Full:
                    # Force a reconnect and complete snapshot instead of leaving
                    # a live socket silently detached from every future update.
                    try:
                        while True:
                            q.get_nowait()
                    except queue.Empty:
                        pass
                    q.put_nowait(None)
                    dead_queues.append(q)
                except Exception:
                    dead_queues.append(q)

            for q in dead_queues:
                self.listeners.discard(q)


# Singleton event bus instance
event_bus = EventBus()
