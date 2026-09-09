"""x1080x 兼容入口，代理会话及 CF 处理由公共层提供。"""
import time
from dataclasses import replace
from scrapers.core.session import SessionHttpClient
from scrapers.core.pool import shared_pool, stop_shared_clients


class X1080XHttpClient(SessionHttpClient):
    pass


def shared_http_client(settings, endpoint, base_url, rate_settings=None):
    settings = replace(settings, solver_url=endpoint or settings.solver_url)
    if rate_settings:
        settings = replace(settings, min_interval_seconds=rate_settings.min_interval_seconds,
                           cooldown_seconds=rate_settings.cooldown_seconds,
                           max_cooldown_seconds=rate_settings.max_cooldown_seconds)
    return shared_pool("x1080x", settings, base_url)
