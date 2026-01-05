import json
import yaml
import penngrader.grader as grader


class MockResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def _lower_headers(headers):
    return {k.lower(): v for k, v in headers.items()}


def test_grade_uses_api_keys(monkeypatch, tmp_path, capsys):
    config_file = tmp_path / "config.yaml"
    yaml.safe_dump(
        {
            "grader_api_url": "https://example.com/grader",
            "token_generator_url": "https://example.com/token_generator",
            "grader_api_key": "grader-key",
            "token_generator_api_key": "token-key",
        },
        config_file.open("w", encoding="utf-8"),
    )

    captured_requests = []

    def fake_urlopen(request_obj):
        body = request_obj.data.decode("utf-8") if request_obj.data else ""
        captured_requests.append(
            {
                "url": request_obj.full_url,
                "headers": dict(request_obj.headers),
                "body": body,
            }
        )
        if "token_generator" in request_obj.full_url:
            return MockResponse(b'{"token1": "tokA", "token2": "tokB"}')
        return MockResponse(b'"ok"')

    monkeypatch.setattr(grader.urllib.request, "urlopen", fake_urlopen)

    pg = grader.PennGrader(
        config_filename=str(config_file),
        homework_id="hw1",
        student_id="12345678",
        secret="secret",
        course_name="cis_course",
    )

    pg.grade(test_case_id="test_case_1", answer={"key": "value"})
    out, _ = capsys.readouterr()
    assert "ok" in out

    assert len(captured_requests) == 2

    token_req = captured_requests[0]
    grader_req = captured_requests[1]

    token_headers = _lower_headers(token_req["headers"])
    grader_headers = _lower_headers(grader_req["headers"])

    assert token_headers.get("x-api-key") == "token-key"
    assert grader_headers.get("x-api-key") == "grader-key"

    token_body = json.loads(token_req["body"])
    assert token_body["student_id"] == "12345678"
    assert token_body["course_name"] == "cis_course"
    assert token_body["test_case"] == "test_case_1"
