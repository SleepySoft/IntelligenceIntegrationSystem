import datetime
from typing import List
from pydantic import BaseModel, Field


class CollectedData(BaseModel):
    UUID: str = Field(..., min_length=1)    # [MUST]: The UUID to identify a message.
    token: str = Field(..., min_length=1)   # [MUST]: The token to identify the legal end point.
    source: str | None = None               # (Optional): Message source. If it requires reply.
    target: str | None = None               # (Optional): Message source. If it requires reply.
    prompt: str | None = None               # (Optional): The prompt to ask LLM to process this message.

    title: str | None = None                # [MUST]: The content to be processed.
    authors: List[str] | None = []          # (Optional): Article authors.
    content: str                            # [MUST]: The content to be processed.
    pub_time: object | None = None          # (Optional): Content publish time. Can be time.struct_time, datetime, str, ...
    informant: str | None = None            # (Optional): The source of message (like URL).


class ProcessedData(BaseModel):
    UUID: str = Field(..., min_length=1)
    INFORMANT: str = Field(..., min_length=1)
    PUB_TIME: str | datetime.datetime | None = None

    TIME: list | None = Field(default_factory=list)
    LOCATION: list | None = Field(default_factory=list)
    PEOPLE: list | None = Field(default_factory=list)
    ORGANIZATION: list | None = Field(default_factory=list)
    EVENT_TITLE: str | None = Field(..., min_length=1)
    EVENT_BRIEF: str | None = Field(..., min_length=1)
    EVENT_TEXT: str | None = None

    RATE: dict | None = {}
    IMPACT: str | None = None
    TIPS: str | None = None


class ArchivedDataExtraFields(BaseModel):
    RAW_DATA: dict | None
    SUBMITTER: str | None
    APPENDIX: dict | None


class ArchivedData(ProcessedData, ArchivedDataExtraFields):
    pass


APPENDIX_TIME_GOT           = '__TIME_GOT__'            # Timestamp of get from collector
APPENDIX_TIME_POST          = '__TIME_POST__'           # Timestamp of post to processor
APPENDIX_TIME_DONE          = '__TIME_DONE__'           # Timestamp of retrieve from processor
APPENDIX_TIME_ARCHIVED      = '__TIME_ARCHIVED__'
APPENDIX_RETRY_COUNT        = '__RETRY_COUNT__'
APPENDIX_ARCHIVED_FLAG      = '__ARCHIVED__'
APPENDIX_MAX_RATE_CLASS     = '__MAX_RATE_CLASS__'
APPENDIX_MAX_RATE_SCORE     = '__MAX_RATE_SCORE__'
APPENDIX_MAX_RATE_CLASS_EXCLUDE = '内容准确率'

APPENDIX_MANUAL_RATING      = '__MANUAL_RATING__'

APPENDIX_AI_SERVICE         = '__AI_SERVICE__'
APPENDIX_AI_MODEL           = '__AI_MODEL__'

APPENDIX_LINK_UPSTREAM      = '__LINK_UPSTREAM__'
APPENDIX_LINK_DOWNSTREAM    = '__LINK_DOWNSTREAM__'


ARCHIVED_FLAG_DROP = 'D'
ARCHIVED_FLAG_ERROR = 'E'
ARCHIVED_FLAG_RETRY = 'R'
ARCHIVED_FLAG_ARCHIVED = 'A'
ARCHIVED_FLAG_SENSITIVE = 'S'


VECTOR_DB_SUMMARY = 'intelligence_summary'
VECTOR_DB_FULL_TEXT = 'intelligence_full_text'

