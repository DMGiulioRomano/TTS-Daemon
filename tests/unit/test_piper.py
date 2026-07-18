"""PiperProvider tested against a fake ``piper`` executable.

The fake is a real subprocess (a small shell script), so argument building,
stdin passing, output collection, exit codes, and timeouts are exercised for
real -- only the neural network is missing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import make_clip
from tts_gateway.core.errors import SynthesisError
from tts_gateway.core.models import SynthesisRequest
from tts_gateway.providers.piper import PiperProvider, default_models_dir

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake piper is a POSIX script")

FAKE_PIPER = """\
#!/bin/sh
# Fake piper: records argv and stdin, copies a fixture wav to --output_file.
printf '%s\\n' "$@" > "$PIPER_TEST_RECORD/argv.txt"
cat > "$PIPER_TEST_RECORD/stdin.txt"
out=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--output_file" ]; then out="$arg"; fi
  prev="$arg"
done
if [ -n "$out" ]; then cp "$PIPER_TEST_FIXTURE" "$out"; fi
"""


@pytest.fixture()
def fake_piper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    bin_dir = tmp_path / "bin"
    record_dir = tmp_path / "record"
    models_dir = tmp_path / "models"
    for directory in (bin_dir, record_dir, models_dir):
        directory.mkdir()

    fixture_wav = tmp_path / "fixture.wav"
    fixture_wav.write_bytes(make_clip("piper fixture").data)

    script = bin_dir / "piper"
    script.write_text(FAKE_PIPER)
    script.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PIPER_TEST_RECORD", str(record_dir))
    monkeypatch.setenv("PIPER_TEST_FIXTURE", str(fixture_wav))

    (models_dir / "en_US-alpha.onnx").write_bytes(b"fake model")
    metadata = {"language": {"code": "en_US"}, "dataset": "alpha", "audio": {"quality": "medium"}}
    (models_dir / "en_US-alpha.onnx.json").write_text(json.dumps(metadata))
    (models_dir / "it_IT-beta.onnx").write_bytes(b"fake model")

    def argv() -> list[str]:
        return (record_dir / "argv.txt").read_text().splitlines()

    def stdin() -> str:
        return (record_dir / "stdin.txt").read_text()

    return SimpleNamespace(
        bin_dir=bin_dir,
        models_dir=models_dir,
        script=script,
        fixture_wav=fixture_wav,
        argv=argv,
        stdin=stdin,
        provider=lambda **settings: PiperProvider({"models_dir": str(models_dir), **settings}),
    )


class TestAvailability:
    def test_missing_binary(self, tmp_path: Path) -> None:
        provider = PiperProvider(
            {"binary": "definitely-not-piper-xyz", "models_dir": str(tmp_path)}
        )
        availability = provider.availability()
        assert not availability.available
        assert "not found on PATH" in availability.reason

    def test_binary_but_no_models(self, fake_piper: SimpleNamespace, tmp_path: Path) -> None:
        empty = tmp_path / "empty-models"
        empty.mkdir()
        provider = PiperProvider({"models_dir": str(empty)})
        availability = provider.availability()
        assert not availability.available
        assert "no voice models" in availability.reason

    def test_available_with_binary_and_models(self, fake_piper: SimpleNamespace) -> None:
        assert fake_piper.provider().availability().available

    def test_default_voice_path_counts_as_model(
        self, fake_piper: SimpleNamespace, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty-models"
        empty.mkdir()
        model = fake_piper.models_dir / "en_US-alpha.onnx"
        provider = PiperProvider({"models_dir": str(empty), "default_voice": str(model)})
        assert provider.availability().available

    def test_default_models_dir_location(self) -> None:
        assert str(default_models_dir()).endswith(os.path.join("tts-gateway", "piper"))


class TestSynthesis:
    def test_happy_path(self, fake_piper: SimpleNamespace) -> None:
        clip = fake_piper.provider().synthesize(SynthesisRequest(text="ciao mondo"))
        assert clip.data == fake_piper.fixture_wav.read_bytes()
        assert fake_piper.stdin() == "ciao mondo"
        argv = fake_piper.argv()
        # first model alphabetically is the default voice
        assert argv[:2] == ["--model", str(fake_piper.models_dir / "en_US-alpha.onnx")]
        assert "--output_file" in argv

    def test_named_voice(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.provider().synthesize(SynthesisRequest(text="x", voice="it_IT-beta"))
        assert str(fake_piper.models_dir / "it_IT-beta.onnx") in fake_piper.argv()

    def test_voice_as_path(self, fake_piper: SimpleNamespace) -> None:
        model = str(fake_piper.models_dir / "it_IT-beta.onnx")
        fake_piper.provider().synthesize(SynthesisRequest(text="x", voice=model))
        assert model in fake_piper.argv()

    def test_configured_default_voice(self, fake_piper: SimpleNamespace) -> None:
        provider = fake_piper.provider(default_voice="it_IT-beta")
        provider.synthesize(SynthesisRequest(text="x"))
        assert str(fake_piper.models_dir / "it_IT-beta.onnx") in fake_piper.argv()

    def test_unknown_voice_lists_available(self, fake_piper: SimpleNamespace) -> None:
        with pytest.raises(SynthesisError, match=r"en_US-alpha, it_IT-beta"):
            fake_piper.provider().synthesize(SynthesisRequest(text="x", voice="nope"))

    def test_missing_voice_path(self, fake_piper: SimpleNamespace, tmp_path: Path) -> None:
        with pytest.raises(SynthesisError, match="model file not found"):
            fake_piper.provider().synthesize(
                SynthesisRequest(text="x", voice=str(tmp_path / "gone.onnx"))
            )

    def test_speed_maps_to_inverse_length_scale(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.provider().synthesize(SynthesisRequest(text="x", speed=2.0))
        argv = fake_piper.argv()
        index = argv.index("--length_scale")
        assert argv[index + 1] == "0.5000"

    def test_normal_speed_omits_length_scale(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.provider().synthesize(SynthesisRequest(text="x", speed=1.0))
        assert "--length_scale" not in fake_piper.argv()

    def test_custom_speed_flag(self, fake_piper: SimpleNamespace) -> None:
        provider = fake_piper.provider(speed_flag="--length-scale")
        provider.synthesize(SynthesisRequest(text="x", speed=2.0))
        assert "--length-scale" in fake_piper.argv()

    def test_speaker_option(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.provider().synthesize(SynthesisRequest(text="x", options={"speaker": 3}))
        argv = fake_piper.argv()
        assert argv[argv.index("--speaker") + 1] == "3"

    def test_unknown_option_rejected(self, fake_piper: SimpleNamespace) -> None:
        with pytest.raises(SynthesisError, match="Unknown piper options: emotion"):
            fake_piper.provider().synthesize(SynthesisRequest(text="x", options={"emotion": "sad"}))

    def test_extra_args_appended(self, fake_piper: SimpleNamespace) -> None:
        provider = fake_piper.provider(extra_args=["--sentence_silence", "0.4"])
        provider.synthesize(SynthesisRequest(text="x"))
        argv = fake_piper.argv()
        assert argv[argv.index("--sentence_silence") + 1] == "0.4"

    def test_invalid_speed(self, fake_piper: SimpleNamespace) -> None:
        with pytest.raises(SynthesisError, match="speed must be positive"):
            fake_piper.provider().synthesize(SynthesisRequest(text="x", speed=-1))


class TestFailureModes:
    def test_nonzero_exit_includes_stderr(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.script.write_text("#!/bin/sh\necho 'model is corrupt' >&2\nexit 3\n")
        with pytest.raises(SynthesisError, match=r"status 3.*model is corrupt"):
            fake_piper.provider().synthesize(SynthesisRequest(text="x"))

    def test_missing_output_file(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.script.write_text("#!/bin/sh\ncat > /dev/null\nexit 0\n")
        with pytest.raises(SynthesisError, match="wrote no output"):
            fake_piper.provider().synthesize(SynthesisRequest(text="x"))

    def test_empty_output_file(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.script.write_text(
            "#!/bin/sh\n"
            'out=""; prev=""\n'
            'for arg in "$@"; do\n'
            '  if [ "$prev" = "--output_file" ]; then out="$arg"; fi\n'
            '  prev="$arg"\n'
            "done\n"
            "cat > /dev/null\n"
            ': > "$out"\n'
        )
        with pytest.raises(SynthesisError, match="empty audio"):
            fake_piper.provider().synthesize(SynthesisRequest(text="x"))

    def test_timeout(self, fake_piper: SimpleNamespace) -> None:
        fake_piper.script.write_text("#!/bin/sh\ncat > /dev/null\nsleep 3\n")
        provider = fake_piper.provider(timeout_seconds=0.3)
        with pytest.raises(SynthesisError, match="timed out"):
            provider.synthesize(SynthesisRequest(text="x"))

    def test_binary_path_missing(self, fake_piper: SimpleNamespace, tmp_path: Path) -> None:
        provider = PiperProvider(
            {"binary": str(tmp_path / "no-such-piper"), "models_dir": str(fake_piper.models_dir)}
        )
        with pytest.raises(SynthesisError, match="binary not found"):
            provider.synthesize(SynthesisRequest(text="x"))


class TestVoices:
    def test_metadata_from_sidecar_json(self, fake_piper: SimpleNamespace) -> None:
        voices = {voice.id: voice for voice in fake_piper.provider().voices()}
        assert set(voices) == {"en_US-alpha", "it_IT-beta"}
        alpha = voices["en_US-alpha"]
        assert alpha.language == "en_US"
        assert alpha.name == "alpha"
        assert "medium" in (alpha.description or "")
        # model without sidecar json still gets a usable entry
        beta = voices["it_IT-beta"]
        assert beta.name == "it_IT-beta"
        assert beta.language is None

    def test_corrupt_sidecar_is_ignored(self, fake_piper: SimpleNamespace) -> None:
        (fake_piper.models_dir / "it_IT-beta.onnx.json").write_text("{broken json")
        voices = {voice.id: voice for voice in fake_piper.provider().voices()}
        assert voices["it_IT-beta"].name == "it_IT-beta"

    def test_empty_dir(self, fake_piper: SimpleNamespace, tmp_path: Path) -> None:
        provider = PiperProvider({"models_dir": str(tmp_path / "missing")})
        assert provider.voices() == []
