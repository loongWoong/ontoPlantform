"""Use cases for the lightweight ontology workflow.

The workflow is a *platform-side control loop* around the existing ontology
lifecycle.  It never changes the ontology itself: structural edits still go
through :mod:`ontology_usecases`, and the only hard transition is the existing
publish gate.  What this module adds is the evidence around that gate:

    round -> scope -> CQs -> reuse -> sketch/formalize -> samples
          -> acceptance run -> review -> publish -> next round

All state is stored as ordinary repository documents (``workflow_round``,
``workflow_cq``, ...), so it inherits workspace deletion, reset and SQLite
persistence without a new migration.
"""

from __future__ import annotations

from .common import ApplicationContext, active_ontology_row
from ..domain.ontology.model import Ontology
from ..domain.ontology.validation import validate_ontology
from ..domain.workflow.model import (
    acceptance_summary,
    canonical_hash,
    compute_steps,
    evaluate_expectation,
    evaluate_query,
    normalize_cq,
    normalize_reuse,
    normalize_round,
    normalize_sample,
    ontology_fingerprint,
    sample_fingerprint,
    sample_problems,
)
from ..errors import GateError, NotFoundError, ValidationError
from .ontology_usecases import PublishOntology, ReviseOntology
from .workflow_cq_io import export_cqs as export_cq_set, parse_cqs

ROUND = "workflow_round"
CQ = "workflow_cq"
REUSE = "workflow_reuse"
SAMPLE = "workflow_sample"
RUN = "workflow_run"
REVIEW = "workflow_review"

_MUTABLE_CQ = {"code", "question", "type", "priority", "source", "rationale",
               "expected", "query", "model_refs", "sample_refs", "notes"}


def _sort_newest(items: list[dict], key: str = "created_at") -> list[dict]:
    return sorted(items, key=lambda item: (
        int(item.get("seq") or 0), str(item.get(key) or ""),
        str(item.get("id") or "")), reverse=True)


