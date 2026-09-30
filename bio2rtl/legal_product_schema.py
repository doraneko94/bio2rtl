from __future__ import annotations

"""Schema-neutral accessors for proof-backed legal event products.

Legal products may encode a recovered two-phase source as legacy ``H/L`` strings
or as the actual scheduler-owned source values (usually 0/1).  They may also
carry a sampled input using legacy ``sda/event_sda`` labels or generic
``data/next_data`` labels.  Consumers must use semantic topology, never a
protocol-specific field spelling, to interpret these values.
"""


def edge_index(lp: dict) -> dict[str, int]: 
    return {str(n): i for i, n in enumerate(lp.get("edge_tuple", []))}


def state_index(lp: dict) -> dict[str, int]: 
    return {str(n): i for i, n in enumerate(lp.get("state_tuple", []))}


def phase_level_map(lp: dict) -> dict[object, str]: 
    """Return raw phase-value -> ``H``/``L`` from recovered phase topology.

    A state whose outgoing canonical phase event is PHEVT_RISE is LOW; a state
    whose outgoing canonical phase event is PHEVT_FALL is HIGH.  Legacy H/L
    products are accepted directly.  No GPIO number, protocol name, or project
    signal name is involved.
    """
    out: dict[object, str] = {"H": "H", "L": "L"}
    topo = lp.get("topology") or {}
    pe = topo.get("phase_events") or {}
    for raw, ev in pe.items(): 
        try: 
            key: object = int(raw)
        except (TypeError, ValueError): 
            key = raw
        sev = str(ev)
        if sev == "PHEVT_RISE": 
            out[key] = "L"
        elif sev == "PHEVT_FALL": 
            out[key] = "H"
    return out


def phase_label(lp: dict, raw: object) -> str: 
    m = phase_level_map(lp)
    if raw in m: 
        return m[raw]
    s = str(raw)
    if s in m: 
        return m[s]
    try: 
        i = int(raw)
    except (TypeError, ValueError): 
        i = None
    if i is not None and i in m: 
        return m[i]
    raise ValueError(f"legal product phase value {raw!r} cannot be classified from topology")


def data_fields(lp: dict) -> tuple[str | None, str | None]: 
    """Discover current/next sampled-data scalar fields from tuple structure."""
    E = set(map(str, lp.get("edge_tuple", [])))
    # Generic product first; legacy compatibility second.
    for cur, nxt in (("data", "next_data"), ("sampled_data", "next_sampled_data"), ("sda", "next_sda")): 
        if cur in E and nxt in E: 
            return cur, nxt
    return None, None


def busy_fields(lp: dict) -> tuple[str | None, str | None]: 
    E = set(map(str, lp.get("edge_tuple", [])))
    for cur, nxt in (("busy", "next_busy"),): 
        if cur in E and nxt in E: 
            return cur, nxt
    return None, None


def shared_counter_field(lp: dict) -> tuple[str | None, str | None]: 
    E = set(map(str, lp.get("edge_tuple", [])))
    for cur, nxt in (("shared_counter", "next_shared_counter"), ("PCOUNT", "next_PCOUNT")): 
        if cur in E and nxt in E: 
            return cur, nxt
    return None, None


def event_sample(lp: dict, edge: list, ei: dict[str, int] | None = None) -> int: 
    """Recover the sampled data value used by one legal edge.

    Legacy products store it explicitly.  Generic products store current/next
    data.  On a phase-rising completion the newly sampled value is ``next``;
    on other actions the semantic event environment uses the current value.
    """
    ei = ei or edge_index(lp)
    for name in ("event_sample", "event_sda"): 
        if name in ei: 
            return int(edge[ei[name]])
    cur, nxt = data_fields(lp)
    if cur is None or nxt is None: 
        return 0
    ev = str(edge[ei["event"]])
    topo = lp.get("topology") or {}
    pe = {str(v): k for k, v in (topo.get("phase_events") or {}).items()}
    # Canonical rising completion enters the high/active sampled phase.
    if ev == "PHEVT_RISE" or (ev in pe and phase_label(lp, pe[ev]) == "L"): 
        return int(edge[ei[nxt]])
    return int(edge[ei[cur]])


def scalar_source_fields(lp: dict) -> list[str]: 
    """Return non-control scalar pre-state fields without protocol aliases."""
    E = list(map(str, lp.get("edge_tuple", [])))
    excluded = {"class", "phase", "event", "event_sample", "event_sda"}
    return [n for n in E if not n.startswith("next_") and n not in excluded]
