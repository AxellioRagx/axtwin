"""`list_plugins()` / `aximprint plugins`: the listing the control-plane API reads
(`api/Spec.md` A1.1). Every registered plugin, each with a closed run-config schema
that names `source` as required."""
import json

from aximprint import cli
from aximprint.ingest import IngestPlugin, get_plugin, list_plugins
from aximprint.ingest.base import RUN_OPTION_SCHEMA


def test_every_registered_plugin_is_listed_with_a_closed_schema():
    rows = list_plugins()
    names = [r["name"] for r in rows]
    assert names == sorted(names)
    assert {"zeek", "suricata", "axquery", "pcap", "splunk", "elastic"} <= set(names)
    for r in rows:
        schema = r["config_schema"]
        assert schema["type"] == "object"
        assert schema["required"] == ["source"]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {"source", *RUN_OPTION_SCHEMA}
        # The listing is a projection of the plugin's own declaration, not a second table.
        assert schema == get_plugin(r["name"]).config_schema()


def test_each_shipped_plugin_says_what_its_source_is():
    for name in ("zeek", "suricata", "axquery", "pcap", "splunk", "elastic"):
        assert get_plugin(name).source_help, name
        desc = get_plugin(name).config_schema()["properties"]["source"]["description"]
        assert desc == get_plugin(name).source_help


def test_the_contract_default_is_honest_rather_than_empty():
    class Bare(IngestPlugin):
        name = "bare-test-plugin"

        def events(self, source, ts_from=None, ts_to=None):  # pragma: no cover
            return iter(())

    assert "plugin-defined" in Bare.config_schema()["properties"]["source"]["description"]


def test_the_cli_verb_prints_the_listing_as_json(capsys):
    assert cli.main(["plugins"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"plugins": list_plugins()}


# ---------------------------------------------------------------------------
# `source_kind` + `probe` (`api/Spec.md` A7: stored discovery sources)
# ---------------------------------------------------------------------------
def test_the_listing_says_how_each_source_is_addressed():
    kinds = {r["name"]: r["source_kind"] for r in list_plugins()}
    assert kinds["axquery"] == "http"
    assert kinds["zeek"] == "path"
    assert kinds["suricata"] == "path"
    assert kinds["pcap"] == "path"
    assert kinds["splunk"] == "http"
    assert kinds["elastic"] == "http"
    assert set(kinds.values()) <= {"path", "http"}


def test_a_plugin_that_cannot_probe_says_so_instead_of_guessing():
    assert get_plugin("zeek").probe("/nowhere") == {"supported": False}


def test_the_probe_verb_exits_2_for_a_plugin_that_cannot_probe(capsys):
    assert cli.main(["probe", "--plugin", "zeek", "--source", "/nowhere"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["supported"] is False and out["plugin"] == "zeek"


def test_the_probe_verb_names_an_unknown_plugin(capsys):
    assert cli.main(["probe", "--plugin", "nope", "--source", "x"]) == 2
    assert "no ingest plugin 'nope'" in capsys.readouterr().err
