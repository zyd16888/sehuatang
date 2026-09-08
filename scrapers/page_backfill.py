"""按页区间补抓的共享基础设施。

设计（学习自可用实现）：
- 补抓 = 指定列表页区间 + 跳过已存在 + 详情失败进失败台账；
- 检查点按「完整处理完的页」推进，详情失败不阻塞检查点
  （失败由台账负责退避重试，页覆盖进度与失败恢复解耦）；
- 检查点键为 source:partition，x1080x 的 partition 是 typeid，
  sehuatang 的 partition 是 fid。
- Sehuatang 分页补抓在读取正常列表后核对明确末页、当前页与整页重复；
  异常列表不推进检查点。已有越界检查点不会自动改写。
"""
import json
from pathlib import Path
from typing import Optional, Sequence

from scrapers.core.contracts import CrawlTarget, DiscoveryResult
from util.log_util import log


class PageCheckpointStore:
    """按 source:partition 记录最后完成页的 JSON 检查点。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else (
            Path(__file__).resolve().parent.parent
            / "data"
            / "page_backfill_progress.json"
        )

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            log.warning(f"读取分页补抓检查点失败，视为无进度: {exc}")
            return {}

    @staticmethod
    def _key(source: str, partition) -> str:
        return f"{source}:{partition}"

    def load(self, source: str, partition) -> int:
        try:
            return int(self._read().get(self._key(source, partition), 0) or 0)
        except (TypeError, ValueError):
            return 0

    def save(self, source: str, partition, page: int) -> None:
        data = self._read()
        data[self._key(source, partition)] = int(page)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def clear(self, source: str, partition) -> None:
        data = self._read()
        if data.pop(self._key(source, partition), None) is not None:
            temp_path = self.path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self.path)


class FixedTargetSource:
    """把一批已知目标包装成 SourceAdapter，供 CrawlEngine 复用
    fetch/parse/save/失败台账 流程（跳过 discover 阶段的列表抓取）。"""

    def __init__(self, source, targets: Sequence[CrawlTarget]):
        self.name = source.name
        self._source = source
        self._targets = list(targets)

    def discover(self, context, http) -> DiscoveryResult:
        return DiscoveryResult(targets=self._targets)

    def parse_detail(self, target, result):
        return self._source.parse_detail(target, result)