def _positive_int(value, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


class WorkflowUseCases:
    def __init__(self, ctx: ApplicationContext,
                 publisher) -> None:
        self._ctx = ctx
        self._publish_ontology = PublishOntology(ctx, publisher)
        self._revise_ontology = ReviseOntology(ctx)

    # ------------------------------------------------------------------
    # loading helpers
    # ------------------------------------------------------------------

    def _rounds(self, workspace_id: str) -> list[dict]:
        return sorted(self._ctx.repo.list(ROUND, workspace_id),
                      key=lambda row: (int(row.get("seq") or 0),
                                       str(row.get("id") or "")))

    def _round(self, workspace_id: str, round_id: str) -> dict:
        row = self._ctx.repo.get(ROUND, workspace_id, round_id)
        if row is None:
            raise NotFoundError(f"workflow round not found: {round_id!r}")
        return row

    def _items(self, kind: str, workspace_id: str, round_id: str) -> list[dict]:
        return [row for row in self._ctx.repo.list(kind, workspace_id)
                if row.get("round_id") == round_id]

    def _cqs(self, workspace_id: str, round_id: str) -> list[dict]:
        return sorted(self._items(CQ, workspace_id, round_id),
                      key=lambda row: str(row.get("code") or row.get("id")))

    def _reuse(self, workspace_id: str, round_id: str) -> list[dict]:
        return _sort_newest(self._items(REUSE, workspace_id, round_id))

    def _samples(self, workspace_id: str, round_id: str) -> list[dict]:
        return _sort_newest(self._items(SAMPLE, workspace_id, round_id))

    def _runs(self, workspace_id: str, round_id: str) -> list[dict]:
        return _sort_newest(self._items(RUN, workspace_id, round_id))

    def _reviews(self, workspace_id: str, round_id: str) -> list[dict]:
        return _sort_newest(self._items(REVIEW, workspace_id, round_id))

    def _ontology(self, workspace_id: str) -> dict:
        row = active_ontology_row(self._ctx, workspace_id)
        if row is None:
            raise NotFoundError(
                "create the ontology before starting a workflow round")
        return row

    def _ensure_draft(self, workspace_id: str, actor: str) -> dict:
        """A round must publish a *new* version.

        The spiral workflow starts each batch from the last published
        version.  Finding a published ontology therefore means "revise it
        first"; doing that automatically here is what makes "start round"
        work from the console without a hidden extra click.
        """
        ontology = self._ontology(workspace_id)
        if ontology.get("lifecycle") == "published":
            self._revise_ontology(workspace_id, actor)
            ontology = self._ontology(workspace_id)
        return ontology

    def _ontology_summary(self, row: dict) -> dict:
        objects = row.get("objects") or {}
        relations = row.get("relations") or {}
        properties: dict[str, list[str]] = {}
        for name, spec in objects.items():
            properties[name] = sorted((spec.get("properties") or {}).keys())
        return {
            "id": row.get("id"),
            "name": row.get("name"),
            "version": int(row.get("version") or 0),
            "lifecycle": row.get("lifecycle"),
            "fingerprint": ontology_fingerprint(row),
            "objects": sorted(objects),
            "relations": sorted(relations),
            "properties": properties,
        }

    def _validation(self, workspace_id: str, row: dict) -> dict:
        try:
            ontology = Ontology.from_dict(row)
            problems = validate_ontology(ontology)
        except Exception as exc:  # a malformed draft is a blocker, not a 500
            problems = [str(exc)]
        return {
            "ok": not problems,
            "problems": problems,
            "checked_at": self._ctx.clock(),
            "ontology_fingerprint": ontology_fingerprint(row),
        }

    def _model_refs(self, row: dict) -> set[str]:
        valid: set[str] = set()
        for name, spec in (row.get("objects") or {}).items():
            valid.add(name)
            for prop in (spec.get("properties") or {}):
                valid.add(f"{name}.{prop}")
        valid.update((row.get("relations") or {}).keys())
        valid.update((row.get("vocabulary") or {}).keys())
        return valid

    def _assert_model_refs(self, workspace_id: str, refs: list[str]) -> None:
        if not refs:
            return
        valid = self._model_refs(self._ontology(workspace_id))
        unknown = sorted(ref for ref in refs if ref not in valid)
        if unknown:
            raise ValidationError(
                "unknown model reference(s): " + ", ".join(unknown))

    def _touch_round(self, workspace_id: str, round_id: str,
                     *, reset_review: bool = False) -> dict:
        row = self._round(workspace_id, round_id)
        if row.get("status") == "accepted":
            raise ValidationError(
                "this round is accepted; open the next round to continue")
        row["status"] = "active"
        row["updated_at"] = self._ctx.clock()
        self._ctx.save(ROUND, row)
        return row

    # ------------------------------------------------------------------
    # public read surface
    # ------------------------------------------------------------------

    def overview(self, workspace_id: str, actor: str,
                 round_id: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        rounds = self._rounds(workspace_id)
        active = next((row for row in reversed(rounds)
                       if row.get("status") != "accepted"), None)
        chosen_id = round_id or (active or {}).get("id") or ""
        detail = self.detail(workspace_id, actor, chosen_id) if chosen_id else None
        return {
            "rounds": rounds,
            "active_round_id": chosen_id,
            "round": detail,
        }

    def detail(self, workspace_id: str, actor: str, round_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        round_doc = self._round(workspace_id, round_id)
        cqs = self._cqs(workspace_id, round_id)
        reuse = self._reuse(workspace_id, round_id)
        samples = self._samples(workspace_id, round_id)
        runs = self._runs(workspace_id, round_id)
        reviews = self._reviews(workspace_id, round_id)
        ontology = self._ontology(workspace_id)
        fingerprint = ontology_fingerprint(ontology)
        for sample in samples:
            problems = sample_problems(sample, ontology)
            sample["validation"] = {"ok": not problems, "problems": problems}
        validation = round_doc.get("validation") or {}
        steps = compute_steps(
            round_doc=round_doc, ontology=ontology, cqs=cqs, reuse=reuse,
            samples=samples, runs=runs, reviews=reviews,
            validation=validation, fingerprint=fingerprint)
        detail = {
            "round": round_doc,
            "cqs": cqs,
            "reuse": reuse,
            "samples": samples,
            "runs": runs,
            "reviews": reviews,
            "steps": steps,
            "validation": validation,
            "ontology": self._ontology_summary(ontology),
            "trace_matrix": self._trace_matrix(cqs, runs),
            "publish_check": self._publish_check(
                workspace_id, round_doc, cqs, reuse, samples, runs, reviews,
                ontology),
            "sample_template": self._sample_template(ontology),
        }
        return detail

    # ------------------------------------------------------------------
    # rounds
    # ------------------------------------------------------------------

    def create_round(self, workspace_id: str, actor: str,
                     payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        rounds = self._rounds(workspace_id)
        open_round = next(
            (row for row in reversed(rounds)
             if row.get("status") != "accepted"), None)
        if open_round is not None:
            raise ValidationError(
                "there is already an open round; publish or finish it first")
        ontology = self._ensure_draft(workspace_id, actor)
        seq = max((int(row.get("seq") or 0) for row in rounds), default=0) + 1
        raw = dict(payload or {})
        raw.setdefault("name", f"第 {seq} 轮")
        raw["ontology_id"] = ontology.get("id")
        raw["ontology_version"] = int(ontology.get("version") or 0)
        raw["ontology_fingerprint"] = ontology_fingerprint(ontology)
        raw["created_by"] = actor
        now = self._ctx.clock()
        round_id = self._ctx.id_factory()
        doc = normalize_round(raw, workspace_id=workspace_id,
                              round_id=round_id, seq=seq, now=now, actor=actor)
        self._ctx.save(ROUND, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.round.create",
                              round_id, {"seq": seq, "name": doc["name"]})
        return doc

    def update_round(self, workspace_id: str, actor: str, round_id: str,
                     payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        current = self._round(workspace_id, round_id)
        if current.get("status") == "accepted":
            raise ValidationError(
                "this round is accepted; open the next round to continue")
        merged = {**current}
        for key in ("name", "goal", "scope", "target", "reuse_skipped"):
            if key in (payload or {}):
                merged[key] = (payload or {})[key]
        if "validation" in (payload or {}):
            merged["validation"] = payload["validation"]
        doc = normalize_round(merged, workspace_id=workspace_id,
                              round_id=round_id,
                              seq=int(current.get("seq") or 1),
                              now=self._ctx.clock(),
                              actor=current.get("created_by") or actor)
        doc["created_at"] = current.get("created_at") or doc["created_at"]
        doc["created_by"] = current.get("created_by") or actor
        doc["status"] = "active"
        doc["ontology_id"] = current.get("ontology_id")
        doc["ontology_version"] = current.get("ontology_version")
        doc["ontology_fingerprint"] = current.get("ontology_fingerprint")
        doc["published_version"] = current.get("published_version")
        doc["published_at"] = current.get("published_at")
        self._ctx.save(ROUND, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.round.update",
                              round_id, {"keys": sorted((payload or {}))})
        return doc

    def bind_round(self, workspace_id: str, actor: str,
                   round_id: str) -> dict:
        """Point a round at the current active ontology (e.g. after a revise)."""
        self._ctx.require(actor, workspace_id, "editor")
        row = self._round(workspace_id, round_id)
        if row.get("status") == "accepted":
            raise ValidationError("accepted rounds cannot be rebound")
        ontology = self._ontology(workspace_id)
        row.update({
            "ontology_id": ontology.get("id"),
            "ontology_version": int(ontology.get("version") or 0),
            "ontology_fingerprint": ontology_fingerprint(ontology),
            "validation": {},
            "status": "active",
            "updated_at": self._ctx.clock(),
        })
        self._ctx.save(ROUND, row)
        self._ctx.audit_event(actor, workspace_id, "workflow.round.bind",
                              round_id,
                              {"ontology_version": row["ontology_version"]})
        return row

    def next_round(self, workspace_id: str, actor: str, round_id: str,
                   payload: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        current = self._round(workspace_id, round_id)
        if current.get("status") != "accepted":
            raise GateError("only an accepted round can open the next round")
        payload = payload or {}
        rounds = self._rounds(workspace_id)
        open_round = next(
            (row for row in reversed(rounds)
             if row.get("id") != round_id and row.get("status") != "accepted"),
            None)
        if open_round is not None:
            raise ValidationError(
                "another round is still open; finish it before starting the next")
        ontology = self._ensure_draft(workspace_id, actor)
        seq = max((int(row.get("seq") or 0) for row in rounds), default=0) + 1
        raw = {
            "name": payload.get("name") or f"第 {seq} 轮",
            "goal": payload.get("goal", ""),
            "scope": payload.get("scope") or {},
            "target": payload.get("target") or current.get("target"),
            "created_by": actor,
            "ontology_id": ontology.get("id"),
            "ontology_version": int(ontology.get("version") or 0),
            "ontology_fingerprint": ontology_fingerprint(ontology),
        }
        now = self._ctx.clock()
        next_id = self._ctx.id_factory()
        doc = normalize_round(raw, workspace_id=workspace_id,
                              round_id=next_id, seq=seq, now=now, actor=actor)
        self._ctx.save(ROUND, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.round.next",
                              next_id, {"previous": round_id, "seq": seq})
        return doc

    # ------------------------------------------------------------------
    # CQs
    # ------------------------------------------------------------------

    def _round_codes(self, workspace_id: str, round_id: str,
                     exclude: str = "") -> set[str]:
        return {str(row.get("code") or "") for row in
                self._cqs(workspace_id, round_id)
                if str(row.get("id") or "") != exclude}

    def _next_cq_code(self, workspace_id: str, round_id: str) -> str:
        used = self._round_codes(workspace_id, round_id)
        index = 1
        while f"CQ-{index:02d}" in used:
            index += 1
        return f"CQ-{index:02d}"

    def add_cq(self, workspace_id: str, actor: str, round_id: str,
               payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        self._assert_model_refs(
            workspace_id, list(payload.get("model_refs") or []))
        code = str(payload.get("code") or "").strip()
        if code and code in self._round_codes(workspace_id, round_id):
            raise ValidationError(f"CQ code already used: {code!r}")
        now = self._ctx.clock()
        cq_id = self._ctx.id_factory()
        doc = normalize_cq(
            payload, workspace_id=workspace_id, round_id=round_id,
            cq_id=cq_id, now=now,
            default_code=self._next_cq_code(workspace_id, round_id))
        self._ctx.save(CQ, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.cq.create",
                              cq_id, {"round_id": round_id, "code": doc["code"]})
        return doc

    def update_cq(self, workspace_id: str, actor: str, round_id: str,
                  cq_id: str, payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        current = self._ctx.repo.get(CQ, workspace_id, cq_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"CQ not found: {cq_id!r}")
        merged = {**current, **{key: value for key, value in (payload or {}).items()
                                if key in _MUTABLE_CQ}}
        if "model_refs" in (payload or {}):
            self._assert_model_refs(
                workspace_id, list(payload.get("model_refs") or []))
        code = str(merged.get("code") or "").strip()
        if code and code in self._round_codes(workspace_id, round_id,
                                              exclude=cq_id):
            raise ValidationError(f"CQ code already used: {code!r}")
        doc = normalize_cq(merged, workspace_id=workspace_id,
                           round_id=round_id, cq_id=cq_id,
                           now=self._ctx.clock(), default_code=current["code"])
        doc["created_at"] = current.get("created_at") or doc["created_at"]
        self._ctx.save(CQ, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.cq.update",
                              cq_id, {"keys": sorted((payload or {}))})
        return doc

    def delete_cq(self, workspace_id: str, actor: str, round_id: str,
                  cq_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        current = self._ctx.repo.get(CQ, workspace_id, cq_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"CQ not found: {cq_id!r}")
        self._ctx.repo.delete(CQ, workspace_id, cq_id)
        self._ctx.audit_event(actor, workspace_id, "workflow.cq.delete",
                              cq_id, {"round_id": round_id,
                                      "code": current.get("code")})
        return {"deleted": cq_id}

    # ------------------------------------------------------------------
    # CQ import / export / dry run
    # ------------------------------------------------------------------

    def export_cqs(self, workspace_id: str, actor: str, round_id: str,
                   fmt: str = "csv") -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        self._round(workspace_id, round_id)
        return export_cq_set(self._cqs(workspace_id, round_id), fmt)

    def import_cqs(self, workspace_id: str, actor: str, round_id: str,
                   payload: dict) -> dict:
        """Append parsed CQs atomically after validating every row.

        Codes are unique inside a round.  Existing CQs are kept; a caller that
        wants to replace a set exports first and deletes explicitly.  Nothing
        is written unless every imported row normalizes successfully.
        """
        self._ctx.require(actor, workspace_id, "editor")
        payload = payload or {}
        raw_rows = parse_cqs(
            str(payload.get("content") or ""),
            str(payload.get("format") or "auto"))
        if not raw_rows:
            raise ValidationError("import contains no CQ rows")
        now = self._ctx.clock()
        existing = self._round_codes(workspace_id, round_id)
        docs = []
        for index, raw in enumerate(raw_rows, start=1):
            self._assert_model_refs(
                workspace_id, list(raw.get("model_refs") or []))
            code = str(raw.get("code") or "").strip()
            if code and code in existing:
                raise ValidationError(
                    f"imported CQ code already used in this round: {code!r}")
            doc = normalize_cq(
                raw, workspace_id=workspace_id, round_id=round_id,
                cq_id=self._ctx.id_factory(), now=now,
                default_code=self._next_cq_code(workspace_id, round_id))
            if doc["code"] in existing:
                raise ValidationError(
                    f"imported CQ code already used in this round: "
                    f"{doc['code']!r}")
            existing.add(doc["code"])
            docs.append(doc)
        # Only invalidate prior evidence after every row has validated; a
        # rejected import must not make a passing run look stale.
        self._touch_round(workspace_id, round_id)
        for doc in docs:
            self._ctx.save(CQ, doc)
        self._ctx.audit_event(
            actor, workspace_id, "workflow.cq.import", round_id,
            {"count": len(docs), "codes": [doc["code"] for doc in docs]})
        return {"imported": len(docs), "cqs": docs}

    def preview_cq(self, workspace_id: str, actor: str, round_id: str,
                   cq_id: str, payload: dict | None = None
                   ) -> dict:
        """Run one CQ without saving a run (debugging / spreadsheet feedback)."""
        self._ctx.require(actor, workspace_id, "viewer")
        self._round(workspace_id, round_id)
        current = self._ctx.repo.get(CQ, workspace_id, cq_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"CQ not found: {cq_id!r}")
        samples = self._samples(workspace_id, round_id)
        result = self._evaluate_cq(workspace_id, current, samples)
        return {
            "round_id": round_id,
            "cq_id": cq_id,
            "engine": (current.get("query") or {}).get("kind") or "fixture",
            "result": result,
            "sample_ids": sorted({
                part for part in
                str(result.get("sample_ids") or "").split(",") if part}),
        }

    # ------------------------------------------------------------------
    # reuse decisions
    # ------------------------------------------------------------------

    def add_reuse(self, workspace_id: str, actor: str, round_id: str,
                  payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        now = self._ctx.clock()
        doc = normalize_reuse(payload, workspace_id=workspace_id,
                              round_id=round_id,
                              reuse_id=self._ctx.id_factory(), now=now)
        self._ctx.save(REUSE, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.reuse.create",
                              doc["id"], {"decision": doc["decision"]})
        return doc

    def update_reuse(self, workspace_id: str, actor: str, round_id: str,
                     reuse_id: str, payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        current = self._ctx.repo.get(REUSE, workspace_id, reuse_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"reuse record not found: {reuse_id!r}")
        merged = {**current, **(payload or {})}
        doc = normalize_reuse(merged, workspace_id=workspace_id,
                              round_id=round_id, reuse_id=reuse_id,
                              now=self._ctx.clock())
        doc["created_at"] = current.get("created_at") or doc["created_at"]
        self._ctx.save(REUSE, doc)
        return doc

    def delete_reuse(self, workspace_id: str, actor: str, round_id: str,
                     reuse_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        current = self._ctx.repo.get(REUSE, workspace_id, reuse_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"reuse record not found: {reuse_id!r}")
        self._ctx.repo.delete(REUSE, workspace_id, reuse_id)
        return {"deleted": reuse_id}

    # ------------------------------------------------------------------
    # samples
    # ------------------------------------------------------------------

    def add_sample(self, workspace_id: str, actor: str, round_id: str,
                   payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        now = self._ctx.clock()
        doc = normalize_sample(payload, workspace_id=workspace_id,
                               round_id=round_id,
                               sample_id=self._ctx.id_factory(), now=now)
        problems = sample_problems(doc, self._ontology(workspace_id))
        doc["validation"] = {"ok": not problems, "problems": problems}
        self._ctx.save(SAMPLE, doc)
        self._ctx.audit_event(actor, workspace_id, "workflow.sample.create",
                              doc["id"], {"rows": doc["row_count"]})
        return doc

    def update_sample(self, workspace_id: str, actor: str, round_id: str,
                      sample_id: str, payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        current = self._ctx.repo.get(SAMPLE, workspace_id, sample_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"sample not found: {sample_id!r}")
        merged = {**current, **(payload or {})}
        doc = normalize_sample(merged, workspace_id=workspace_id,
                               round_id=round_id, sample_id=sample_id,
                               now=self._ctx.clock())
        problems = sample_problems(doc, self._ontology(workspace_id))
        doc["validation"] = {"ok": not problems, "problems": problems}
        doc["created_at"] = current.get("created_at") or doc["created_at"]
        self._ctx.save(SAMPLE, doc)
        return doc

    def delete_sample(self, workspace_id: str, actor: str, round_id: str,
                      sample_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        self._touch_round(workspace_id, round_id)
        current = self._ctx.repo.get(SAMPLE, workspace_id, sample_id)
        if current is None or current.get("round_id") != round_id:
            raise NotFoundError(f"sample not found: {sample_id!r}")
        self._ctx.repo.delete(SAMPLE, workspace_id, sample_id)
        return {"deleted": sample_id}

    def _sample_template(self, ontology: dict) -> dict:
        objects: dict[str, list[dict]] = {}
        for name, spec in (ontology.get("objects") or {}).items():
            props = spec.get("properties") or {}
            example: dict = {"id": f"{name}-1"}
            for prop, meta in props.items():
                kind = str((meta or {}).get("type") or "string")
                example[prop] = {
                    "string": "示例", "number": 0, "integer": 0,
                    "boolean": True, "datetime": "2026-01-01T00:00:00",
                    "array": [], "object": {},
                }.get(kind, "示例")
            objects[name] = [example]
        return {
            "name": "第一组样例",
            "description": "由当前本体生成的空白样例，替换成业务里真实的几条实例。",
            "objects": objects,
            "relations": [],
        }

    # ------------------------------------------------------------------
    # acceptance
    # ------------------------------------------------------------------

    def _cq_rows(self, workspace_id: str, round_id: str, cq: dict,
                 samples: list[dict]) -> tuple[list[dict], list[str], str]:
        refs = list(cq.get("sample_refs") or [])
        selected = [sample for sample in samples
                    if not refs or sample.get("id") in refs]
        missing = [ref for ref in refs
                   if not any(sample.get("id") == ref for sample in samples)]
        rows: list[dict] = []
        for sample in selected:
            rows.extend(evaluate_query(sample, cq.get("query") or {}))
        return rows, missing, ",".join(str(s.get("id")) for s in selected)

    @staticmethod
    def _cq_engine(cq: dict) -> str:
        return str((cq.get("query") or {}).get("kind") or "fixture")

    def _runtime_rows(self, workspace_id: str, query: dict) -> list[dict]:
        """Read the published runtime state through the same gateway the UI uses."""
        gateway = self._ctx.gateway_for(workspace_id)
        relation = str(query.get("relation") or "")
        if relation:
            ref = str(query.get("ref") or "")
            if not ref:
                raise ValidationError(
                    "runtime relation query needs 'ref' such as Vehicle/V001")
            direction = str(query.get("direction") or "out")
            rows: list[dict] = []
            for item in gateway.related(ref, relation, direction):
                obj = gateway.get_object(item)
                if obj is None:
                    rows.append({"id": str(item).rsplit("/", 1)[-1],
                                 "ref": str(item)})
                else:
                    rows.append({**obj, "ref": item})
            return rows
        object_type = str(query.get("object_type") or "")
        if not object_type:
            raise ValidationError("runtime query needs object_type or relation")
        objects = list(gateway.find_objects(object_type) or [])
        # Reuse the fixture evaluator for filters/limit so both engines share
        # one matching contract.  Runtime rows already carry properties.
        return evaluate_query(
            {"objects": {object_type: objects}, "relations": []},
            {"kind": "fixture", "object_type": object_type,
             "filters": query.get("filters") or {},
             "limit": int(query.get("limit") or 0)})

    def _evaluate_cq(self, workspace_id: str, cq: dict,
                     samples: list[dict]) -> dict:
        """Evaluate one CQ against its configured engine; never raises."""
        expected = cq.get("expected") or {}
        engine = self._cq_engine(cq)
        used = ""
        if expected.get("kind") == "manual":
            status, actual, detail = evaluate_expectation(expected, [])
        elif engine == "runtime":
            try:
                rows = self._runtime_rows(workspace_id, cq.get("query") or {})
                status, actual, detail = evaluate_expectation(expected, rows)
            except Exception as exc:
                status, actual, detail = "error", {"rows": []}, str(exc)
        elif not samples:
            status, actual, detail = (
                "error", {"rows": []},
                "本轮还没有样例实例；先添加样例集，或把 CQ 查询切到运行时")
        else:
            try:
                rows, missing, used = self._cq_rows(
                    workspace_id, cq.get("round_id") or "", cq, samples)
                if missing:
                    status, actual, detail = (
                        "error", {"rows": rows},
                        "找不到 CQ 引用的样例：" + ", ".join(missing))
                else:
                    status, actual, detail = evaluate_expectation(
                        expected, rows)
            except Exception as exc:
                status, actual, detail = "error", {"rows": []}, str(exc)
        return {
            "cq_id": cq.get("id"),
            "code": cq.get("code"),
            "question": cq.get("question"),
            "priority": cq.get("priority"),
            "type": cq.get("type"),
            "expected": expected,
            "query": cq.get("query") or {},
            "engine": engine,
            "sample_ids": used,
            "model_refs": cq.get("model_refs") or [],
            "status": status,
            "actual": actual,
            "detail": detail,
        }

    def run_acceptance(self, workspace_id: str, actor: str,
                       round_id: str, payload: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        payload = payload or {}
        round_doc = self._round(workspace_id, round_id)
        if round_doc.get("status") == "accepted":
            raise ValidationError("accepted rounds do not need another run")
        ontology = self._ontology(workspace_id)
        fingerprint = ontology_fingerprint(ontology)
        cqs = self._cqs(workspace_id, round_id)
        if not cqs:
            raise ValidationError("write at least one CQ before running acceptance")
        samples = self._samples(workspace_id, round_id)
        results: list[dict] = []
        for cq in cqs:
            results.append(self._evaluate_cq(workspace_id, cq, samples))
        summary = acceptance_summary(results, round_doc.get("target"))
        counts = summary
        status = "passed" if (
            counts["failed"] == 0 and counts["error"] == 0
            and counts["automated"] > 0 and counts["meets_target"]) else "failed"
        used_samples = sorted({
            part for result in results
            for part in str(result.get("sample_ids") or "").split(",") if part})
        used = [sample for sample in samples if sample.get("id") in used_samples]
        engines = {str(result.get("engine") or "fixture") for result in results}
        engine = ("mixed" if len(engines) > 1 else
                  (engines.pop() if engines else "fixture"))
        evidence = {
            "ontology_id": ontology.get("id"),
            "ontology_version": int(ontology.get("version") or 0),
            "ontology_fingerprint": fingerprint,
            "sample_ids": used_samples,
            "sample_hash": canonical_hash(
                {sample.get("id"): sample_fingerprint(sample)
                 for sample in used}),
            "query_hash": canonical_hash([
                {"code": cq.get("code"), "query": cq.get("query"),
                 "expected": cq.get("expected")} for cq in cqs]),
        }
        if payload.get("note"):
            evidence["note"] = str(payload.get("note"))[:2000]
        now = self._ctx.clock()
        run = {
            "id": self._ctx.id_factory(),
            "workspace_id": workspace_id,
            "round_id": round_id,
            "kind": "run",
            "seq": max((int(row.get("seq") or 0)
                        for row in self._runs(workspace_id, round_id)),
                       default=0) + 1,
            "status": status,
            "engine": engine,
            "started_at": now,
            "finished_at": now,
            "created_by": actor,
            "ontology_fingerprint": fingerprint,
            "ontology_version": int(ontology.get("version") or 0),
            "summary": summary,
            "results": results,
            "evidence": evidence,
        }
        self._ctx.save(RUN, run)
        self._ctx.audit_event(
            actor, workspace_id, "workflow.acceptance.run", run["id"],
            {"round_id": round_id, "status": status,
             "pass_rate": summary["pass_rate"],
             "p0_pass_rate": summary["p0_pass_rate"]})
        return run


    # ------------------------------------------------------------------
    # review / publish
    # ------------------------------------------------------------------

    def _latest_run(self, workspace_id: str, round_id: str) -> dict | None:
        runs = self._runs(workspace_id, round_id)
        return runs[0] if runs else None

    def _publish_check(self, workspace_id: str, round_doc: dict,
                       cqs: list[dict], reuse: list[dict],
                       samples: list[dict], runs: list[dict],
                       reviews: list[dict], ontology: dict,
                       *, require_review: bool = True) -> dict:
        fingerprint = ontology_fingerprint(ontology)
        validation = round_doc.get("validation") or {}
        target = {**round_doc.get("target", {}), "min_cq": 5, "max_cq": 10,
                  "p0_pass_rate": 100, "overall_pass_rate": 80}
        target.update(round_doc.get("target") or {})
        blockers: list[str] = []
        warnings: list[str] = []
        if not cqs:
            blockers.append("没有任何 CQ：本轮没有可验证的目标")
        elif len(cqs) < int(target["min_cq"]):
            blockers.append(
                f"CQ 数量 {len(cqs)} 少于本轮目标 {target['min_cq']}")
        elif len(cqs) > int(target["max_cq"]):
            warnings.append(
                f"CQ 数量 {len(cqs)} 超过本轮目标 {target['max_cq']}，建议拆到下一轮")
        if not any(row.get("priority") == "P0" for row in cqs):
            warnings.append("没有 P0 CQ：验收无法区分关键问题")
        if not validation:
            blockers.append("还没有运行发布前语义检查")
        elif validation.get("ontology_fingerprint") != fingerprint:
            blockers.append("语义检查结果已过期：本体在此之后改过")
        elif not validation.get("ok"):
            blockers.append("语义检查未通过：" + "；".join(
                validation.get("problems") or []))
        if not reuse and not round_doc.get("reuse_skipped"):
            warnings.append("还没有复用调研；可记录一条，或显式标记本轮不复用")
        fixture_cqs = [
            cq for cq in cqs
            if self._cq_engine(cq) != "runtime"
            and (cq.get("expected") or {}).get("kind") != "manual"]
        if not samples and fixture_cqs:
            blockers.append("没有样例实例：fixture CQ 无法验证")
        elif not samples and not fixture_cqs:
            warnings.append("本轮 CQ 不读样例；验收只使用运行时或人工结果")
        for sample in samples:
            problems = sample_problems(sample, ontology)
            if problems:
                warnings.append(
                    f"样例「{sample.get('name') or sample.get('id')}」与本体不一致："
                    + "；".join(problems[:3]))
        latest_run = runs[0] if runs else None
        if latest_run is None:
            blockers.append("还没有运行 CQ 验证")
        else:
            if latest_run.get("ontology_fingerprint") != fingerprint:
                blockers.append("最近一次验证的证据已过期：本体在此之后改过")
            evidence = latest_run.get("evidence") or {}
            sample_ids = set(evidence.get("sample_ids") or [])
            current_samples = [sample for sample in samples
                               if sample.get("id") in sample_ids]
            current_sample_hash = canonical_hash(
                {sample.get("id"): sample_fingerprint(sample)
                 for sample in current_samples})
            if evidence.get("sample_hash") != current_sample_hash:
                blockers.append("最近一次验证使用的样例已变更，需要重新运行")
            current_query_hash = canonical_hash([
                {"code": cq.get("code"), "query": cq.get("query"),
                 "expected": cq.get("expected")} for cq in cqs])
            if evidence.get("query_hash") != current_query_hash:
                blockers.append("CQ 查询或期望已变更，需要重新运行验证")
            if latest_run.get("status") != "passed":
                blockers.append("最近一次 CQ 验证未通过")
            summary = latest_run.get("summary") or {}
            if summary.get("pass_rate", 0) < float(target["overall_pass_rate"]):
                blockers.append(
                    f"整体通过率 {summary.get('pass_rate', 0)}% 低于目标 "
                    f"{target['overall_pass_rate']}%")
            if summary.get("p0_pass_rate", 0) < float(target["p0_pass_rate"]):
                blockers.append(
                    f"P0 通过率 {summary.get('p0_pass_rate', 0)}% 低于目标 "
                    f"{target['p0_pass_rate']}%")
            for result in latest_run.get("results") or []:
                if result.get("priority") == "P0" and \
                        result.get("status") not in ("passed", "manual"):
                    blockers.append(
                        f"P0 问题 {result.get('code')} 未通过："
                        f"{result.get('detail') or ''}")
        latest_review = reviews[0] if reviews else None
        if require_review:
            if latest_review is None:
                blockers.append("还没有评审通过记录")
            elif latest_review.get("decision") != "approve":
                blockers.append("最近一次评审未通过：需要处理意见后重新提交")
            elif latest_review.get("ontology_fingerprint") != fingerprint:
                blockers.append("评审记录对应的本体已变更，需要重新评审")
        if latest_run and latest_review and \
                latest_review.get("decision") == "approve" and \
                latest_review.get("evidence_run_id") != latest_run.get("id"):
            message = "评审之后又跑过新的验证；请重新确认评审结论"
            if require_review:
                blockers.append(message)
            else:
                warnings.append(message)
        if round_doc.get("ontology_id") and \
                round_doc.get("ontology_id") != ontology.get("id"):
            blockers.append("本轮绑定的是旧的修订版本，请先「同步当前本体」")
        if round_doc.get("status") != "accepted" and \
                ontology.get("lifecycle") == "published":
            blockers.append("本体已发布，需要修订出新草稿才能发布本轮")
        if latest_review and latest_review.get("decision") == "approve" and \
                latest_review.get("actor") == round_doc.get("created_by"):
            warnings.append("评审人与本轮创建人相同；建议由另一位同事复核")
        manual = [row.get("code") for row in cqs
                  if (row.get("expected") or {}).get("kind") == "manual"]
        if manual:
            warnings.append(
                "人工 CQ 不进入自动通过率：" + "、".join(manual))
        return {
            "ok": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "ontology_fingerprint": fingerprint,
            "validation": validation,
            "latest_run_id": (latest_run or {}).get("id"),
            "latest_review_id": (latest_review or {}).get("id"),
            "step": _step_label(blockers, warnings),
        }

    def check(self, workspace_id: str, actor: str, round_id: str) -> dict:
        """Validate the ontology and persist the result on the round.

        This is the explicit step-5 action; a GET must not silently mutate a
        round, so publish-check reads this stored result and reports staleness.
        """
        self._ctx.require(actor, workspace_id, "editor")
        round_doc = self._round(workspace_id, round_id)
        if round_doc.get("status") == "accepted":
            raise ValidationError("accepted rounds cannot be re-checked")
        ontology = self._ontology(workspace_id)
        validation = self._validation(workspace_id, ontology)
        round_doc["validation"] = validation
        round_doc["ontology_id"] = ontology.get("id")
        round_doc["ontology_version"] = int(ontology.get("version") or 0)
        round_doc["ontology_fingerprint"] = validation["ontology_fingerprint"]
        round_doc["updated_at"] = self._ctx.clock()
        self._ctx.save(ROUND, round_doc)
        self._ctx.audit_event(
            actor, workspace_id, "workflow.publish_check", round_id,
            {"ok": validation["ok"], "problems": validation["problems"]})
        return {
            "round": round_doc,
            "validation": validation,
            "publish_check": self._publish_check(
                workspace_id, round_doc, self._cqs(workspace_id, round_id),
                self._reuse(workspace_id, round_id),
                self._samples(workspace_id, round_id),
                self._runs(workspace_id, round_id),
                self._reviews(workspace_id, round_id), ontology),
        }

    def publish_check(self, workspace_id: str, actor: str,
                      round_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        round_doc = self._round(workspace_id, round_id)
        return self._publish_check(
            workspace_id, round_doc, self._cqs(workspace_id, round_id),
            self._reuse(workspace_id, round_id),
            self._samples(workspace_id, round_id),
            self._runs(workspace_id, round_id),
            self._reviews(workspace_id, round_id), self._ontology(workspace_id))

    def submit_review(self, workspace_id: str, actor: str, round_id: str,
                      payload: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        round_doc = self._round(workspace_id, round_id)
        if round_doc.get("status") == "accepted":
            raise ValidationError("accepted rounds cannot be reviewed again")
        decision = str(payload.get("decision") or "approve").strip()
        if decision not in ("approve", "reject", "changes_requested"):
            raise ValidationError(
                "review decision must be approve|reject|changes_requested")
        ontology = self._ontology(workspace_id)
        fingerprint = ontology_fingerprint(ontology)
        latest_run = self._latest_run(workspace_id, round_id)
        check = self._publish_check(
            workspace_id, round_doc, self._cqs(workspace_id, round_id),
            self._reuse(workspace_id, round_id),
            self._samples(workspace_id, round_id),
            self._runs(workspace_id, round_id),
            self._reviews(workspace_id, round_id), ontology,
            require_review=False)
        if decision == "approve":
            if latest_run is None:
                raise GateError("cannot approve: no acceptance run yet")
            if latest_run.get("ontology_fingerprint") != fingerprint:
                raise GateError(
                    "cannot approve: evidence is stale (ontology changed)")
            if latest_run.get("status") != "passed":
                raise GateError("cannot approve: latest acceptance run failed")
            if not check["ok"]:
                # Warnings do not block, but hard blockers do.
                raise GateError(
                    "cannot approve before the publish gate is clear: "
                    + "；".join(check["blockers"]))
        now = self._ctx.clock()
        review = {
            "id": self._ctx.id_factory(),
            "workspace_id": workspace_id,
            "round_id": round_id,
            "kind": "review",
            "seq": max((int(row.get("seq") or 0)
                        for row in self._reviews(workspace_id, round_id)),
                       default=0) + 1,
            "decision": decision,
            "comment": str(payload.get("comment") or "")[:4000],
            "actor": actor,
            "ontology_fingerprint": fingerprint,
            "ontology_version": int(ontology.get("version") or 0),
            "evidence_run_id": (latest_run or {}).get("id"),
            "created_at": now,
        }
        self._ctx.save(REVIEW, review)
        round_doc["status"] = "in_review" if decision == "approve" else "active"
        round_doc["updated_at"] = now
        self._ctx.save(ROUND, round_doc)
        self._ctx.audit_event(
            actor, workspace_id, "workflow.review.submit", review["id"],
            {"round_id": round_id, "decision": decision,
             "run": review["evidence_run_id"]})
        return review

    def publish(self, workspace_id: str, actor: str, round_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        check = self.publish_check(workspace_id, actor, round_id)
        if not check["ok"]:
            raise GateError(
                "publish blocked: " + "；".join(check["blockers"]))
        result = self._publish_ontology(workspace_id, actor)
        round_doc = self._round(workspace_id, round_id)
        ontology = result.get("ontology") or {}
        round_doc.update({
            "status": "accepted",
            "published_version": int(ontology.get("version") or 0),
            "published_at": self._ctx.clock(),
            "updated_at": self._ctx.clock(),
            "ontology_id": ontology.get("id") or round_doc.get("ontology_id"),
            "ontology_version": int(ontology.get("version") or 0),
            "ontology_fingerprint": ontology_fingerprint(ontology),
        })
        self._ctx.save(ROUND, round_doc)
        self._ctx.audit_event(
            actor, workspace_id, "workflow.round.publish", round_id,
            {"version": round_doc["published_version"],
             "run": check["latest_run_id"]})
        return {
            "round": round_doc,
            "publish_check": check,
            "ontology": ontology,
            "runtime": result.get("runtime"),
        }

    # ------------------------------------------------------------------
    # traceability / report
    # ------------------------------------------------------------------

    def _trace_matrix(self, cqs: list[dict],
                      runs: list[dict]) -> list[dict]:
        latest = runs[0] if runs else None
        results = {row.get("cq_id"): row for row in
                   ((latest or {}).get("results") or [])}
        rows = []
        for cq in cqs:
            result = results.get(cq.get("id")) or {}
            rows.append({
                "cq_id": cq.get("id"),
                "code": cq.get("code"),
                "question": cq.get("question"),
                "type": cq.get("type"),
                "priority": cq.get("priority"),
                "model_refs": cq.get("model_refs") or [],
                "sample_refs": cq.get("sample_refs") or [],
                "query": cq.get("query") or {},
                "expected": cq.get("expected") or {},
                "status": result.get("status") or "open",
                "detail": result.get("detail") or "",
                "actual": result.get("actual") or {},
            })
        return rows

    def trace_matrix(self, workspace_id: str, actor: str,
                     round_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        self._round(workspace_id, round_id)
        cqs = self._cqs(workspace_id, round_id)
        runs = self._runs(workspace_id, round_id)
        return {
            "round_id": round_id,
            "rows": self._trace_matrix(cqs, runs),
            "latest_run_id": (runs[0] if runs else {}).get("id"),
        }

    def report(self, workspace_id: str, actor: str, round_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        detail = self.detail(workspace_id, actor, round_id)
        round_doc = detail["round"]
        lines = [
            f"# 本体工程流报告 · {round_doc.get('name')}",
            "",
            f"- 轮次：第 {round_doc.get('seq')} 轮（{round_doc.get('status')}）",
            f"- 目标：{round_doc.get('goal') or '—'}",
            f"- 本体：v{detail['ontology']['version']} · "
            f"fingerprint `{detail['ontology']['fingerprint']}`",
            "",
            "## 八步进度",
            "",
        ]
        for step in detail["steps"]:
            lines.append(f"- [{'x' if step['status'] == 'done' else ' '}] "
                         f"{step['no']}. {step['title']}：{step['detail']}")
        lines += ["", "## CQ 追踪矩阵", "",
                  "| CQ | 优先级 | 问题 | 模型引用 | 样例 | 结果 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for row in detail["trace_matrix"]:
            lines.append(
                f"| {row['code']} | {row['priority']} | {row['question']} | "
                f"{', '.join(row['model_refs']) or '—'} | "
                f"{', '.join(row['sample_refs']) or '全部'} | {row['status']} |")
        lines += ["", "## 验收证据", ""]
        latest = detail["runs"][0] if detail["runs"] else None
        if latest:
            summary = latest.get("summary") or {}
            lines += [
                f"- 最近运行：`{latest.get('id')}` · {latest.get('status')}",
                f"- 通过率：{summary.get('pass_rate', 0)}% · P0 "
                f"{summary.get('p0_pass_rate', 0)}%",
                f"- 本体指纹：`{latest.get('ontology_fingerprint')}`",
                f"- 样例哈希：`{(latest.get('evidence') or {}).get('sample_hash')}`",
            ]
        else:
            lines.append("- 还没有运行")
        lines += ["", "## 发布门禁", ""]
        check = detail["publish_check"]
        lines.append(f"- 结论：{'可发布' if check['ok'] else '不可发布'}")
        for blocker in check["blockers"]:
            lines.append(f"- 阻塞：{blocker}")
        for warning in check["warnings"]:
            lines.append(f"- 提醒：{warning}")
        return {
            "round": round_doc,
            "markdown": "\n".join(lines),
            "steps": detail["steps"],
            "trace_matrix": detail["trace_matrix"],
            "publish_check": check,
        }


def _step_label(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return "blocked"
    if warnings:
        return "warning"
    return "ready"
