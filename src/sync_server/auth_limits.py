"""Bounded, process-local login budgets; called only on the network loop."""
import ipaddress
import time
from collections import OrderedDict, deque


def normalize_ip(address):
    value = ipaddress.ip_address(address.split("%", 1)[0])
    return str(value.ipv4_mapped or value) if isinstance(value, ipaddress.IPv6Address) else str(value)


class AuthLimits:
    def __init__(self, config, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.buckets = OrderedDict()
        self.inflight = 0

    def begin(self, ip, account):
        now = self.clock()
        cutoff = now - self.config.auth_window_seconds
        # Updated buckets move to the end, so expiry work is bounded by live keys.
        while self.buckets and next(iter(self.buckets.values()))[-1] <= cutoff:
            self.buckets.popitem(last=False)
        keys = (("ip", normalize_ip(ip)), ("account", account))
        limits = (self.config.auth_ip_attempts, self.config.auth_user_attempts)
        for key, limit in zip(keys, limits):
            bucket = self.buckets.get(key)
            if bucket:
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return "rate_limit"
        # Do not evict active limits under attack; fail closed until keys expire.
        if len(self.buckets) + sum(key not in self.buckets for key in keys) > 4096:
            return "capacity"
        if self.inflight >= self.config.auth_max_inflight:
            return "busy"
        for key in keys:
            self.buckets.setdefault(key, deque()).append(now)
            self.buckets.move_to_end(key)
        self.inflight += 1
        return None

    def finish(self):
        self.inflight -= 1
