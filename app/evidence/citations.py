"""仅校验引用身份，不判断证据在语义上是否支持答案。"""


class CitationValidationError(ValueError):
    """模型未引用非空且确实提供过的证据 ID。"""


class CitationValidator:
    def validate(
        self, evidence_used: list[str], evidence_provided: list[str]
    ) -> tuple[str, ...]:
        if not evidence_used:
            raise CitationValidationError("Citation list is empty")
        if not set(evidence_used).issubset(set(evidence_provided)):
            raise CitationValidationError("Vision response cited unknown evidence")
        return tuple(dict.fromkeys(evidence_used))
