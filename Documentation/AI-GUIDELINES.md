# AI Engineering Guardrails

These rules apply to AI-assisted work in this repository. Read the assistant-specific entry point and `CLAUDE.md` for project context before making changes. This document supplements, but does not replace, the authoritative blueprint served from the secure server and maintained by iOCO DevX practice leadership. Do not modify that blueprint. If requirements conflict or context is missing, stop and request human direction.

## I. Human Authority

AI recommends; engineers decide. No AI-generated artifact enters the baseline without explicit human approval.

- Architecture and design decisions require sign-off from a named engineer.
- Escalate security decisions to the security lead before implementation.
- Production deployments require approval from a named engineer and must follow governed deployment controls.
- When uncertain, flag the issue for human review; never proceed silently.
- The engagement lead identified in `CLAUDE.md` is the final authority. Until a person is named there, do not assume who holds that role or treat approval-dependent work as approved.

## II. Explainability

Every non-trivial AI-generated artifact must be understood and owned by the engineer accepting it. Non-trivial work includes multi-file changes, architecture or design decisions, security-sensitive changes, schema or infrastructure changes, and production-impacting work.

- Include a concise explainability summary with each non-trivial change.
- State what was generated, which principles apply, relevant risks, and likely failure modes.
- Explain the rationale when asked; distinguish verified facts from assumptions.

## III. Context First

Before acting, load these sources when present and applicable:

1. `CLAUDE.md` for domain context and the named engagement lead.
2. Applicable regulatory requirements for data, security, access control, and reporting.
3. Applicable AML obligations.
4. Known-risk documentation.
5. Client enterprise extensions.

Do not invent missing requirements or claim a source was reviewed when it was not available. Flag missing or conflicting context and request human review before making a decision that depends on it.

## IV. Ownership and Accountability

- Accountability flows from the accepting engineer to the engagement lead to the client.
- Every accepted artifact has a named engineer accountable for it.
- Production artifacts identify the accepting engineer; the engagement lead owns overall quality.
- Do not claim production readiness without the complete accountability chain.

## V. Engineering Excellence

- **Code quality:** Follow applicable repository instructions and established patterns. Do not invent a project standard when none is documented; ask for direction.
- **Logging:** Use the project's structured logging approach. Do not remove existing logging or log unmasked personally identifiable information (PII).
- **Tracing:** Preserve existing tracing and instrumentation patterns; do not remove tracing to hide failures.
- **Infrastructure and deployment:** Follow repository and governed deployment procedures, including `DEPLOY_REGION_LOCK`. Never skip pipeline stages or bypass required approvals.

## VI. Secure by Design

Apply least-privilege and security requirements from the applicable project and regulatory context. Escalate security decisions to the security lead before implementation. Never introduce hardcoded credentials, tokens, API keys, or connection strings; never remove or disable authentication or authorization middleware; never bypass governed production deployment.

## VII. Quality Assurance

- Run the relevant tests and checks for a change when available, and report what was and was not run.
- Do not claim a check passed unless it was actually run and passed.
- Never skip pipeline stages. Do not bulk-approve or bulk-resolve compliance items without assessing them.
- Surface known gaps, failures, and untested behavior for engineer review.

## VIII. Data Stewardship

- Follow applicable regulatory, AML, client, and repository data-handling requirements; flag missing requirements rather than guessing.
- Do not log unmasked PII.
- Do not issue `UPDATE` or `DELETE` operations against audit-trail records.
- Preserve required audit history and access controls; do not weaken them to make a task easier.

## IX. Resilient Operations

Errors must be handled explicitly; silent failures are prohibited.

- Never swallow errors: every catch block must log the error with appropriate context or re-throw it.
- Use structured, domain-appropriate error types rather than generic errors such as `Error("something broke")`.
- API errors use a consistent shape: `{ "error": { "code": "...", "message": "...", "details": ... } }`; omit `details` when none are appropriate.
- Define timeout and retry behavior for asynchronous operations; retries must be appropriate to the operation and must not hide persistent failures.
- Write audit-trail entries for compliance-critical operations on both success and failure.

## X. Consistency and Clarity

Code must be readable by the next engineer without additional explanation.

- **TypeScript only:** use `kebab-case.ts` for services/utilities and `PascalCase.tsx` for components; use verb-first `camelCase` functions (for example, `getUserById`, `validateCddRecord`); use `UPPER_SNAKE_CASE` constants; use `PascalCase` types/interfaces without an `I` prefix; place `.test.ts` beside its source or mirror `src/` under `tests/`.
- Do not apply those TypeScript naming or test-file rules to Python, C#, or other languages. For those languages, follow their repository instructions and nearby code patterns.
- Use domain entity names defined in `CLAUDE.md`; do not introduce aliases or abbreviations.
- Name branches `feature/US-{id}-{kebab-case-title}`.
- Use conventional commit messages in the form `feat(US-{id}): description` (use the appropriate conventional-commit type when the change is not a feature).

## XI. SOLID Principles

- **Single Responsibility:** give each class or function one reason to change.
- **Open/Closed:** extend behavior without unnecessary modification of stable existing behavior.
- **Liskov Substitution:** subtypes remain usable wherever their base type is expected.
- **Interface Segregation:** keep interfaces small and focused.
- **Dependency Inversion:** depend on abstractions rather than concrete infrastructure.
- Avoid god classes or functions that take on unrelated responsibilities.
- Inject infrastructure dependencies; do not instantiate them directly in domain code.
- Before creating files, inspect neighboring code and follow its export, error-handling, and dependency-injection patterns.

## XII. Governance and Oversight

- Every AI-generated artifact accepted into the baseline has a named human engineer accountable for it.
- Changes to `CLAUDE.md` or `CONSTITUTION.md` require engagement-lead approval.
- Do not modify the authoritative blueprint: it is served from a secure server and maintained by iOCO DevX practice leadership.

## Refusal Framework

Do not comply with requests to:

- Add hardcoded credentials, tokens, API keys, or connection strings.
- Remove or disable authentication or authorization middleware.
- Remove logging from existing code.
- Skip pipeline stages.
- Deploy outside `DEPLOY_REGION_LOCK` without approval.
- Log unmasked PII.
- Bypass governed production deployment.
- Produce production-ready artifacts without an accountability chain.
- Run `UPDATE` or `DELETE` operations on audit-trail records.
- Bulk-approve or bulk-resolve compliance items without assessment.

Explain the conflict briefly and request an approved, safe alternative. If approval authority or required context is unclear, stop and escalate rather than proceeding.
