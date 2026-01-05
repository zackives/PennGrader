# Backend API Specification (Autograder)

Authoritative summary of the backend Lambda endpoints for client library development. Aligned with `SECURITY_MODEL.md` and `client_example.py`.

## Core Principles
- **Single-use tokens**: Every grading interaction is gated by HMAC-SHA256 tokens issued by `token_generator`. Tokens are single-use, expire after 1 hour, and are tied to `student_id` + `test_case` (use `homework_id` as `test_case` for grade retrieval).
- **Rate limiting**: Token generation is limited to **3 requests/minute per student**.
- **Two-token flow**: Clients request two tokens per submission: `token1` (fetch test) and `token2` (save grade). Grade retrieval also requires two tokens tied to `homework_id` + `student_id`.
- **Environment-driven secrets**: `SYSTEM_SALT` is provided via Lambda env var (not config files). Course secret is retrieved from DynamoDB `Classes` table via `course_id` filter and `secret_key` value.
- **HTTP Methods**: 
  - `token_generator`: **GET** (recommended) or POST - for retrieving tokens without side effects
  - All other endpoints: **POST** - for submitting data and modifying state

## Endpoints Overview

### 1) token_generator (Lambda)
Generates a pair of single-use tokens for a student & test case.

- **HTTP Method**: `GET` (recommended) or `POST`
- **API Gateway Path**: typically `/token_generator`
- **Headers**: `x-api-key: <api_key>` (required if API Gateway API key requirement is enabled)
- **Query Parameters (for GET)** or **Request Body (JSON for POST)**:
  - `student_id` (string, required)
  - `student_secret` (string, required) — typically the student’s ID/secret
  - `test_case` (string, required) — test case name/id (use `homework_id` when requesting tokens for grade lookup)
  - `course_name` (string, required) — public course identifier
- **Processing**:
  - Rate-limit check (3/min per `student_id`).
  - Looks up `course_secret` by scanning `Classes` table where `course_id == course_name`, returning `secret_key`.
  - Generates two distinct tokens using HMAC-SHA256 with key `(student_secret + SYSTEM_SALT)` and message `(student_id + test_case + course_secret + timestamp + nonce)`.
  - Stores tokens in `StudentTokens` with status `active`, `created_at` timestamp, and TTL = 1h.
  - Updates `StudentRateLimit` table to track request count.
- **Responses**:
  - `200`: `{ "token1": "...", "token2": "..." }`
  - `429`: rate limit exceeded (back off ≥60s)
  - `400`: malformed payload, missing required fields, or invalid course name
- **Notes**:
  - Tokens are single-use; reuse returns an error at consuming endpoints.
  - Tokens expire after 1 hour (enforced via DynamoDB TTL and runtime timestamp checks).
  - Token format: `<hmac_hash>.<nonce>.<timestamp>`

### 2) grader (Lambda)
Orchestrates grading: fetches test case (using `token_test`), runs grade logic, saves result (using `token_save`).

- **HTTP Method**: `POST`
- **API Gateway Path**: typically `/grader`
- **Headers**: `x-api-key: <api_key>` (required if API Gateway API key requirement is enabled)
- **Request Body (JSON)**:
  - `homework_id` (string, required)
  - `student_id` (string, required)
  - `test_case_id` (string, required)
  - `secret` (string, optional; kept for compatibility)
  - `answer` (string, required) — base64/dill-serialized payload or plain text
  - `token_test` (string, required) — from token_generator (token1)
  - `token_save` (string, required) — from token_generator (token2)
- **Processing**:
  - Calls `get_test_case` API with `homework_id` + `token_test`.
  - Deserializes test case code and libraries from base64/dill format.
  - Imports required libraries dynamically into grader namespace.
  - Executes the test case function against deserialized `answer`.
  - Calls `save_student` API with `token_save` to persist score.
- **Responses**:
  - `200`: grading message string (includes score and feedback)
  - `400`: errors including:
    - Parse errors (malformed payload)
    - Token errors (invalid, expired, already used)
    - Fetch errors (test case not found, HTTP 400/401/403)
    - Grading errors (execution exceptions)
    - Save errors (storage failures)
- **Error Details by HTTP Code**:
  - `400` from get_test_case: Bad request - invalid homework_id or token format
  - `401` from get_test_case: Unauthorized - invalid API key
  - `403` from get_test_case: Forbidden - token invalid, expired, or already used
