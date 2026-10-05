"""Identity validation only: this does not assess semantic support."""


class CitationValidationError(ValueError):
    """Model output did not cite nonempty, supplied evidence identities."""


class CitationValidator:
    def validate(
        self, evidence_used: list[str], evidence_provided: list[str]
    ) -> tuple[str, ...]:
        if not evidence_used:
            raise CitationValidationError("Citation list is empty")
        if not set(evidence_used).issubset(set(evidence_provided)):
            raise CitationValidationError("Vision response cited unknown evidence")
        return tuple(dict.fromkeys(evidence_used))
