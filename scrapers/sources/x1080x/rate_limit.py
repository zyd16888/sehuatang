"""兼容旧导入；所有来源共用 core.rate_limit。"""
from scrapers.core.rate_limit import (
    BackfillHttpClient, CrawlStopped, RateLimitSettings, RequestGate,
    SiteRateLimited, limited_result,
)