- **Notes**:
  - Both tokens are mandatory in the new security model.
  - `secret` is still accepted for backward compatibility but not the primary trust mechanism.
  - The `deserialize()` function handles both base64/dill-encoded and plain text answers.

### 3) get_test_case (Lambda)
Returns test case code and required libraries for a homework/test case. Requires a valid single-use token.

- **HTTP Method**: `POST`
- **API Gateway Path**: typically `/GetTest` or `/get_test_case`
- **Headers**: `x-api-key: <api_key>` (required if API Gateway API key requirement is enabled)
- **Request Body (JSON)**:
  - `homework_id` (string, required)
  - `token` (string, required) — should be `token1` from token_generator
- **Processing**:
  - Parses event body (handles both direct JSON and API Gateway wrapped format).
  - Verifies token in `StudentTokens` table:
    - Token exists and has not been used (status ≠ 'used')
    - Token has not expired (current_time - created_at ≤ 3600 seconds)
    - Token claims match request (student_id and test_case)
  - Retrieves test case and libraries from `HomeworksTestCases` table using DynamoDB Table resource.
  - Marks token as `used` with `used_at` timestamp.
- **Responses**:
  - `200`: `{ "tests": <serialized_tests>, "libs": <serialized_libs> }`
  - `400`: errors including:
    - Malformed payload (missing homework_id or token)
    - Token not found or already used
    - Token has expired
    - Token missing student_id or test_case claims
    - Homework not found in database
- **Notes**:
  - Test cases and libraries are stored in base64/dill-serialized format.
  - Token verification happens before database lookup.
  - Tokens are immediately marked as used to prevent replay attacks.

### 4) save_student (Lambda)
Records a student’s score for a test case. Requires a valid single-use token.

- **HTTP Method**: `POST`
- **API Gateway Path**: typically `/SaveGrade` or `/save_student`
- **Headers**: `x-api-key: <api_key>` (required if API Gateway API key requirement is enabled)
- **Request Body (JSON)**:
  - `homework_id` (string, required)
  - `student_id` (string, required)
  - `test_case_id` (string, required)
  - `secret` (string, required for legacy secret validation)
  - `score` (number/string, required)
  - `max_score` (number/string, required)
  - `message` (string, optional)
  - `token` (string, required) — should be `token2` from token_generator
- **Processing**:
  - Verifies token in `StudentTokens` table:
    - Token exists and has not been used (status ≠ 'used')
    - Token has not expired (current_time - created_at ≤ 3600 seconds)
    - Token `student_id` claim matches request `student_id`
    - Token `test_case` claim matches request `test_case_id`
  - Retrieves existing submission from `Gradebook` to check for secret mismatch.
  - Writes to `Gradebook` with composite key:
    - `homework_id`: partition key
    - `student_submission_id`: sort key = `student_id + '_' + test_case_id`
  - Stores `student_score`, `max_score`, `timestamp`, and `secret`.
  - Marks token as `used` with `used_at` timestamp.
- **Responses**:
  - `200`: success message with score summary
  - `400`: errors including:
    - Malformed payload (missing required fields)
    - Token verification failed (not found, expired, used, or claims mismatch)
    - Secret mismatch (different secret used for same question)
    - Storage errors (DynamoDB write failures)
- **Notes**:
  - Legacy secret validation: if a previous submission exists with a different secret, the request fails.
  - Tokens are single-use and immediately marked as used.

### 5) grades_lambda (read gradebook)
Fetches grades for a student. ALL_STUDENTS is disabled. Requires two single-use tokens tied to `student_id` + `homework_id`.

- **HTTP Method**: `POST`
- **API Gateway Path**: typically `/grades_lambda` or `/grades`
- **Headers**: `x-api-key: <api_key>` (required if API Gateway API key requirement is enabled)
- **Request Body (JSON)**:
  - `homework_id` (string, required)
  - `request_type` (enum: must be `STUDENT_GRADE`; `ALL_STUDENTS_GRADES` is rejected)
  - `student_id` (string, required)
  - `token1` (string, required) — token tied to `student_id` + `homework_id`
  - `token2` (string, required) — second token tied to `student_id` + `homework_id`
- **Processing**:
  - Validates `request_type` is `STUDENT_GRADE` (throws exception for `ALL_STUDENTS_GRADES`).
  - Retrieves homework metadata from `HomeworksMetadata` table (deadline, max_daily_submissions, total_score).
  - Verifies both tokens against `StudentTokens` table:
    - Both tokens exist and have not been used
    - Both tokens have not expired
    - Both tokens match `student_id` and `homework_id`
  - Marks both tokens as used with `used_at` timestamps.
  - Scans `Gradebook` table for all submissions where `homework_id` matches and `student_submission_id` begins with `student_id`.
  - Serializes response as JSON (not base64/dill).
