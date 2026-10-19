from typing import Any
from fastapi import HTTPException, status
from pydantic import BaseModel

class ErrorDetails(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: Any = None

class IncuBrixErrorResponse(BaseModel):
    error: ErrorDetails

class IncuBrixException(HTTPException):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        retryable: bool = False,
        details: Any = None
    ):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "retryable": retryable, "details": details}
        )

# Standard Error Codes (integration standard)
class ErrorCodes:
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    INVALID_TIMELINE_SCHEMA = "INVALID_TIMELINE_SCHEMA"
    TIMELINE_OUT_OF_BOUNDS = "TIMELINE_OUT_OF_BOUNDS"
    EMPTY_TIMELINE = "EMPTY_TIMELINE"
    NOTHING_TO_UNDO = "NOTHING_TO_UNDO"
    NOTHING_TO_REDO = "NOTHING_TO_REDO"
    INVALID_RESTORE_TARGET = "INVALID_RESTORE_TARGET"
    AMBIGUOUS_INSTRUCTION = "AMBIGUOUS_INSTRUCTION"
    INSTRUCTION_REJECTED = "INSTRUCTION_REJECTED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    RENDER_FAILED = "RENDER_FAILED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
