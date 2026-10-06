"""已准备证据的访问异常，与 Agent 工具层独立。"""


class UnknownPaperError(Exception):
    pass


class EvidenceDependencyError(Exception):
    pass
