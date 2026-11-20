import json
import os
import stat
import sys

import pytest

from maillon_agent import cli, client, config, hardware


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILLON_AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.delenv("MAILLON_ENROLLMENT_TOKEN", raising=False)
    return tmp_path / "agent"


# ------------------------------------------------------------- nvidia-smi
def test_parse_single_gpu():
    assert hardware.parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12282\n") == [
        hardware.Gpu("NVIDIA GeForce RTX 4070", 12282)
    ]


def test_parse_several_gpus_and_noise():
    output = "NVIDIA GeForce RTX 3060, 12288\n\nNVIDIA RTX A5000, 24564\nligne illisible\n"
    gpus = hardware.parse_nvidia_smi(output)
    assert [g.vram_mb for g in gpus] == [12288, 24564]


def test_parse_name_with_comma():
    assert hardware.parse_nvidia_smi("Carte, édition spéciale, 8192")[0] == hardware.Gpu(
        "Carte, édition spéciale", 8192
    )


def test_detect_keeps_the_largest_gpu(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hardware,
        "detect_gpus",
        lambda: [hardware.Gpu("Petit", 8188), hardware.Gpu("Grand", 24564)],
    )
    hw = hardware.detect(tmp_path / "models")
    assert (hw.gpu_model, hw.vram_mb) == ("Grand", 24564)
    assert hw.cpu_cores >= 1 and hw.ram_mb > 0 and hw.disk_free_gb >= 0


def test_detect_without_gpu(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda: None)
    hw = hardware.detect(tmp_path / "models")
    assert hw.gpu_model is None and hw.vram_mb == 0


# ---------------------------------------------------------------- adresse
@pytest.mark.parametrize(
    "given, expected",
    [
        ("https://api.exemple.com/", "https://api.exemple.com"),
        ("api.exemple.com", "https://api.exemple.com"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
    ],
)
def test_server_url_accepted(given, expected):
    assert client.normalize_server_url(given) == expected


@pytest.mark.parametrize("given", ["http://api.exemple.com", "ftp://x", "https://"])
def test_server_url_refused(given):
    with pytest.raises(client.AgentError):
        client.normalize_server_url(given)


# ---------------------------------------------------------- configuration
def test_config_roundtrip_and_private_permissions(isolated_home):
    cfg = config.AgentConfig("https://api.exemple.com", "id-1", "ma_secret", "wss://api.exemple.com/agent/ws")
    path = config.save(cfg)
    assert config.load() == cfg
    assert json.loads(path.read_text())["agent_token"] == "ma_secret"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert config.forget() is True and config.load() is None


def test_enroll_refuses_when_already_enrolled(capsys):
    config.save(config.AgentConfig("https://a.b", "id", "ma_x", "wss://a.b/agent/ws"))
    assert cli.main(["enroll", "--server", "https://a.b"]) == 1
    assert "déjà inscrite" in capsys.readouterr().err


def test_enroll_refuses_plain_http_to_a_remote_server(monkeypatch, capsys):
    monkeypatch.setenv("MAILLON_ENROLLMENT_TOKEN", "me_x")
    assert cli.main(["enroll", "--server", "http://api.exemple.com"]) == 2
    assert "HTTPS" in capsys.readouterr().err


def test_status_without_enrollment(capsys):
    assert cli.main(["status"]) == 1
    assert "pas inscrite" in capsys.readouterr().err


def test_info_runs(capsys):
    assert cli.main(["info"]) == 0
    assert "Classement" in capsys.readouterr().out


@pytest.mark.parametrize(
    "vram, ram, disk, expected",
    [
        (24564, 32000, 400, "niveau A"),
        (12282, 32000, 400, "niveau B"),
        (8188, 32000, 400, "niveau C"),
        (6144, 32000, 400, "moins de 8 Go"),
        (0, 32000, 400, "aucun GPU"),
        (12282, 8000, 400, "mémoire vive"),
        (12282, 32000, 10, "50 Go"),
    ],
)
def test_local_tier_estimate_matches_server_rules(vram, ram, disk, expected):
    hw = hardware.Hardware("Linux", "GPU", vram, 8, ram, disk)
    assert expected in cli._estimate_tier(hw)