- **Responses**:
  - `200`: JSON serialized grades array plus metadata `(grades, deadline, max_daily_submissions, max_score)`
  - `400`: errors including:
    - Malformed payload
    - Unsupported request_type (ALL_STUDENTS_GRADES)
    - Token verification failed (not found, expired, used, or claims mismatch)
    - Homework metadata not found
- **Notes**:
  - Clients must first call `token_generator` with `test_case = homework_id` to obtain `token1`/`token2` for this request.
  - Both tokens are verified and marked used before grade retrieval.
  - Uses low-level DynamoDB client for metadata lookup (requires `{'S': value}` format).
  - Uses DynamoDB Table resource for token operations.

### 6) homework_config_lambda (course/homework metadata)
Creates or updates homework metadata. (If exposed)

- Not covered by token flow; typically admin-only. Refer to deployment-specific policy for access control.

## Client Flows

**Grading flow** (homework test execution)
1. Call `token_generator` with (`student_id`, `student_secret`, `test_case`, `course_name`).
2. Receive `token1` (tests) and `token2` (save).
3. Preferred: call `grader` with `token_test = token1`, `token_save = token2`.
4. Alternate: call `get_test_case` with `token = token1`, run locally, then `save_student` with `token = token2`.
5. On 400 token errors, regenerate; on 429 back off ≥60s.

**Grade retrieval flow** (student reads their grades)
1. Call `token_generator` with (`student_id`, `student_secret`, `test_case = homework_id`, `course_name`).
2. Receive `token1` and `token2` (both tied to homework_id).
3. Call `grades_lambda` with `request_type = STUDENT_GRADE`, supplying `token1`, `token2`, `student_id`, and `homework_id`.
4. Tokens are single-use; regenerate for each lookup.

## Token Semantics
- Format: `<hmac>.<nonce>.<timestamp>`
- Key: `student_secret + SYSTEM_SALT`
- Message: `student_id + test_case + course_secret + timestamp + nonce`
- TTL: 1 hour (enforced via DynamoDB TTL + runtime checks)
- Single-use: status flips to `used` after first consumption

## Required DynamoDB Tables
- `StudentTokens`: token store (`token`, `student_id`, `test_case`, `course_secret`, `status`, `created_at`, `ttl`)
- `StudentRateLimit`: rate limiting per student (`student_id`, `request_times[]`, `ttl`)
- `Classes`: course metadata (`secret_key` as PK, `course_id` used to filter)
- `HomeworksTestCases`: test case content and libraries
- `Gradebook`: grade storage
- `HomeworksMetadata`: homework metadata (deadline, max submissions, scores)

## Client Error Handling Guidance
- `429` from token_generator: back off (≥60s), then retry.
- Token errors (`400`): regenerate tokens; ensure token matches `student_id`/`test_case_id`; ensure not reused; ensure within 1h.
- Grader errors: inspect message; could stem from test retrieval, grading exception, or save failure.

## Security Notes
- Always transport over HTTPS.
- Do not log tokens or secrets in plaintext.
- Rotate `SYSTEM_SALT` and course secrets periodically; regenerating tokens is required after rotation.
- Ensure API Gateway keys/authorizers are applied where appropriate.

## Open Questions / Clarifications Needed
1) Confirm API Gateway paths/hosts and required headers (e.g., `x-api-key`) for each endpoint in production.
2) Confirm whether `course_name` maps 1:1 to `course_id` (string) in `Classes`, and whether multiple records per `course_id` can exist. Currently the scan returns the first match.
3) For `grader`, should `secret` remain optional, or should clients omit it entirely in the new flow?
4) For `save_student`, should `secret` be deprecated and ignored when token is present?
5) Are there size limits for `answer` payloads or preferred serialization beyond base64+dill?

## Minimal Client Checklist
- Acquire `SYSTEM_SALT` via environment (server-side only; clients never see it).
- Call `token_generator` before each grading attempt.
- Pass `token_test` and `token_save` to `grader` (or `token` individually to `get_test_case` / `save_student`).
- Handle 429 with backoff; handle 400 by regenerating tokens.
- Respect token TTL (1 hour) and single-use rules.
