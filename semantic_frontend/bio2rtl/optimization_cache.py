from __future__ import annotations

"""Content-addressed optimization result cache for bio2rtl.

This module is deliberately independent of any specific BIO binary or I2C design.
A cached negative result is reusable only when the baseline netlist, cell library,
environment contract, pass id, and pass parameters all match exactly.
"""

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json


NEGATIVE_STATUSES = {
    "REJECTED", 
    "REJECTED_5PCT", 
    "REJECTED_5PCT_LOW_BUDGET", 
    "REJECTED_FOR_REV33", 
    "TIMEOUT_NEGATIVE", 
    "TIMEOUT_STATE_EXPLOSION", 
    "FORBIDDEN", 
    "INVALID_AUTHORITY", 
    "NOT_PROOF_SUPPORTED", 
    "ALREADY_CONSUMED_BY_PRIOR_SPECIALIZATION", 
}


@dataclass(frozen = True)
class OptimizationScope: 
    baseline_sha256: str
    cell_library_sha256: str
    environment_contract_sha256: str
    pass_id: str
    parameters: dict[str, Any]

    def fingerprint(self) -> str: 
        payload = json.dumps(asdict(self), sort_keys = True, separators = (",", ":"))
        return sha256(payload.encode()).hexdigest()


class OptimizationCache: 
    def __init__(self, path: Path): 
        self.path = Path(path)
        if self.path.exists(): 
            data = json.loads(self.path.read_text())
        else: 
            data = {"version": "bio2rtl-optimization-cache-v1", "entries": {}}
        if data.get("version") != "bio2rtl-optimization-cache-v1": 
            raise ValueError(f"unsupported optimization-cache version: {data.get('version')}")
        self.data = data
        self.data.setdefault("entries", {})

    def lookup(self, scope: OptimizationScope) -> dict[str, Any] | None: 
        return self.data["entries"].get(scope.fingerprint())

    def should_run(self, scope: OptimizationScope) -> tuple[bool, str]: 
        entry = self.lookup(scope)
        if entry is None: 
            return True, "cache-miss"
        status = str(entry.get("status", ""))
        if status in NEGATIVE_STATUSES: 
            return False, f"negative-cache:{status}"
        if status == "PASS": 
            return False, "positive-cache:PASS"
        return True, f"cache-entry-not-terminal:{status}"

    def record(
        self, 
        scope: OptimizationScope, 
        *, 
        status: str, 
        evidence: dict[str, Any] | None = None, 
        reason: str = "", 
    ) -> str: 
        fp = scope.fingerprint()
        self.data["entries"][fp] = {
            "scope": asdict(scope), 
            "status": str(status), 
            "reason": str(reason), 
            "evidence": evidence or {}, 
        }
        return fp

    def save(self) -> None: 
        self.path.parent.mkdir(parents = True, exist_ok = True)
        self.path.write_text(json.dumps(self.data, indent = 2, sort_keys = True) + "\n")


def sha256_file(path: Path) -> str: 
    h = sha256()
    with Path(path).open("rb") as f: 
        for chunk in iter(lambda: f.read(1024 * 1024), b""): 
            h.update(chunk)
    return h.hexdigest()


def import_legacy_negative_cache(
    cache: OptimizationCache, 
    legacy: dict[str, Any], 
    *, 
    baseline_sha256: str, 
    cell_library_sha256: str, 
    environment_contract_sha256: str, 
) -> int: 
    """Import pass-level legacy negatives without pretending unknown parameters match.

    Each imported item is scoped with parameters={"legacy_scope": True}. A future pass
    must request the same parameter marker to reuse it; otherwise it remains a cache miss.
    This prevents an old failure from suppressing a materially different search.
    """
    count = 0
    for row in legacy.get("negative_cache", []): 
        pid = str(row.get("id", "")).strip()
        status = str(row.get("status", "")).strip()
        if not pid or status not in NEGATIVE_STATUSES: 
            continue
        scope = OptimizationScope(
            baseline_sha256 = baseline_sha256, 
            cell_library_sha256 = cell_library_sha256, 
            environment_contract_sha256 = environment_contract_sha256, 
            pass_id = pid, 
            parameters = {"legacy_scope": True}, 
        )
        cache.record(
            scope, 
            status = status, 
            reason = str(row.get("reason", row.get("replacement", ""))), 
            evidence = {"imported_from_legacy": True}, 
        )
        count += 1
    return count
