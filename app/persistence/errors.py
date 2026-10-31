"""公开错误仅保留稳定代码与固定说明，不携带驱动异常或秘密。"""

MESSAGES = {
    "storage_configuration_invalid": "数据库配置缺失或格式错误。",
    "storage_dependency_missing": "启用数据库需要安装持久化依赖。",
    "storage_not_initialized": "数据库资源尚未初始化。",
    "storage_disabled": "数据库功能未启用。",
    "storage_closing": "数据库资源正在关闭或已关闭。",
    "storage_unavailable": "数据库连接暂时不可用。",
    "storage_pool_timeout": "等待数据库连接超时。",
    "storage_health_timeout": "数据库连接检查超时。",
    "storage_cleanup_failed": "数据库资源清理未能按时完成。",
}


class StorageError(Exception):
    def __init__(self, code, *, retryable=False, sqlstate=None, replay_safe=False):
        self.code = code
        self.message = MESSAGES[code]
        self.retryable = retryable
        self.sqlstate = sqlstate
        self.replay_safe = replay_safe
        super().__init__(f"{code}: {self.message}")

    def public_error(self):
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class DatabaseConfigurationError(StorageError):
    def __init__(self):
        super().__init__("storage_configuration_invalid")
