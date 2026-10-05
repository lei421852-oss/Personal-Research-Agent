"""Prepared evidence access failures, independent of the Agent tool layer."""


class UnknownPaperError(Exception):
    pass


class EvidenceDependencyError(Exception):
    pass
