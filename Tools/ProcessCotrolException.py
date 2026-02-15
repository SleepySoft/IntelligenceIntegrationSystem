import os
import inspect
import datetime
from contextlib import contextmanager


class ProcessControlException(Exception):
    """Base process exception."""
    def __init__(self, classification: str, position: str, message: str, item=None, **kwargs):
        super().__init__(message)
        self.classification = classification
        self.position = position
        self.item = item
        self.data = kwargs
        self.timestamp = datetime.datetime.now()

        # If user does not set the position flag.
        if not position:
            stack = inspect.stack()
            if len(stack) > 2:
                caller_frame = stack[2]
                self.file_name = os.path.basename(caller_frame.filename)
                self.line_number = caller_frame.lineno
                self.function_name = caller_frame.function
            else:
                self.file_name, self.line_number, self.function_name = "unknown", 0, "unknown"

    def __str__(self):
        return f"[{self.classification} @ {self.file_name}:{self.line_number} in {self.function_name}] {super().__str__()}"


# --------------------------------------------------------------------------------

class ProcessSkip(ProcessControlException):
    """Skip process. Commonly continue a loop."""
    def __init__(self, reason: str, item=None, position: str = "", **kwargs):
        super().__init__(
            classification="SKIP",
            position=position,
            message=f"SKIPPED: {reason}",
            item=item,
            **kwargs
        )
        self.reason = reason


class ProcessRetry(ProcessControlException):
    """Retry operation. Commonly continue and increase retry count."""
    def __init__(self, reason: str, max_attempts=3, item=None, position: str = "", **kwargs):
        super().__init__(
            classification="RETRY",
            position=position,
            message=f"RETRY REQUIRED: {reason}",
            item=item,
            **kwargs
        )
        self.reason = reason
        self.max_attempts = max_attempts


class ProcessPause(ProcessControlException):
    """Pause process. Skip except timeout exceed."""
    def __init__(self, reason: str, resume_after: datetime.timedelta, item=None, position: str = "", **kwargs):
        super().__init__(
            classification="PAUSE",
            position=position,
            message=f"PAUSED: {reason}",
            item=item,
            **kwargs
        )
        self.reason = reason
        self.resume_time = datetime.datetime.now() + resume_after


class ProcessIgnore(ProcessControlException):
    """Ignore. Just do nothing."""
    def __init__(self, reason: str, item=None, position: str = "", **kwargs):
        super().__init__(
            classification="IGNORE",
            position=position,
            message=f"IGNORED: {reason}",
            item=item,
            **kwargs
        )
        self.reason = reason


class ProcessTerminate(ProcessControlException):
    """Have to stop process. Commonly break a loop."""
    def __init__(self, reason: str, exit_code=0, item=None, position: str = "", **kwargs):
        super().__init__(
            classification="TERMINATE",
            position=position,
            message=f"TERMINATED: {reason}",
            item=item,
            **kwargs
        )
        self.exit_code = exit_code


# --------------------------------------------------------------------------------

class ProcessProblem(ProcessControlException):
    """用于处理具体的业务逻辑问题，通常需要根据 problem 类型做分发处理。"""
    def __init__(self, problem: str, position: str = "", item=None, **kwargs):
        super().__init__(
            classification="PROBLEM",
            position=position,
            message=f"PROBLEM: {problem}",
            item=item,
            **kwargs
        )
        self.problem = problem


class ProcessWarning(ProcessControlException):
    def __init__(self, message: str, position: str = "", item=None, **kwargs):
        super().__init__(
            classification="WARNING",
            position=position,
            message=f"WARNING: {message}",
            item=item,
            **kwargs
        )


class ProcessError(ProcessControlException):
    def __init__(self, error_text: str, position: str = "", item=None, **kwargs):
        super().__init__(
            classification="ERROR",
            position=position,
            message=f"ERROR: {error_text}",
            item=item,
            **kwargs
        )


class ProcessCritical(ProcessControlException):
    def __init__(self, error_text: str, position: str = "", item=None, **kwargs):
        super().__init__(
            classification="CRITICAL",
            position=position,
            message=f"CRITICAL: {error_text}",
            item=item,
            **kwargs
        )


# --------------------------------------------------------------------------------

class ValidationException(ProcessControlException):
    """Exception raised when data validation fails."""
    def __init__(self, field: str, message: str, item=None, position: str = "validation", **kwargs):
        super().__init__(
            classification="VALIDATION",
            position=position,
            message=f"VALIDATION FAILURE: {field} - {message}",
            item=item,
            **kwargs
        )
        self.field = field


class MissingFieldError(ValidationException):
    """Exception raised when a required field is missing."""
    def __init__(self, field: str, item=None, **kwargs):
        super().__init__(field=field, message="Required field missing", item=item, **kwargs)


class InvalidTypeError(ValidationException):
    """Exception raised when a field has an incorrect data type."""
    def __init__(self, field: str, expected_type: str, item=None, **kwargs):
        super().__init__(field=field, message=f"Expected type {expected_type}", item=item, **kwargs)


# --------------------------------------------------------------------------------

class PositioningException(ProcessControlException):
    """Exception used to wrap an internal error with specific positional context."""
    def __init__(self, position: str, message: str, original_exception: Exception = None, item=None, **kwargs):
        # We store the original exception in kwargs to pass it to the base class data dict
        kwargs['original_exception'] = original_exception

        super().__init__(
            classification="EXECUTION_ERROR",
            position=position,
            message=f"Error at [{position}]: {message}",
            item=item,
            **kwargs
        )
        self.original_exception = original_exception

@contextmanager
def positioning_exception_context(position: str, message: str):
    try:
        yield
    except Exception as e:
        raise PositioningException(position, message, e) from e
