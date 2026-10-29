"""只公开固定代码，不回显正文、连接串或驱动异常。"""

MESSAGES = {
    "memory_invalid_arguments": "记忆参数不符合契约。",
    "memory_source_rejected": "记忆内容必须来自当前问题的原文片段。",
    "memory_sensitive_content": "记忆包含不允许保存的秘密或敏感信息。",
    "memory_scope_forbidden": "无权访问该记忆范围。",
    "memory_conflict": "同一逻辑键已有不同内容，请先检索并显式更新。",
    "memory_version_conflict": "记忆版本已变化，请重新检索。",
    "memory_not_found": "当前范围中没有可操作的有效记忆。",
    "memory_unavailable": "记忆数据库暂时不可用。",
    "memory_schema_missing": "记忆迁移尚未完成。",
    "memory_commit_unknown": "记忆提交结果未知，只能检索核对，未自动重新写入。",
    "memory_busy": "该记忆正在更新，请先核对当前版本。",
    "memory_invalid_record": "记忆记录无法通过完整性校验。",
    "memory_configuration_invalid": "记忆配置缺失或格式错误。",
}


class MemoryFailure(Exception):
    def __init__(self, code):
        self.code = code if code in MESSAGES else "memory_invalid_record"
        self.message = MESSAGES[self.code]
        super().__init__(self.code)
