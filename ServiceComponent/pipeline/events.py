"""主流程阶段名。阶段名是稳定契约，payload 不是。"""

INTAKE_RECEIVED = "intake.received"
INTAKE_ACCEPTED = "intake.accepted"
INTAKE_REJECTED = "intake.rejected"

ANALYSIS_REQUESTED = "analysis.requested"
ANALYSIS_COMPLETED = "analysis.completed"
ANALYSIS_FAILED = "analysis.failed"

ARCHIVE_REQUESTED = "archive.requested"
ARCHIVE_COMPLETED = "archive.completed"
ARCHIVE_FAILED = "archive.failed"

# Shadow 使用独立 worker group；事件只在正式结果完成持久化后投递。
SHADOW_REQUESTED = "analysis.shadow.requested"
