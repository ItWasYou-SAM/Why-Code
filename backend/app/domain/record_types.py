"""The nine normalized record types. Exactly these; nothing else."""

from enum import StrEnum


class RecordType(StrEnum):
    PULL_REQUEST = "pull_request"
    COMMIT = "commit"
    CHANGED_FILE = "changed_file"
    REVIEW = "review"
    REVIEW_COMMENT = "review_comment"
    ISSUE = "issue"
    ISSUE_COMMENT = "issue_comment"
    PERSON = "person"
    DOC_FILE = "doc_file"
