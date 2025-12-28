from dataclasses import dataclass, field
from typing import Callable, Any
import heapq

@dataclass(order=True)
class Event:
    time: float
    priority: int
    action: Callable[..., None] = field(compare=False)
    args: tuple = field(compare=False, default=())

class ScheduleCore:
    
    def __init__(self):
        self.current_time: float = 0.0
        self._event_queue: list[Event] = []
    
    def schedule(self, time: float, priority: int, action: Callable, *args):
        event = Event(time=time, priority=priority, action=action, args=args)
        heapq.heappush(self._event_queue, event)

    def run(self, until: float):
        while self._event_queue:
            event = heapq.heappop(self._event_queue)

            if event.time > until:
                break
            
            self.current_time = event.time
            event.action(self.current_time, *event.args)