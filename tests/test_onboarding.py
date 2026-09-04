import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from matcha_core.onboarding import (
    ONBOARDING_JSON_SCHEMA,
    OpenAICompatibleSpecGenerator,
    RepositoryBootstrapper,
    SpecsBootstrapError,
    build_repository_context,
    normalize_specs_draft,
    render_specs_draft,
    write_specs_draft,
)
from matcha_core.specs_parser import SpecsParser


def valid_payload(path: str = "Sources/GameLibrary.swift"):
    return {
        "project_summary": "The application stores games and lets users resume their progress.",
        "features": [
            {
                "name": "Game library",
                "description": "Players can keep a persistent library of games they are currently playing.",
                "priority": "High",
                "confidence": 0.91,
                "evidence": [{"path": path, "rationale": "Defines the persistent game collection."}],
                "acceptance_criteria": [
                    {
                        "description": "Players can add a game to their library.",
                        "confidence": 0.88,
                        "evidence": [{"path": path, "rationale": "The add action inserts a game."}],
                    },
                    {
                        "description": "Saved games remain available after the application restarts.",
                        "confidence": 0.82,
                        "evidence": [{"path": path, "rationale": "The library uses persistent storage."}],
                    },
                ],
            }
        ],
        "questions": [
            {
                "feature_name": "Game library",
                "question": "Should archived games remain visible by default?",
                "reason": "The code exposes both states but does not establish product intent.",
            }
        ],
    }


class FakeGenerator:
    model = "fake-model"

    def __init__(self, payload=None):
        self.payload = payload or valid_payload()
        self.calls = []

    def generate_specs(self, repository_context, max_features, language, debug=None):
        self.calls.append(
            {
                "repository_context": repository_context,
                "max_features": max_features,
                "language": language,
            }
        )
        return self.payload


class FailedGenerator:
    model = "fake-model"

    def generate_specs(self, repository_context, max_features, language, debug=None):
        if debug is not None:
            debug["response"] = '{"features": []}'
        raise SpecsBootstrapError("no usable features")


