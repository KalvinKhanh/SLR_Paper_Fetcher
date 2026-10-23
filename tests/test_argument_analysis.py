import tempfile
import unittest
from pathlib import Path

from ai_analysis import (_extract_tei_document, _section_chunks, analyze_cross_paper_relationships,
                         _annotate_roles, _reconstruct_warrants, build_aif_graph,
                         validate_aif_graph, _quote_span, _parse_json, PROPOSITION_SCHEMA,
                         ANALYSIS_SCHEMA, ROLE_SCHEMA, _normalize_proposition_output,
                         _normalize_role_result)
from paper_store import PaperStore
from unittest.mock import patch


class StructuredDocumentTests(unittest.TestCase):
    def test_normalizes_model_aliases_using_only_grounded_quote(self):
        item = _normalize_proposition_output({
            "type": "Finding / Result", "source_quote": "The system reduced staleness.",
        })
        self.assertEqual(item["proposition_type"], "finding")
        self.assertEqual(item["text"], item["source_quote"])

    def test_normalizes_role_and_relation_aliases_returned_by_ollama(self):
        result = _normalize_role_result({
            "propositions": [{"id": "p1", "role": "evidence"}],
            "relations": [{"relation_type": "supports", "source_id": "p1",
                           "target_id": "p2", "target_kind": "proposition"}],
        })
        self.assertEqual(result["annotations"][0]["proposition_id"], "p1")
        self.assertEqual(result["annotations"][0]["roles"], ["evidence"])
        self.assertEqual(result["relations"][0]["premise_ids"], ["p1"])
        self.assertEqual(result["relations"][0]["conclusion_id"], "p2")

    def test_role_json_array_is_partitioned_by_schema_keys(self):
        parsed = _parse_json('[{"id":"p1","role":"evidence"},'
                             '{"relation_type":"supports","source_id":"p1","target_id":"p2"}]',
                             ROLE_SCHEMA)
        self.assertEqual(len(parsed["annotations"]), 1)
        self.assertEqual(len(parsed["relations"]), 1)

    def test_json_array_is_wrapped_only_when_schema_has_one_array_root(self):
        parsed = _parse_json('[{"text":"A finding"}]', PROPOSITION_SCHEMA)
        self.assertEqual(parsed, {"propositions": [{"text": "A finding"}]})
        wrapped = _parse_json('[{"summary":"Draft"}]', ANALYSIS_SCHEMA)
        self.assertEqual(wrapped, {"summary": "Draft"})
        with self.assertRaisesRegex(ValueError, "expected an object"):
            _parse_json('["not an analysis object"]', ANALYSIS_SCHEMA)

    def test_extracts_imrad_and_keeps_section_offsets(self):
        body = " ".join(["Repeated study sentence with measurable outcomes."] * 80)
        tei = f'''<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>Study</title></titleStmt><sourceDesc><biblStruct><analytic><author><persName>A</persName></author></analytic><imprint><date when="2025"/></imprint></biblStruct></sourceDesc></fileDesc><profileDesc><abstract><p>Abstract text.</p></abstract></profileDesc></teiHeader><text><body><div><head>Methods</head><p>{body}</p></div><div><head>Results</head><p>{body}</p></div></body></text></TEI>'''.encode()
        document, metadata = _extract_tei_document(tei)
        self.assertEqual([item["section_type"] for item in document["sections"]], ["abstract", "methods", "results"])
        self.assertEqual(metadata["title"], "Study")
        self.assertEqual(document["metadata"]["completeness"], "full_text_candidate")
        for before, after in zip(document["sections"], document["sections"][1:]):
            self.assertLess(before["end_offset"], after["start_offset"])

    def test_short_body_is_never_marked_as_full_text(self):
        tei = b'''<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div><head>Findings</head><p>Short text.</p></div></body></text></TEI>'''
        document, _ = _extract_tei_document(tei)
        self.assertEqual(document["metadata"]["completeness"], "partial_text")

    def test_quote_match_requires_source_text(self):
        self.assertEqual(_quote_span("Evidence\n   supports the result.", "Evidence supports the result."), (0, 32))
        self.assertIsNone(_quote_span("Evidence supports result.", "Evidence proves the result."))

    def test_section_chunk_offsets_preserve_long_text(self):
        original = ("Repeated source sentence with a quote anchor. " * 700) + "UNIQUE_ANCHOR."
        chunks = _section_chunks({"sections": [{"id": "s", "section_type": "results", "heading": "Results",
            "text": original, "start_offset": 300, "end_offset": 300 + len(original), "provenance": {}}]})
        self.assertGreater(len(chunks), 1)
        target = "UNIQUE_ANCHOR."
        found = next(chunk for chunk in chunks if target in chunk["text"])
        start, _ = _quote_span(found["text"], target)
        self.assertEqual(original[found["chunk_start"] + start:found["chunk_start"] + start + len(target)], target)


