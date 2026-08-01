import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { EvalDrawer } from "./EvalDrawer";

const pendingCandidate = {
  schema_version: "eval-inbox-candidate.v1",
  candidate_id: "blind:sample-1:hash",
  source_kind: "blind_grading_label",
  dedupe_key: "sample-1",
  payload_schema_version: "grading-calibration-sample.v1",
  payload_hash: "payload-hash",
  payload: {
    schema_version: "grading-calibration-sample.v1",
    sample_id: "sample-1",
    annotator: "owner",
    blind_to_model_output: true,
    question: {},
    learner_answer: "answer text",
    human_verdict: "对",
    human_matched_points: [],
    human_missing_points: [],
  },
  lifecycle_status: "active",
  review_status: "pending",
  release_gate_eligible: true,
  privacy_review_required: true,
  review_request_id: null,
  review_reason: null,
  reviewed_at: null,
  created_at: 1,
};

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("requires explicit privacy review before promoting an immutable snapshot", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const request = input instanceof Request ? input : new Request(String(input));
    if (request.url.endsWith("/api/v1/eval/candidates") && request.method === "GET") {
      return Response.json({ items: [pendingCandidate] });
    }
    if (request.url.includes("/api/v1/eval/snapshots?") && request.method === "GET") {
      return Response.json({ items: [] });
    }
    if (request.url.includes("/review") && request.method === "POST") {
      return Response.json({
        ...pendingCandidate,
        review_status: "approved",
        review_request_id: "review-1",
        review_reason: "隐私检查通过",
        reviewed_at: 2,
      });
    }
    if (request.url.endsWith("/api/v1/eval/snapshots") && request.method === "POST") {
      expect(await request.json()).toEqual({
        candidate_ids: [pendingCandidate.candidate_id],
      });
      return Response.json(
        {
          schema_version: "eval-dataset-snapshot.v1",
          snapshot_id: "content-hash",
          content_sha256: "content-hash",
          redaction_profile: "learning-facts.v1",
          candidate_count: 1,
          eligible_blind_count: 1,
          exploratory_count: 0,
          items: [],
          created_at: 3,
        },
        { status: 201 },
      );
    }
    throw new Error(`Unexpected request: ${request.method} ${request.url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();

  const { container } = render(<EvalDrawer open onClose={() => undefined} />);

  expect(await screen.findByText("盲标样本 · sample-1")).toBeInTheDocument();
  expect(container.querySelector("details")).not.toHaveAttribute("open");
  expect(screen.getByRole("button", { name: "生成快照" })).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "隐私检查通过" }));
  await user.click(screen.getByRole("button", { name: "生成快照" }));

  expect(await screen.findByText("快照已固定")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
});