class OnboardingTests(unittest.TestCase):
    def test_openai_compatible_generator_parses_json_payload(self):
        client = MagicMock()
        client.model = "test-model"
        client.create_json_completion.return_value = json.dumps(valid_payload())
        generator = OpenAICompatibleSpecGenerator(client)

        payload = generator.generate_specs("context", 5, "English")

        self.assertEqual(payload["features"][0]["name"], "Game library")
        client.create_json_completion.assert_called_once()
        self.assertEqual(
            client.create_json_completion.call_args.kwargs["json_schema"],
            ONBOARDING_JSON_SCHEMA,
        )

    def test_openai_compatible_generator_scales_output_budget_for_feature_count(self):
        client = MagicMock()
        client.model = "test-model"
        client.create_json_completion.return_value = json.dumps(valid_payload())
        generator = OpenAICompatibleSpecGenerator(client)

        generator.generate_specs("context", 12, "English")

        self.assertEqual(client.create_json_completion.call_args.kwargs["max_tokens"], 9_600)

    def test_openai_compatible_generator_rejects_invalid_json(self):
        client = MagicMock()
        client.model = "test-model"
        client.create_json_completion.return_value = "not json"
        generator = OpenAICompatibleSpecGenerator(client)

        with self.assertRaisesRegex(SpecsBootstrapError, "invalid onboarding JSON"):
            generator.generate_specs("context", 5, "English")

    def test_openai_compatible_generator_repairs_incomplete_shape(self):
        client = MagicMock()
        client.model = "test-model"
        client.create_json_completion.side_effect = [
            '{"project_summary":"A sufficiently long summary","features":[{"name":"Library"}]}',
            json.dumps(valid_payload()),
        ]
        generator = OpenAICompatibleSpecGenerator(client)
        debug = {}

        payload = generator.generate_specs("context", 5, "English", debug=debug)

        self.assertEqual(payload["features"][0]["name"], "Game library")
        self.assertEqual(client.create_json_completion.call_count, 2)
        self.assertIn("missing description", debug["primary_shape_error"])
        self.assertIn("repair_response", debug)

    def test_context_includes_product_code_and_excludes_secrets_and_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "Sources"
            source.mkdir()
            (source / "GameLibrary.swift").write_text("struct GameLibrary {}\n", encoding="utf-8")
            (repo / ".env").write_text("TOKEN=do-not-read\n", encoding="utf-8")
            (repo / "credentials.json").write_text('{"password":"secret"}', encoding="utf-8")
            (repo / "SPECS.md").write_text("existing specs", encoding="utf-8")
            derived = repo / "DerivedData"
            derived.mkdir()
            (derived / "Generated.swift").write_text("generated", encoding="utf-8")

            context, selected = build_repository_context(repo, max_context_chars=20_000)

        self.assertEqual(selected, ["Sources/GameLibrary.swift"])
        self.assertIn("struct GameLibrary", context)
        self.assertNotIn("do-not-read", context)
        self.assertNotIn("existing specs", context)
        self.assertNotIn("Generated.swift", context)

    def test_context_excludes_xcode_user_and_asset_catalog_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "Sources"
            source.mkdir()
            (source / "App.swift").write_text("struct App {}\n", encoding="utf-8")
            user_data = repo / "Project.xcodeproj" / "xcuserdata" / "user.xcuserdatad"
            user_data.mkdir(parents=True)
            (user_data / "scheme.plist").write_text("<plist/>", encoding="utf-8")
            assets = repo / "Assets.xcassets" / "AppIcon.appiconset"
            assets.mkdir(parents=True)
            (assets / "Contents.json").write_text("{}", encoding="utf-8")

            _, selected = build_repository_context(repo, max_context_chars=20_000)

        self.assertEqual(selected, ["Sources/App.swift"])

    def test_bootstrap_builds_parseable_draft_with_evidence_and_questions(self):
        generator = FakeGenerator()
        bootstrapper = RepositoryBootstrapper(generator)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "Sources"
            source.mkdir()
            (source / "GameLibrary.swift").write_text(
                "struct GameLibrary { func add() {} }\n",
                encoding="utf-8",
            )

            draft = bootstrapper.bootstrap_path(
                repo,
                provider="ollama",
                language="Spanish",
                max_context_chars=20_000,
            )
            output = repo / "SPECS.draft.md"
            write_specs_draft(draft, output)
            parsed = SpecsParser().parse(str(output))

        self.assertEqual(draft.model, "fake-model")
        self.assertEqual(draft.provider, "ollama")
        self.assertEqual(draft.questions[0].feature_id, "FEAT-001")
        self.assertEqual(parsed[0]["id"], "FEAT-001")
        self.assertEqual(parsed[0]["status"], "Draft")
        self.assertEqual(len(parsed[0]["acceptance_criteria"]), 2)
        self.assertIn("Sources/GameLibrary.swift", parsed[0]["acceptance_criteria"][0]["referenced_files"])
        self.assertEqual(generator.calls[0]["language"], "Spanish")

    def test_normalization_rejects_hallucinated_evidence_path(self):
        payload = valid_payload(path="Sources/DoesNotExist.swift")

        with self.assertRaisesRegex(SpecsBootstrapError, "unavailable evidence"):
            normalize_specs_draft(
                payload,
                repo_path=Path("/tmp/repo"),
                selected_files=["Sources/GameLibrary.swift"],
                provider="ollama",
                model="fake-model",
                max_features=12,
            )

    def test_render_marks_every_feature_as_draft(self):
        draft = normalize_specs_draft(
            valid_payload(),
            repo_path=Path("/tmp/repo"),
            selected_files=["Sources/GameLibrary.swift"],
            provider="ollama",
            model="fake-model",
            max_features=12,
        )

        rendered = render_specs_draft(draft)

        self.assertIn("**Status**: Draft", rendered)
        self.assertIn("observed behavior, not confirmed product intent", rendered)
        self.assertIn("[FEAT-001] Should archived games remain visible", rendered)

    def test_writer_refuses_to_overwrite_without_force(self):
        draft = normalize_specs_draft(
            valid_payload(),
            repo_path=Path("/tmp/repo"),
            selected_files=["Sources/GameLibrary.swift"],
            provider="ollama",
            model="fake-model",
            max_features=12,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "SPECS.draft.md"
            output.write_text("keep me", encoding="utf-8")

            with self.assertRaisesRegex(SpecsBootstrapError, "Refusing to overwrite"):
                write_specs_draft(draft, output)

            self.assertEqual(output.read_text(encoding="utf-8"), "keep me")

    def test_bootstrap_rejects_too_small_context_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")

            with self.assertRaisesRegex(SpecsBootstrapError, "at least 10000"):
                RepositoryBootstrapper(FakeGenerator()).bootstrap_path(
                    repo,
                    provider="openai",
                    max_context_chars=9999,
                )

    def test_bootstrap_writes_debug_data_when_generation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
            debug_path = repo / "bootstrap-debug.json"

            with self.assertRaisesRegex(SpecsBootstrapError, "no usable features"):
                RepositoryBootstrapper(FailedGenerator()).bootstrap_path(
                    repo,
                    provider="ollama",
                    max_context_chars=20_000,
                    debug_output_path=debug_path,
                )

            debug = debug_path.read_text(encoding="utf-8")
            self.assertIn('"response": "{\\"features\\": []}"', debug)
            self.assertIn('"error": "no usable features"', debug)


if __name__ == "__main__":
    unittest.main()
