from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class RunStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


@dataclass(frozen=True)
class FetchResult:
    url: str
    body: Optional[bytes]
    status_code: Optional[int]
    attempts: int
    elapsed_ms: int
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and bool(self.body)


@dataclass
class RunSummary:
    source: str
    run_id: str
    discovered: int = 0
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    saved: int = 0
    updated: int = 0
    retries: int = 0
    elapsed_ms: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> RunStatus:
        if self.failed == 0:
            return RunStatus.SUCCESS
        if self.succeeded > 0:
            return RunStatus.PARTIAL_SUCCESS
        return RunStatus.FAILED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "run_id": self.run_id,
            "status": self.status.value,
            "discovered": self.discovered,
            "requested": self.requested,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "saved": self.saved,
            "updated": self.updated,
            "retries": self.retries,
            "elapsed_ms": self.elapsed_ms,
            **self.details,
        }
