"""Lightweight span recorder with W3C traceparent propagation and Prometheus text export.
Spans are kept in-process (bounded); an OTel exporter can subscribe via `add_sink`."""
import contextvars, os, secrets, threading, time
from collections import defaultdict, deque
from contextlib import contextmanager

_trace = contextvars.ContextVar("trace", default=None)
_lock = threading.Lock()
DUR = defaultdict(lambda: deque(maxlen=5000))      # span name -> durations (ms)
RECENT = deque(maxlen=500)                          # recent spans for waterfall
COUNTERS = defaultdict(float)
_sinks = []

def add_sink(fn): _sinks.append(fn)

def new_traceparent():
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"

def start_trace(traceparent=None):
    tp = traceparent if traceparent and len(traceparent.split("-")) == 4 else new_traceparent()
    _trace.set(tp)
    return tp.split("-")[1]

def current_trace_id():
    tp = _trace.get()
    return tp.split("-")[1] if tp else None

@contextmanager
def span(name, **attrs):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        rec = dict(trace_id=current_trace_id(), name=name, ms=round(ms, 3), start=time.time() - ms / 1000, **attrs)
        with _lock:
            DUR[name].append(ms); RECENT.append(rec)
        for s in _sinks:
            try: s(rec)
            except Exception: pass

def inc(name, v=1.0):
    with _lock: COUNTERS[name] += v

def pct(name, q):
    with _lock: xs = sorted(DUR.get(name, ()))
    if not xs: return None
    return xs[min(len(xs) - 1, int(q / 100 * len(xs)))]

def reset():
    with _lock: DUR.clear(); RECENT.clear(); COUNTERS.clear()

def prometheus_text():
    out = []
    with _lock:
        for n, xs in DUR.items():
            s = sorted(xs)
            for q in (50, 95, 99):
                out.append(f'span_duration_ms{{span="{n}",quantile="{q/100}"}} {s[min(len(s)-1,int(q/100*len(s)))]:.3f}')
            out.append(f'span_count{{span="{n}"}} {len(s)}')
        for k, v in COUNTERS.items():
            out.append(f"{k.replace('.', '_')} {v}")
    out.append(f"process_rss_bytes {_rss()}")
    return "\n".join(out) + "\n"

def _rss():
    try:
        with open("/proc/self/statm") as f: return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception: return 0