class AIFGraphTests(unittest.TestCase):
    def test_multi_premise_ra_and_undercut_ca_are_structured(self):
        propositions = [
            {"id": "e1", "text": "Evidence one", "proposition_type": "finding", "argumentative_roles": ["evidence"]},
            {"id": "e2", "text": "Evidence two", "proposition_type": "finding", "argumentative_roles": ["evidence"]},
            {"id": "c1", "text": "Claim", "proposition_type": "conclusion", "argumentative_roles": ["claim"]},
            {"id": "l1", "text": "The inference is invalid", "proposition_type": "limitation", "argumentative_roles": ["rebuttal"]},
        ]
        relations = [
            {"id": "r1", "relation_type": "supports", "premise_ids": ["e1", "e2"], "conclusion_id": "c1", "review_status": "pending"},
            {"id": "r2", "relation_type": "undercut", "premise_ids": ["l1"], "target_id": "c1", "target_kind": "inference", "review_status": "pending"},
        ]
        graph, validation = build_aif_graph(propositions, relations)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(sum(edge["target_node_id"] == "ra:r1" and edge["source_node_id"].startswith("i:") for edge in graph["edges"]), 2)
        self.assertIn({"id": "e:r2:target", "source_node_id": "ca:r2", "target_node_id": "ra:r1"}, graph["edges"])
        self.assertEqual({node["node_type"] for node in graph["nodes"]}, {"I", "RA", "CA"})

    def test_unconnected_graph_node_is_not_mistaken_for_a_valid_argument(self):
        result = validate_aif_graph({"nodes": [{"id": "ra1", "node_type": "RA"}], "edges": []})
        self.assertFalse(result["valid"])


class CrossPaperCandidateTests(unittest.TestCase):
    def test_cross_paper_links_use_only_valid_distinct_papers_and_keep_quotes(self):
        propositions = [
            {"id": "p1", "doi": "10.1000/a", "text": "Traffic exposure was associated with lower exposure.",
             "source_quote": "Traffic exposure was associated with lower exposure.", "proposition_type": "finding",
             "argumentative_roles": ["evidence"], "is_inferred": False},
            {"id": "p2", "doi": "10.1000/b", "text": "Traffic exposure was associated with higher exposure.",
             "source_quote": "Traffic exposure was associated with higher exposure.", "proposition_type": "finding",
             "argumentative_roles": ["evidence"], "is_inferred": False},
            {"id": "p3", "doi": "10.1000/c", "text": "A reconstructed warrant", "source_quote": "Warrant quote",
             "proposition_type": "other", "argumentative_roles": ["warrant"], "is_inferred": True},
        ]
        with patch("ai_analysis._provider_json", return_value=({"relationships": [{
            "source_proposition_id": "p1", "target_proposition_id": "p2", "relationship": "contradicts",
            "explanation": "Context aligns and outcomes differ.", "confidence": 0.7}, {
            "source_proposition_id": "p1", "target_proposition_id": "p3", "relationship": "supports",
            "explanation": "Should be rejected.", "confidence": 0.8}]}, "mock-model")):
            relationships, model = analyze_cross_paper_relationships(propositions, "ollama")
        self.assertEqual(model, "mock-model")
        self.assertEqual(len(relationships), 1)
        self.assertEqual(relationships[0]["relationship"], "contradicts")
        self.assertEqual(relationships[0]["source_quote"], propositions[0]["source_quote"])
        self.assertEqual(relationships[0]["target_quote"], propositions[1]["source_quote"])

    def test_large_role_and_warrant_stages_are_chunked(self):
        propositions = [{"id": f"p{i}", "text": f"Proposition {i}", "proposition_type": "finding",
                         "argumentative_roles": [], "source_quote": f"Quote {i}", "confidence": 0.5,
                         "provenance": {}} for i in range(51)]
        relations = [{"id": f"r{i}", "relation_type": "supports", "premise_ids": [f"p{i}"],
                     "conclusion_id": f"p{i+1}", "target_id": None, "target_kind": None} for i in range(21)]
        with patch("ai_analysis._provider_json", return_value=({"annotations": [], "relations": []}, "model")) as roles:
            result = _annotate_roles(propositions, "ollama")
        self.assertEqual(roles.call_count, 2)
        self.assertEqual(result[0], [])
        with patch("ai_analysis._provider_json", return_value=({"warrants": []}, "model")) as warrants:
            _reconstruct_warrants(propositions, relations, "ollama")
        self.assertEqual(warrants.call_count, 2)


