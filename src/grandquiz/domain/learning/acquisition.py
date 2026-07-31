"""Web Acquisition 的可恢复管理台账。

本模块拥有状态机、审批令牌与候选快照；HTTP 只投影它，不自行发明第二套生命周期。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel

from grandquiz.domain.learning.ingest import PreparedIngest
from grandquiz.domain.learning.ingest.pipeline import (
    IngestFailureCode,
    IngestFailureStage,
)
from grandquiz.domain.learning.persistence import DatabaseSource, LearningDatabase, database_from

AcquisitionKind = Literal["upload", "url"]
AcquisitionStatus = Literal["queued", "running", "needs_input", "succeeded", "failed", "cancelled"]
AcquisitionFailureCode = IngestFailureCode | Literal["processing_failed", "interrupted"]
AcquisitionFailureStage = IngestFailureStage | Literal["processing", "runtime"]
_TERMINAL = {"succeeded", "failed", "cancelled"}


class AcquisitionTransitionError(RuntimeError):
    """调用方请求了不合法或不可重复的状态跃迁。"""


class AcquisitionRun(BaseModel):
    run_id: str
    trace_id: str
    kind: AcquisitionKind
    locator: str
    display_name: str
    status: AcquisitionStatus
    request_payload: dict[str, str]
    prepared: PreparedIngest | None = None
    token_expires_at: float
    token_used_at: float | None = None
    created_at: float
    updated_at: float
    resource_id: str | None = None
    error_code: AcquisitionFailureCode | None = None
    error_stage: AcquisitionFailureStage | None = None
    error_message: str | None = None


class AcquisitionLedger:
    """持久状态机；每个公开变更都立即提交或参与外层 LearningDatabase 事务。"""

    def __init__(self, db: DatabaseSource) -> None:
        self._db: LearningDatabase = database_from(db)
        self._owns_db = not isinstance(db, LearningDatabase)

    @property
    def transaction_owner(self) -> LearningDatabase:
        return self._db

    def create(
        self,
        *,
        run_id: str,
        trace_id: str,
        kind: AcquisitionKind,
        locator: str,
        display_name: str,
        request_payload: Mapping[str, str],
        token_hash: str,
        token_expires_at: float,
        now: float,
    ) -> AcquisitionRun:
        self._db.connection.execute(
            """
            INSERT INTO acquisition_runs (
                run_id, trace_id, kind, locator, display_name, status,
                request_payload, token_hash, token_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trace_id,
                kind,
                locator,
                display_name,
                json.dumps(dict(request_payload), ensure_ascii=False, sort_keys=True),
                token_hash,
                token_expires_at,
                now,
                now,
            ),
        )
        self._db.commit()
        return self.require(run_id)

    def get(self, run_id: str) -> AcquisitionRun | None:
        row = self._db.connection.execute(
            """
            SELECT runs.run_id, runs.trace_id, runs.kind, runs.locator, runs.display_name,
                   runs.status, runs.request_payload, runs.prepared_payload,
                   runs.token_expires_at, runs.token_used_at, runs.created_at, runs.updated_at,
                   runs.resource_id, runs.error_code, runs.error_message, failures.error_stage
            FROM acquisition_runs AS runs
            LEFT JOIN acquisition_run_failures AS failures ON failures.run_id = runs.run_id
            WHERE runs.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def require(self, run_id: str) -> AcquisitionRun:
        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def recent(self, *, limit: int = 20) -> list[AcquisitionRun]:
        rows = self._db.connection.execute(
            """
            SELECT runs.run_id, runs.trace_id, runs.kind, runs.locator, runs.display_name,
                   runs.status, runs.request_payload, runs.prepared_payload,
                   runs.token_expires_at, runs.token_used_at, runs.created_at, runs.updated_at,
                   runs.resource_id, runs.error_code, runs.error_message, failures.error_stage
            FROM acquisition_runs AS runs
            LEFT JOIN acquisition_run_failures AS failures ON failures.run_id = runs.run_id
            ORDER BY runs.updated_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def in_flight(self) -> list[AcquisitionRun]:
        rows = self._db.connection.execute(
            """
            SELECT runs.run_id, runs.trace_id, runs.kind, runs.locator, runs.display_name,
                   runs.status, runs.request_payload, runs.prepared_payload,
                   runs.token_expires_at, runs.token_used_at, runs.created_at, runs.updated_at,
                   runs.resource_id, runs.error_code, runs.error_message, failures.error_stage
            FROM acquisition_runs AS runs
            LEFT JOIN acquisition_run_failures AS failures ON failures.run_id = runs.run_id
            WHERE runs.status IN ('queued', 'running')
            ORDER BY runs.updated_at
            """
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def mark_running(self, run_id: str, *, now: float) -> AcquisitionRun:
        return self._transition(run_id, expected={"queued"}, status="running", now=now)

    def mark_needs_input(
        self,
        run_id: str,
        *,
        prepared: PreparedIngest,
        now: float,
    ) -> AcquisitionRun:
        return self._transition(
            run_id,
            expected={"running"},
            status="needs_input",
            now=now,
            prepared_payload=prepared.model_dump_json(),
        )

    def consume_approval_token(
        self,
        run_id: str,
        *,
        token_hash: str,
        now: float,
    ) -> AcquisitionRun:
        with self._db.transaction() as conn:
            row = conn.execute(
                """
                SELECT status, token_hash, token_expires_at, token_used_at
                FROM acquisition_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if str(row[0]) != "needs_input":
                raise AcquisitionTransitionError("当前状态不能提交审批")
            if row[3] is not None:
                raise AcquisitionTransitionError("审批令牌已经使用")
            if now > float(row[2]):
                raise AcquisitionTransitionError("审批令牌已经过期")
            if not secrets.compare_digest(str(row[1]), token_hash):
                raise AcquisitionTransitionError("审批令牌无效")
            conn.execute(
                "UPDATE acquisition_runs SET token_used_at = ?, updated_at = ? WHERE run_id = ?",
                (now, now, run_id),
            )
        return self.require(run_id)

    def verify_control_token(
        self,
        run_id: str,
        *,
        token_hash: str,
        now: float,
    ) -> AcquisitionRun:
        row = self._db.connection.execute(
            "SELECT token_hash, token_expires_at FROM acquisition_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        if now > float(row[1]):
            raise AcquisitionTransitionError("控制令牌已经过期")
        if not secrets.compare_digest(str(row[0]), token_hash):
            raise AcquisitionTransitionError("控制令牌无效")
        return self.require(run_id)

    def mark_succeeded(
        self,
        run_id: str,
        *,
        resource_id: str,
        now: float,
    ) -> AcquisitionRun:
        return self._transition(
            run_id,
            expected={"needs_input"},
            status="succeeded",
            now=now,
            resource_id=resource_id,
            clear_payloads=True,
        )

    def mark_failed(
        self,
        run_id: str,
        *,
        code: AcquisitionFailureCode,
        stage: AcquisitionFailureStage,
        message: str,
        now: float,
    ) -> AcquisitionRun:
        run = self.require(run_id)
        if run.status in _TERMINAL:
            return run
        return self._transition(
            run_id,
            expected={run.status},
            status="failed",
            now=now,
            error_code=code,
            error_stage=stage,
            error_message=message,
            clear_payloads=True,
        )

    def mark_cancelled(self, run_id: str, *, now: float) -> AcquisitionRun:
        run = self.require(run_id)
        if run.status in _TERMINAL:
            return run
        return self._transition(
            run_id,
            expected={run.status},
            status="cancelled",
            now=now,
            clear_payloads=True,
        )

    def fail_interrupted_runs(self, *, now: float) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO acquisition_run_failures (run_id, error_stage)
                SELECT run_id, 'runtime'
                FROM acquisition_runs
                WHERE status IN ('queued', 'running')
                """
            )
            conn.execute(
                """
                UPDATE acquisition_runs
                SET status = 'failed', error_code = 'interrupted',
                    error_message = '服务在材料处理期间重启，请重试',
                    request_payload = '{}', prepared_payload = NULL, updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )

    def _transition(
        self,
        run_id: str,
        *,
        expected: set[str],
        status: AcquisitionStatus,
        now: float,
        prepared_payload: str | None = None,
        resource_id: str | None = None,
        error_code: AcquisitionFailureCode | None = None,
        error_stage: AcquisitionFailureStage | None = None,
        error_message: str | None = None,
        clear_payloads: bool = False,
    ) -> AcquisitionRun:
        run = self.require(run_id)
        if run.status not in expected:
            raise AcquisitionTransitionError(f"不能从 {run.status} 转换到 {status}")
        request_sql = "'{}'" if clear_payloads else "request_payload"
        prepared_sql = "NULL" if clear_payloads else "COALESCE(?, prepared_payload)"
        parameters: tuple[object, ...]
        if clear_payloads:
            parameters = (
                status,
                now,
                resource_id,
                error_code,
                error_message,
                run_id,
            )
        else:
            parameters = (
                status,
                now,
                prepared_payload,
                resource_id,
                error_code,
                error_message,
                run_id,
            )
        with self._db.transaction() as conn:
            conn.execute(
                f"""
                UPDATE acquisition_runs
                SET status = ?, updated_at = ?, prepared_payload = {prepared_sql},
                    request_payload = {request_sql},
                    resource_id = COALESCE(?, resource_id),
                    error_code = ?, error_message = ?
                WHERE run_id = ?
                """,
                parameters,
            )
            if error_stage is not None:
                conn.execute(
                    """
                    INSERT INTO acquisition_run_failures (run_id, error_stage)
                    VALUES (?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET error_stage = excluded.error_stage
                    """,
                    (run_id, error_stage),
                )
        return self.require(run_id)

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> AcquisitionRun:
        prepared_raw = None if row[7] is None else str(row[7])
        raw_error_code = None if row[13] is None else str(row[13])
        raw_error_stage = None if row[15] is None else str(row[15])
        if raw_error_code == "acquisition_failed":
            raw_error_code = "ingest_failed"
            raw_error_stage = raw_error_stage or "reader"
        elif raw_error_code == "processing_failed":
            raw_error_stage = raw_error_stage or "processing"
        elif raw_error_code == "interrupted":
            raw_error_stage = raw_error_stage or "runtime"
        return AcquisitionRun(
            run_id=str(row[0]),
            trace_id=str(row[1]),
            kind=cast("AcquisitionKind", str(row[2])),
            locator=str(row[3]),
            display_name=str(row[4]),
            status=cast("AcquisitionStatus", str(row[5])),
            request_payload=json.loads(str(row[6])),
            prepared=(
                None if prepared_raw is None else PreparedIngest.model_validate_json(prepared_raw)
            ),
            token_expires_at=float(row[8]),
            token_used_at=None if row[9] is None else float(row[9]),
            created_at=float(row[10]),
            updated_at=float(row[11]),
            resource_id=None if row[12] is None else str(row[12]),
            error_code=cast("AcquisitionFailureCode | None", raw_error_code),
            error_message=None if row[14] is None else str(row[14]),
            error_stage=cast("AcquisitionFailureStage | None", raw_error_stage),
        )

    def close(self) -> None:
        if self._owns_db:
            self._db.close()
