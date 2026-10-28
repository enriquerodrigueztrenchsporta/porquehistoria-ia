import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from instagram_pipeline.agents import (
    AgentError, OllamaClient, generate_episode, validate_episode, verify_episode, verify_facts,
)
from instagram_pipeline.research import (
    ResearchError, choose_topic, load_topics, research_topic, validate_url,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def json(self, system, payload):
        self.calls += 1
        return copy.deepcopy(next(self.responses))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.topic = load_topics(ROOT / "data/topics.json")[0]
        self.episode = json.loads((ROOT / "data/demo_episode.json").read_text(encoding="utf-8"))
        self.evidence = {
            "topic_id": self.topic["id"], "facts": self.topic["facts"],
            "quotes_verified": True, "institution_count": 1,
            "sources": self.topic["sources"],
        }

    def fact_report(self):
        return {"passed": True, "issues": [], "facts": [
            {"id": f["id"], "supported": True, "reason": "Direct evidence"}
            for f in self.evidence["facts"]]}

    def episode_report(self):
        return {"passed": True, "title_supported": True, "hook_supported": True, "issues": [],
                "scenes": [{"id": s["id"], "supported": True, "visual_supported": True,
                            "reason": "Direct evidence"} for s in self.episode["scenes"]]}

    def test_rejects_noninstitutional_or_credential_urls(self):
        for url in ("http://www.nasa.gov/history", "https://nasa.gov.evil.test/", "https://127.0.0.1/",
                    "https://evil.test@nasa.gov/", "file:///C:/secret", "https://nasa.gov:8443/"):
            with self.subTest(url=url), self.assertRaises(ResearchError):
                validate_url(url)
        self.assertEqual(validate_url("https://www.nasa.gov/history/#test"), "https://www.nasa.gov/history/")

    def test_rejects_remote_llm_provider(self):
        with self.assertRaises(AgentError):
            OllamaClient("https://api.example.com")

    def test_initial_catalog_never_silently_repeats(self):
        topics = load_topics(ROOT / "data/topics.json")
        with self.assertRaises(ResearchError):
            choose_topic(topics, [t["id"] for t in topics])

    def test_fabricated_quote_blocks_research(self):
        page = {"text": "A real source, but no evidence for the proposed claims.",
                "retrieved_at": "2026-10-28T00:00:00+00:00", "sha256": "unused"}
        with patch("instagram_pipeline.research.fetch_page", return_value=page):
            with self.assertRaisesRegex(ResearchError, "Cita no localizada"):
                research_topic(self.topic)

    def test_verified_literal_quotes_do_not_skip_semantic_review(self):
        page = {"text": " ".join(e["quote"] for f in self.topic["facts"] for e in f["evidence"]),
                "retrieved_at": "2026-10-28T00:00:00+00:00", "sha256": "example"}
        with patch("instagram_pipeline.research.fetch_page", return_value=page):
            evidence = research_topic(self.topic)
        report = self.fact_report()
        report["facts"][0]["supported"] = False
        with self.assertRaises(AgentError):
            verify_facts(evidence, FakeClient([report]))

    def test_missing_fact_in_audit_rejected_even_if_passed(self):
        report = self.fact_report()
        report["facts"].pop()
        with self.assertRaises(AgentError):
            verify_facts(self.evidence, FakeClient([report]))

    def test_scene_cannot_cite_nonexistent_fact(self):
        self.episode["scenes"][0]["fact_ids"] = ["f999"]
        with self.assertRaises(AgentError):
            validate_episode(self.episode, self.evidence)

    def test_model_cannot_inject_visual_code(self):
        self.episode["scenes"][0]["visual"]["script"] = "print('unsafe')"
        with self.assertRaises(AgentError):
            validate_episode(self.episode, self.evidence)

    def test_unsupported_visual_text_blocks_publication(self):
        report = self.episode_report()
        report["scenes"][0]["visual_supported"] = False
        with self.assertRaises(AgentError):
            verify_episode(self.episode, self.evidence, FakeClient([report]))

    def test_incomplete_scene_audit_rejected(self):
        report = self.episode_report()
        report["scenes"].pop()
        with self.assertRaises(AgentError):
            verify_episode(self.episode, self.evidence, FakeClient([report]))

    def test_generation_stops_after_two_rejected_scripts(self):
        rejected = self.episode_report()
        rejected["passed"] = False
        client = FakeClient([self.fact_report(), {"selected_fact_ids": ["f1", "f2", "f3"]},
                             self.episode, rejected, self.episode, rejected])
        with self.assertRaisesRegex(AgentError, "dos intentos"):
            generate_episode(self.topic, self.evidence, client=client)
        self.assertEqual(client.calls, 6)

    def test_live_generation_records_actual_roles_only_after_review(self):
        draft = {k: self.episode[k] for k in ("title", "hook", "scenes")}
        client = FakeClient([self.fact_report(), {"selected_fact_ids": ["f1", "f2", "f3"]},
                             draft, self.episode_report()])
        result = generate_episode(self.topic, self.evidence, client=client)
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(result["generation_mode"], "local_ai")
        self.assertEqual(client.calls, 4)


if __name__ == "__main__":
    unittest.main()


class NormalizeVisualsTests(unittest.TestCase):
    def test_invalid_kind_becomes_allowed_template_and_text_is_untouched(self):
        from instagram_pipeline.agents import normalize_visuals
        episode = {"scenes": [
            {"narration": "Texto", "visual": {"kind": "timeline|map", "title": "T", "label": "L", "script": "x"}},
            {"narration": "Texto", "visual": {"kind": "explosion", "title": "T", "label": "L"}}]}
        normalize_visuals(episode)
        self.assertEqual(episode["scenes"][0]["visual"], {"kind": "map", "title": "T", "label": "L"})
        self.assertEqual(episode["scenes"][1]["visual"]["kind"], "artifact")
        self.assertEqual(episode["scenes"][0]["narration"], "Texto")


class VisualAsStringTests(unittest.TestCase):
    def test_visual_given_as_plain_string_is_wrapped(self):
        from instagram_pipeline.agents import normalize_visuals
        episode = {"scenes": [{"narration": "Texto", "visual": "timeline"}]}
        normalize_visuals(episode)
        self.assertEqual(episode["scenes"][0]["visual"], {"kind": "timeline", "title": "", "label": ""})
