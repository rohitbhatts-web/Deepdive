"""Structured inputs/outputs for the Phase 2 agents. `rationale`/`reason` fields are short stated
justifications for a decision, not reasoning transcripts."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SubQuestionPlan(BaseModel):
    question: str = Field(min_length=5, max_length=400)
    evidence_requirements: list[str] = Field(
        min_length=1, max_length=5, description="Concrete facts/evidence needed to answer the subquestion"
    )
    rationale: str = Field(max_length=300, description="One sentence: why this subquestion matters")
    covers: list[int] = Field(min_length=1, max_length=6, description="0-based positions in `asks` of the asks this subquestion helps answer")


class Ask(BaseModel):
    quote: str = Field(min_length=3, max_length=300, description="The exact words of the question that state this ask")
    label: str = Field(max_length=120, description="Short name of what is being asked for")


class Plan(BaseModel):
    asks: list[Ask] = Field(min_length=1, max_length=8, description="One entry per distinct thing the question asks for")
    subquestions: list[SubQuestionPlan] = Field(min_length=1)


class QueryPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=5, description="Web search queries, each 3-200 chars")
    rationale: str = Field(max_length=300)


class UrlChoice(BaseModel):
    url: str
    reason: str = Field(max_length=300, description="One sentence: what evidence this page likely holds")


class UrlSelection(BaseModel):
    selections: list[UrlChoice]


class Assessment(BaseModel):
    candidate: str = Field(description="The candidate id exactly as given, e.g. c1")
    relevant: bool
    passage_ids: list[str] = Field(description="IDs of the passages (from that candidate only) that bear on the subquestion")
    explanation: str = Field(max_length=300, description="One sentence: what the passages say that bears on the subquestion")


class RelevanceAssessment(BaseModel):
    assessments: list[Assessment]


# --- Phase 3 ---------------------------------------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    passage_id: str = Field(description="ID of a passage shown to you, e.g. S1-P3")
    quote: str = Field(max_length=600, description="Verbatim contiguous text copied from that passage")


class DraftedClaim(BaseModel):
    subquestion_id: int
    text: str = Field(min_length=10, max_length=400, description="One atomic, independently checkable assertion")
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=4)


class ClaimDraft(BaseModel):
    claims: list[DraftedClaim]


Aspect = Literal["ok", "not_applicable", "mismatch", "missing"]


class Aspects(BaseModel):
    date: Aspect = Field(description="dates/time periods in the claim vs the evidence")
    quantity: Aspect = Field(description="numbers, amounts, percentages, counts")
    entity: Aspect = Field(description="who/what the claim is about")
    attribution: Aspect = Field(description=(
        "who holds the view. If the evidence is a source's recommendation, opinion, judgement ('ideal', 'best', "
        "'should'), marketing claim or forecast, a claim stating it as plain fact is a mismatch; the same content "
        "attributed to the source ('the article says ...') is ok; not_applicable for plain descriptions of how "
        "something is or works"))
    scope: Aspect = Field(description="breadth: all/most/some, conditions, versions, places")
    causality: Aspect = Field(description="causal or comparative wording (because, leads to, better than)")
    forecast_vs_observation: Aspect = Field(description="a prediction/opinion in the evidence stated as fact in the claim is a mismatch")
    qualifications: Aspect = Field(description="hedges and caveats (may, usually, only if) in the evidence")


class VerifierVerdict(BaseModel):
    verdict: Literal["supported", "partially_supported", "unsupported", "contradicted"]
    # A free-text reason, not the verdict. It asks for brevity but tolerates an overrun: Run 2's two validation retries (two of its 20 requests) were valid
    # verdicts rejected only because this text exceeded a hard 500-character limit. The verdict, evidence and aspect fields stay exactly as strict.
    explanation: str = Field(max_length=1000, description="Brief reason, under 500 characters; name the passages relied on")
    unsupported_details: list[str] = Field(description="Parts of the claim the evidence does not establish")
    conflicts: list[str] = Field(description="Ways the evidence contradicts the claim")
    evidence_used: list[str] = Field(description="Passage IDs (from those shown) you relied on")
    aspects: Aspects


# --- Phase 4 ---------------------------------------------------------------------------------------------------------


class ObligationItem(BaseModel):
    subject: str = Field(max_length=80, description="One subject the requirement asks about, worded as in the question")
    dimension: str = Field(max_length=120, description="The aspect asked about that subject, e.g. an attribute or behaviour")


class RequirementObligations(BaseModel):
    requirement_key: str = Field(description="The requirement key exactly as given, e.g. Q1.R2")
    obligations: list[ObligationItem]


class ObligationPlan(BaseModel):
    requirements: list[RequirementObligations]


class ClaimRole(BaseModel):
    claim_id: int = Field(description="ID of a verified claim shown to you")
    role: Literal["direct_answer", "relevant_context"] = Field(
        description="direct_answer: the claim itself states what the obligation asks for. relevant_context: related, but it does not state what is asked")
    reason: str = Field(max_length=200, description="Under 160 characters: why this role")


class ObligationAssessment(BaseModel):
    obligation_key: str = Field(description="The obligation key exactly as given, e.g. Q1.R2.O1")
    status: Literal["addressed", "partially_addressed", "unresolved"]
    claim_roles: list[ClaimRole] = Field(description="Every shown claim that bears on this obligation, with its role; unrelated claims are not listed")
    gap: str = Field(max_length=400, description="For anything not fully addressed: exactly what is missing; else empty")
    missing_kind: Literal["none", "date", "quantity", "primary_documentation", "contradiction", "detail", "other"]


class CoverageAssessment(BaseModel):
    obligations: list[ObligationAssessment]


class RevisionPart(BaseModel):
    text: str = Field(max_length=400, description="One atomic claim")
    evidence: list[EvidenceRef]


class Revision(BaseModel):
    action: Literal["revise", "split", "abandon"]
    text: str = Field(max_length=400, description="The replacement claim when revising (empty otherwise)")
    evidence: list[EvidenceRef] = Field(description="Passage IDs and verbatim quotes when revising (empty otherwise)")
    parts: list[RevisionPart] = Field(description="2-4 separate atomic claims when splitting a compound claim (empty otherwise)")
    reason: str = Field(max_length=300, description="What you changed and why, or why no accurate claim is possible")

    @model_validator(mode="after")
    def _action_needs_its_payload(self):
        if self.action == "revise" and (len(self.text.strip()) < 10 or not self.evidence):
            raise ValueError("a revision needs claim text and at least one evidence item")
        if self.action == "split" and not (2 <= len(self.parts) <= 4 and all(len(p.text.strip()) >= 10 and p.evidence for p in self.parts)):
            raise ValueError("a split needs 2-4 parts, each with claim text and at least one evidence item")
        return self


class SectionSelection(BaseModel):
    subquestion_id: int
    claim_ids: list[int] = Field(description="Claim IDs for this subquestion, in the order they should appear")


class OmittedClaim(BaseModel):
    claim_id: int
    reason: str = Field(max_length=200)


class SynthesisPlan(BaseModel):
    sections: list[SectionSelection]
    omitted: list[OmittedClaim]
