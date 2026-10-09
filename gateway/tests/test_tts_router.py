import asyncio

import httpx
import pytest

from switchboard.profiles import Profile, ProfileRegistry
from switchboard.tts import HEALTH_TTL_S, TtsRouter
from switchboard.tts.base import SynthesizedAudio
from switchboard.tts.chatterbox import ChatterboxBackend

PROFILE = Profile(display_name="Phil", model="phil", voice="en_GB-alan-low", chatterbox_voice="phil_ref")


class FakeBackend:
    def __init__(self, name, rate, ready=True, fail_on=()):
        self.name = name
        self.rate = rate
        self.ready = ready
        self.fail_on = set(fail_on)  # 1-based call numbers that raise
        self.calls = 0
        self.health_checks = 0

    async def synthesize(self, text, profile):
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError(f"{self.name} boom")
        return SynthesizedAudio(audio=text.encode(), rate=self.rate, width=2, channels=1)

    async def is_ready(self):
        self.health_checks += 1
        return self.ready


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def run(coro):
    return asyncio.run(coro)


def test_uses_preferred_backend_when_ready():
    chatter, piper = FakeBackend("chatterbox", 24000), FakeBackend("piper", 22050)
    router = TtsRouter(chatter, piper, clock=Clock())

    async def go():
        voice = await router.for_answer(PROFILE)
        return await voice.synthesize("hello")

    assert run(go()).rate == 24000
    assert chatter.calls == 1 and piper.calls == 0


def test_uses_piper_while_preferred_is_not_ready_and_caches_the_check():
    chatter, piper = FakeBackend("chatterbox", 24000, ready=False), FakeBackend("piper", 22050)
    clock = Clock()
    router = TtsRouter(chatter, piper, clock=clock)

    async def go():
        for _ in range(3):
            voice = await router.for_answer(PROFILE)
            assert (await voice.synthesize("x")).rate == 22050
        return chatter.health_checks

    assert run(go()) == 1  # not re-probed on every answer

    chatter.ready = True
    clock.now += HEALTH_TTL_S + 1  # cache expires -> picks the service up once it is ready

    async def later():
        voice = await router.for_answer(PROFILE)
        return (await voice.synthesize("x")).rate

    assert run(later()) == 24000


def test_first_chunk_failure_falls_back_for_the_whole_answer():
    chatter, piper = FakeBackend("chatterbox", 24000, fail_on={1}), FakeBackend("piper", 22050)
    router = TtsRouter(chatter, piper, clock=Clock())

    async def go():
        voice = await router.for_answer(PROFILE)
        return [(await voice.synthesize(t)).rate for t in ("one", "two", "three")]

    assert run(go()) == [22050, 22050, 22050]  # never mixes 24 kHz and 22.05 kHz in one answer
    assert chatter.calls == 1  # not retried for later chunks


def test_failed_backend_is_skipped_by_the_next_answers_for_a_while():
    chatter, piper = FakeBackend("chatterbox", 24000, fail_on={1}), FakeBackend("piper", 22050)
    clock = Clock()
    router = TtsRouter(chatter, piper, clock=clock)

    async def answer():
        voice = await router.for_answer(PROFILE)
        return (await voice.synthesize("x")).rate

    assert run(answer()) == 22050  # fails, falls back
    assert run(answer()) == 22050  # skipped without trying again
    assert chatter.calls == 1


def test_failure_mid_answer_is_a_real_error_not_a_silent_switch():
    chatter, piper = FakeBackend("chatterbox", 24000, fail_on={2}), FakeBackend("piper", 22050)
    router = TtsRouter(chatter, piper, clock=Clock())

    async def go():
        voice = await router.for_answer(PROFILE)
        await voice.synthesize("first")
        await voice.synthesize("second")

    with pytest.raises(RuntimeError):
        run(go())
    assert piper.calls == 0


def test_piper_only_mode_never_probes():
    piper = FakeBackend("piper", 22050)
    router = TtsRouter(piper, piper, clock=Clock())

    async def go():
        voice = await router.for_answer(PROFILE)
        return await voice.synthesize("x")

    assert run(go()).rate == 22050
    assert piper.health_checks == 0


def test_chatterbox_client_sends_the_personas_voice_and_parses_the_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/synthesize":
            seen["json"] = request.read()
            return httpx.Response(200, content=b"\x01\x00\x02\x00",
                                  headers={"X-Sample-Rate": "24000", "X-Bits": "16", "X-Channels": "1"})
        return httpx.Response(200, json={"status": "ready"})

    backend = ChatterboxBackend(base_url="http://tts:8000")
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    audio = run(backend.synthesize("hello", PROFILE))
    assert audio == SynthesizedAudio(audio=b"\x01\x00\x02\x00", rate=24000, width=2, channels=1)
    assert b'"voice":"phil_ref"' in seen["json"].replace(b" ", b"")
    assert run(backend.is_ready()) is True


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"status": "warming"}),
    httpx.Response(200, json={"status": "loading"}),
    httpx.Response(503),
])
def test_chatterbox_is_only_ready_when_fully_warmed(response):
    backend = ChatterboxBackend(base_url="http://tts:8000")
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response))
    assert run(backend.is_ready()) is False


def test_chatterbox_unreachable_is_not_ready():
    def refuse(request):
        raise httpx.ConnectError("refused")

    backend = ChatterboxBackend(base_url="http://tts:8000")
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    assert run(backend.is_ready()) is False


def test_voice_mapping_for_both_backends():
    class FakeOwui:
        async def list_models(self):
            return [{"id": "phil", "name": "Phil", "preset": True}, {"id": "aida", "name": "Aida", "preset": True}]

        async def get_model_avatar(self, model_id):
            return None

    registry = ProfileRegistry({
        "default_voice": "en_US-lessac-medium",
        "overrides": {"phil": "en_GB-alan-low"},
        "chatterbox": {"default_voice": "default", "overrides": {"phil": "phil_ref"}},
    })
    run(registry.refresh(FakeOwui()))
    assert (registry.get("phil").voice, registry.get("phil").chatterbox_voice) == ("en_GB-alan-low", "phil_ref")
    assert (registry.get("aida").voice, registry.get("aida").chatterbox_voice) == ("en_US-lessac-medium", "default")


def test_voices_yaml_without_a_chatterbox_section_still_loads():
    registry = ProfileRegistry({"default_voice": "x"})
    assert registry._chatterbox_default == "default"
