from dataclasses import dataclass, field
from typing import Callable, Any
import heapq

@dataclass(order=True)
class Event:
    time: float
    priority: int
    action: Callable[..., None] = field(compare=False)
    args: tuple = field(compare=False, default=())
    eid: int = field(compare=False, default=0)

class ScheduleCore:
    
    def __init__(self):
        self.current_time: float = 0.0
        self._event_queue: list[Event] = []
        self._next_eid: int = 0
        self._executed_events: int = 0
    
    def schedule(self, time: float, priority: int, action: Callable, *args):
        self._next_eid += 1
        event = Event(time=time, priority=priority, action=action, args=args)
        heapq.heappush(self._event_queue, event)
        return event.eid
    
    def step(self):
        if not self._event_queue:
            return False

        event = heapq.heappop(self._event_queue)
        self.current_time = event.time
        event.action(self.current_time, *event.args)
        self._executed_events += 1
        return True

    def run_until(self, until: float):
        while self._event_queue and self._event_queue[0].time <= until:
            self.step()

    def pending_events(self):
        return len(self._event_queue)
    
    def stats(self):
        return {
            "current time": self.current_time,
            "pending events": len(self._event_queue),
            "executed_events": self._executed_events,
        }