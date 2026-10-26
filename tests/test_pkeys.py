"""Offline tests: everything here runs without a key and without a network call."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_pkeys():
    """pkeys is an extensionless executable, so load it by path."""
    loader = importlib.machinery.SourceFileLoader("pkeys_under_test", str(ROOT / "pkeys"))
    spec = importlib.util.spec_from_loader("pkeys_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pkeys_under_test"] = module
    loader.exec_module(module)
    return module


pk = load_pkeys()


class KeyfileNames(unittest.TestCase):
    def test_longest_provider_name_wins(self):
        known = {"opencode", "opencode-go", "commandcode", "nano-gpt"}
        self.assertEqual(
            pk.split_keyfile_stem("opencode-go-dawid-jgg", known),
            ("opencode-go", "dawid-jgg"),
        )
        self.assertEqual(
            pk.split_keyfile_stem("nano-gpt-work", known),
            ("nano-gpt", "work"),
        )
        self.assertEqual(pk.split_keyfile_stem("commandcode", known), ("commandcode", "default"))

    def test_unknown_prefix_falls_back_to_first_dash(self):
        self.assertEqual(pk.split_keyfile_stem("mystery-tool-live", {"commandcode"}), ("mystery", "tool-live"))
        self.assertEqual(pk.split_keyfile_stem("solo", set()), ("solo", "default"))


class Lanes(unittest.TestCase):
    def test_store_hit_naming_the_live_key_is_one_lane(self):
        live = "live-key"
        spare = "spare-key"
        prov = pk.Provider(name="commandcode", key=live, key_source="models.json")
        cfg = pk.Config(
            auth={},
            models={},
            store={},
            settings={},
            keyfiles=[
                pk.Keyfile(path=Path("/k/commandcode-live.md"), provider="commandcode", label="live", value=live),
                pk.Keyfile(path=Path("/k/commandcode-spare.md"), provider="commandcode", label="spare", value=spare),
            ],
        )
        lanes = pk.lanes(cfg, prov)
        self.assertEqual([lane.label for lane in lanes], ["live", "spare"])
        self.assertEqual([lane.active for lane in lanes], [True, False])
        self.assertEqual(lanes[0].source, "keyfile commandcode-live.md")

    def test_other_providers_keys_are_not_lanes(self):
        prov = pk.Provider(name="commandcode", key="live-key", key_source="models.json")
        cfg = pk.Config(
            auth={},
            models={},
            store={},
            settings={},
            keyfiles=[pk.Keyfile(path=Path("/k/zai-x.md"), provider="zai", label="x", value="zzz")],
        )
        self.assertEqual(len(pk.lanes(cfg, prov)), 1)


class Ranking(unittest.TestCase):
    def test_worst_window_is_the_tightest_one(self):
        data = {"windows": [{"name": "rolling 5h", "percent": 10}, {"name": "weekly", "percent": 90}]}
        self.assertEqual(pk._worst_window(data), (90.0, "weekly"))

    def test_best_lane_skips_lanes_that_did_not_answer(self):
        rows = [
            {"label": "live", "active": True, "data": {"windows": [{"name": "weekly", "percent": 95}]}},
            {"label": "broken", "active": False, "data": {"note": "HTTP 401"}},
            {"label": "spare", "active": False, "data": {"windows": [{"name": "weekly", "percent": 3}]}},
        ]
        self.assertEqual(pk.best_lane(rows)["label"], "spare")

    def test_no_numbers_means_no_recommendation(self):
        self.assertIsNone(pk.best_lane([{"label": "x", "data": {"note": "no api"}}]))


class Precedence(unittest.TestCase):
    """pi resolves auth.json before models.json; pkeys must report the same key."""

    def _cfg(self, auth, models):
        return pk.Config(auth=auth, models=models, store={}, settings={}, keyfiles=[])

    def test_auth_json_beats_models_json(self):
        cfg = self._cfg(
            {"commandcode": {"type": "api_key", "key": "from-auth"}},
            {"providers": {"commandcode": {"apiKey": "from-models", "baseUrl": "https://x"}}},
        )
        prov = pk.build_providers(cfg)["commandcode"]
        self.assertEqual(prov.key, "from-auth")
        self.assertEqual(prov.key_source, "auth.json")
        self.assertTrue(prov.has_conflict)
        self.assertEqual(prov.other_key_sources, [("models.json", "from-models")])

    def test_nested_auth_providers_block_is_not_a_credential(self):
        cfg = self._cfg(
            {"providers": {"nano-gpt": {"apiKey": "junk", "baseUrl": "https://nano"}}},
            {"providers": {"nano-gpt": {"apiKey": "real"}}},
        )
        prov = pk.build_providers(cfg)["nano-gpt"]
        self.assertEqual(prov.key, "real")
        self.assertEqual(prov.key_source, "models.json")
        self.assertFalse(prov.has_conflict)


class Tidy(unittest.TestCase):
    def _plan(self, auth, models, keep="auth"):
        cfg = pk.Config(auth=auth, models=models, store={}, settings={}, keyfiles=[])
        return pk.plan_tidy(cfg, keep)

    def test_duplicate_credentials_keep_auth_by_default(self):
        actions = self._plan(
            {"commandcode": {"type": "api_key", "key": "from-auth"}},
            {"providers": {"commandcode": {"apiKey": "from-models"}}},
        )
        self.assertEqual([a.kind for a in actions], ["drop-models-key"])
        self.assertIn("auth.json", actions[0].drop.replace("models.json", "auth.json"))

    def test_keep_models_flips_the_choice(self):
        actions = self._plan(
            {"commandcode": {"type": "api_key", "key": "from-auth"}},
            {"providers": {"commandcode": {"apiKey": "from-models"}}},
            keep="models",
        )
        self.assertEqual([a.kind for a in actions], ["drop-auth-key"])
        self.assertEqual(actions[0].drop, "auth.json[commandcode]")

    def test_orphan_block_is_dropped_and_oauth_key_is_not_touched(self):
        actions = self._plan(
            {
                "providers": {"nano-gpt": {"apiKey": "junk"}},
                "opencode-go": {"type": "oauth", "access": "a", "refresh": "r", "expires": 1},
            },
            {"providers": {"nano-gpt": {"apiKey": "real"}, "opencode-go": {"apiKey": "unused"}}},
        )
        self.assertEqual(
            [(a.provider, a.kind) for a in actions],
            [("nano-gpt", "drop-orphan-auth-block"), ("opencode-go", "drop-models-key")],
        )

    def test_single_source_means_no_actions(self):
        self.assertEqual(self._plan({"zai": {"type": "api_key", "key": "k"}}, {}), [])


class WritePermissions(unittest.TestCase):
    def test_write_json_keeps_a_private_file_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text("{}")
            os.chmod(path, 0o600)
            pk.write_json(path, {"a": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {"a": 1})

    def test_write_json_creates_new_files_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            pk.write_json(path, {"a": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class CachedRendering(unittest.TestCase):
    """`pkeys limits` without --live must render multi-key lanes from the cache."""

    def test_two_lanes_render_and_name_the_roomier_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "keys"
            agent = Path(tmp) / "agent"
            home.mkdir()
            agent.mkdir()
            (home / "commandcode-live.md").write_text("live-key")
            (home / "commandcode-spare.md").write_text("spare-key")
            (agent / "auth.json").write_text(json.dumps({"commandcode": {"type": "api_key", "key": "live-key"}}))
            (agent / "models.json").write_text("{}")
            (agent / "models-store.json").write_text("{}")
            (agent / "settings.json").write_text("{}")

            def lane(label, percent):
                return {
                    "label": label,
                    "fingerprint": label,
                    "active": label == "live",
                    "fetchedAt": 1,
                    "data": {"state": "active", "windows": [{"name": "weekly", "percent": percent}]},
                }

            state = {
                "limits": {
                    "commandcode": {
                        "fetchedAt": 1,
                        "data": lane("live", 96)["data"],
                        "lanes": [lane("live", 96), lane("spare", 2)],
                    }
                }
            }
            (home / ".pkeys-state.json").write_text(json.dumps(state))

            env = {"PKEYS_HOME": str(home), "PKEYS_AGENT_DIR": str(agent), "PKEYS_SESSIONS_DIR": str(agent / "sessions")}
            saved = {key: os.environ.get(key) for key in env}
            os.environ.update(env)
            try:
                cfg = pk.load_config()
                providers = pk.build_providers(cfg)
                self.assertIn("commandcode", providers)
                lanes = pk.lanes(cfg, providers["commandcode"])
                self.assertEqual([lane.label for lane in lanes], ["live", "spare"])
                pick = pk.best_lane(state["limits"]["commandcode"]["lanes"])
                self.assertEqual(pick["label"], "spare")
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
