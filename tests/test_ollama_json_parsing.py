"""Ollama JSON 파싱 테스트 케이스."""

import pytest
from upbit_bot.services.ollama_client import OllamaClient, OllamaError


class TestOllamaJSONParsing:
    """Ollama JSON 파싱 기능 테스트."""

    def test_parse_json_with_markdown_code_block(self):
        """마크다운 코드 블록이 포함된 응답 파싱 테스트."""
        client = OllamaClient(model="test-model")
        
        # 마크다운 코드 블록이 포함된 응답
        response = """```json
{
  "score": 0.95,
  "reason": "테스트 이유",
  "trend": "uptrend",
  "risk": "medium"
}
```"""
        
        result = client.parse_json_response(response)
        
        assert result["score"] == 0.95
        assert result["reason"] == "테스트 이유"
        assert result["trend"] == "uptrend"
        assert result["risk"] == "medium"

    def test_parse_json_without_markdown(self):
        """마크다운 코드 블록 없이 순수 JSON만 있는 경우."""
        client = OllamaClient(model="test-model")
        
        response = '{"score": 0.85, "trend": "downtrend"}'
        
        result = client.parse_json_response(response)
        
        assert result["score"] == 0.85
        assert result["trend"] == "downtrend"

    def test_parse_json_with_extra_text(self):
        """JSON 앞뒤에 추가 텍스트가 있는 경우."""
        client = OllamaClient(model="test-model")
        
        response = """이것은 JSON 응답입니다:
{
  "score": 0.90,
  "reason": "추가 텍스트 포함"
}
위와 같이 점수는 1.0을 초과할 수 없습니다."""
        
        result = client.parse_json_response(response)
        
        assert result["score"] == 0.90
        assert result["reason"] == "추가 텍스트 포함"

    def test_parse_json_with_multiple_code_blocks(self):
        """여러 코드 블록이 있는 경우 (첫 번째 JSON만 추출)."""
        client = OllamaClient(model="test-model")
        
        response = """```json
{
  "score": 0.95,
  "trend": "uptrend"
}
```

```python
print("이것은 무시됩니다")
```"""
        
        result = client.parse_json_response(response)
        
        assert result["score"] == 0.95
        assert result["trend"] == "uptrend"

    def test_parse_json_invalid_format(self):
        """잘못된 형식의 응답 처리."""
        client = OllamaClient(model="test-model")
        
        response = "이것은 JSON이 아닙니다"
        
        with pytest.raises(OllamaError):
            client.parse_json_response(response)

    def test_parse_json_empty_response(self):
        """빈 응답 처리."""
        client = OllamaClient(model="test-model")
        
        response = ""
        
        with pytest.raises(OllamaError):
            client.parse_json_response(response)

    def test_parse_json_with_nested_objects(self):
        """중첩된 객체가 있는 JSON 파싱."""
        client = OllamaClient(model="test-model")
        
        response = """```json
{
  "score": 0.88,
  "details": {
    "technical": 0.9,
    "sentiment": 0.85
  },
  "trend": "uptrend"
}
```"""
        
        result = client.parse_json_response(response)
        
        assert result["score"] == 0.88
        assert result["details"]["technical"] == 0.9
        assert result["details"]["sentiment"] == 0.85
        assert result["trend"] == "uptrend"

