"""tests/unit/test_llm.py — Tests for backend/llm.py"""

from langchain_openai import ChatOpenAI


class TestGetLlm:
    def setup_method(self):
        """Clear lru_cache before each test to ensure isolation."""
        from backend.llm import get_llm
        get_llm.cache_clear()

    def test_returns_chatOpenAI_instance(self):
        """get_llm() returns a ChatOpenAI instance."""
        from backend.llm import get_llm
        llm = get_llm()
        assert isinstance(llm, ChatOpenAI)

    def test_same_instance_returned_on_second_call(self):
        """Calling twice with same temperature returns same cached instance."""
        from backend.llm import get_llm
        llm_a = get_llm(temperature=0.1)
        llm_b = get_llm(temperature=0.1)
        assert llm_a is llm_b

    def test_different_temperatures_produce_different_instances(self):
        """Different temperature values produce different cached instances."""
        from backend.llm import get_llm
        llm_a = get_llm(temperature=0.0)
        llm_b = get_llm(temperature=0.9)
        assert llm_a is not llm_b

    def test_llm_has_correct_model_config(self):
        """The LLM is configured with max_retries and timeout."""
        from backend.llm import get_llm
        llm = get_llm()
        assert llm.max_retries == 3

    def test_streaming_disabled_and_http1_clients(self):
        """HTTP/2 streaming to OpenAI/Cloudflare can raise ConnectionTerminated."""
        from backend.llm import get_llm

        llm = get_llm()
        assert llm.streaming is False
        http_client = llm.http_client
        assert http_client is not None
        assert http_client._transport._pool._http2 is False

    def teardown_method(self):
        """Clear cache after each test."""
        from backend.llm import get_llm
        get_llm.cache_clear()
