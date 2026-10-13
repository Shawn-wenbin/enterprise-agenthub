"""Redis 原子固定窗口，跨 API 进程共享来源与账号限制。"""

import hashlib
from typing import Any

from redis.asyncio import Redis

from agenthub.core.errors import AppError

# 同一脚本内更新两类计数和首次过期；拒绝请求也计数，不延长窗口。
SCRIPT = """
local allowed = 1
local retry = 0
for i, key in ipairs(KEYS) do
    local count = redis.call('INCR', key)
    if count == 1 then redis.call('EXPIRE', key, ARGV[1]) end
    if count > tonumber(ARGV[i + 1]) then
        allowed = 0
        retry = math.max(retry, redis.call('TTL', key))
    end
end
return {allowed, retry}
"""


class LoginRateLimiter:
    def __init__(
        self,
        redis: Redis,
        window: int,
        source_limit: int,
        account_limit: int,
        namespace: str,
    ) -> None:
        self.redis = redis
        self.window = window
        self.source_limit = source_limit
        self.account_limit = account_limit
        self.namespace = namespace

    async def check(self, source: str, login_name: str) -> None:
        keys = [
            f"{self.namespace}:source:{hashlib.sha256(source.encode()).hexdigest()}",
            f"{self.namespace}:account:{hashlib.sha256(login_name.encode()).hexdigest()}",
        ]
        result: Any = await self.redis.eval(
            SCRIPT, 2, *keys, self.window, self.source_limit, self.account_limit
        )
        if result[0] != 1:
            raise AppError(
                "login_rate_limited",
                "Too many login attempts",
                429,
                {"retry_after_seconds": max(1, int(result[1]))},
            )