class ArgumentReviewStorageTests(unittest.TestCase):
    def test_schema_is_additive_and_reviews_are_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PaperStore(Path(temp) / "papers.sqlite3")
            store.update("10.1000/test", "DOWNLOADED")
            run = {"id": "run-1", "provider": "ollama", "model": "test-model", "prompt_versions": {"extract": "1"},
                   "document": {"metadata": {}, "sections": [{"id": "sec-1", "section_type": "results", "heading": "Results", "text": "Result quote.", "start_offset": 0, "end_offset": 13}]},
                   "propositions": [{"id": "p-1", "text": "Result quote.", "proposition_type": "finding", "section_id": "sec-1", "source_quote": "Result quote.", "argumentative_roles": ["evidence"], "review_status": "pending", "provenance": {}},
                                    {"id": "p-2", "text": "Conclusion.", "proposition_type": "conclusion", "source_quote": "Conclusion.", "review_status": "pending", "provenance": {}}],
                   "relations": [{"id": "r-1", "relation_type": "supports", "premise_ids": ["p-1"], "conclusion_id": "p-2", "review_status": "pending", "provenance": {}}],
                   "graph": {"nodes": [], "edges": []}, "validation": {"valid": True}}
            store.save_argumentation("10.1000/test", run)
            self.assertTrue(store.review_analysis_item("10.1000/test", "p-1", "proposition", "approved"))
            saved = store.get_argumentation("10.1000/test")
            self.assertEqual(saved["propositions"][0]["review_status"], "approved")
            self.assertEqual(saved["reviews"][0]["decision"], "approved")
            self.assertEqual(saved["graph"]["nodes"][0]["metadata"]["review_status"], "approved")
            self.assertTrue(store.review_analysis_item("10.1000/test", "r-1", "relation", "approved"))
            self.assertTrue(store.review_analysis_item("10.1000/test", "p-2", "proposition", "approved"))
            approved_graph = store.approved_argumentation_graphs()[0]["graph"]
            self.assertIn("RA", {node["node_type"] for node in approved_graph["nodes"]})
            self.assertTrue(store.edit_analysis_item("10.1000/test", "r-1", "relation",
                {"relation_type": "supports", "premise_ids": ["p-1"], "conclusion_id": "p-2", "target_id": None,
                 "target_kind": "proposition", "explanation": "Corrected by reviewer"}))
            self.assertEqual(store.get_argumentation("10.1000/test")["relations"][0]["explanation"], "Corrected by reviewer")
            approved = store.approved_propositions_for_synthesis()
            self.assertEqual([item["id"] for item in approved], ["p-1", "p-2"])
            store.create_cross_paper_batch("batch-1", "ollama", "1.0")
            store.complete_cross_paper_batch("batch-1", "model", [{
                "id": "cross-1", "source_doi": "10.1000/test", "target_doi": "10.1000/other",
                "source_proposition_id": "p-1", "target_proposition_id": "p-2", "relationship": "qualifies",
                "explanation": "Context limits scope.", "source_quote": "Result quote.", "target_quote": "Other quote.",
                "confidence": 0.5, "provenance": {"model": "model"}}])
            self.assertTrue(store.review_cross_paper_relationship("cross-1", "approved"))
            self.assertEqual(store.list_cross_paper_relationships()["relationships"][0]["review_status"], "approved")


if __name__ == "__main__":
    unittest.main()
