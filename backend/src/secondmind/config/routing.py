"""Per-step model routing from ``config/models.yaml`` (FR-14.2).

Each agent step names a provider, a model, an optional fallback and a timeout. The environment
can override a step (``MODEL_ANSWER=openai:gpt-6-sol``, ``MODEL_ANSWER_FALLBACK=none``), so
switching a step is a config change only. Provider mode decides what happens to a step whose
provider has no credentials: ``auto`` swaps in the fake provider, ``live`` refuses to start,
``fake`` routes every step to the fake provider.

The ``picker`` lists the models a person can pick for a turn. A pick replaces the model of
every chat step in that turn (embeddings keep their route). Where the picked model's provider
has no credentials, ``auto`` and ``fake`` let the fake provider stand in for it (priced as the
model it stands in for); ``live`` marks it unavailable.
"""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from secondmind.core import ConfigError


class Step(StrEnum):
    INTENT = "intent"
    EXTRACT = "extract"
    RESOLVE = "resolve"
    ENRICH = "enrich"
    RECONCILE = "reconcile"
    PLAN = "plan"
    ANSWER = "answer"
    JUDGE = "judge"
    EMBED = "embed"


class StepKind(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"


STEP_KINDS: dict[Step, StepKind] = {
    step: (StepKind.EMBEDDING if step is Step.EMBED else StepKind.CHAT) for step in Step
}

FAKE_PROVIDER = "fake"
FAKE_MODELS: dict[StepKind, str] = {StepKind.CHAT: "fake-chat", StepKind.EMBEDDING: "fake-embed"}

Effort = Literal["low", "medium", "high"]


class ModelRef(BaseModel):
    """A provider + model pair, written ``provider:model``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> "ModelRef":
        provider, sep, model = value.partition(":")
        if not sep or not provider.strip() or not model.strip():
            raise ValueError(f"expected '<provider>:<model>', got {value!r}")
        return cls(provider=provider.strip(), model=model.strip())

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["anthropic", "openai", "fake"]
    label: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    base_url_env: str | None = None


class PickerConfig(BaseModel):
    """Models a person can pick, ``provider:model`` to display name, and the default pick."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: str
    models: dict[str, str]

    @field_validator("default")
    @classmethod
    def _valid_default(cls, value: str) -> str:
        ModelRef.parse(value)
        return value

    @field_validator("models")
    @classmethod
    def _valid_models(cls, value: dict[str, str]) -> dict[str, str]:
        for ref in value:
            ModelRef.parse(ref)
        return value


class StepConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    fallback: str | None = None
    timeout_s: Annotated[float, Field(gt=0, le=600)]
    max_output_tokens: Annotated[int, Field(ge=1, le=128_000)] = 1024
    effort: Effort | None = None
    prompt: str | None = None

    @field_validator("fallback")
    @classmethod
    def _valid_fallback(cls, value: str | None) -> str | None:
        if value is not None:
            ModelRef.parse(value)
        return value


class RoutingFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    providers: dict[str, ProviderConfig]
    steps: dict[Step, StepConfig]
    picker: PickerConfig | None = None


class ResolvedProvider(BaseModel):
    """A provider with its credentials looked up (secrets are never part of the config hash)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: Literal["anthropic", "openai", "fake"]
    label: str | None = None
    base_url: str | None = None
    has_credentials: bool

    api_key_env: str | None = Field(default=None, exclude=True)


class ResolvedRoute(BaseModel):
    """What a step actually runs on after env overrides and provider mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: Step
    kind: StepKind
    primary: ModelRef
    fallback: ModelRef | None
    timeout_s: float
    max_output_tokens: int
    effort: Effort | None
    prompt: str | None
    substituted: list[str] = []


class ModelChoice(BaseModel):
    """One model in the picker, settled against credentials and provider mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ModelRef
    label: str
    provider_label: str
    served_by: ModelRef
    available: bool

    @property
    def simulated(self) -> bool:
        """The fake provider stands in for this model (no credentials, or fake mode)."""
        return self.served_by != self.ref


class Routing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["auto", "live", "fake"]
    providers: dict[str, ResolvedProvider]
    routes: dict[Step, ResolvedRoute]
    choices: list[ModelChoice] = []
    default_choice: ModelRef | None = None

    def route(self, step: Step) -> ResolvedRoute:
        return self.routes[step]

    def refs(self) -> set[ModelRef]:
        """Every model a call can go to: routes, fallbacks and available picks."""
        out: set[ModelRef] = set()
        for route in self.routes.values():
            out.add(route.primary)
            if route.fallback:
                out.add(route.fallback)
        out.update(c.served_by for c in self.choices if c.available)
        return out

    def choice(self, ref: ModelRef) -> ModelChoice | None:
        return next((c for c in self.choices if c.ref == ref), None)

    def with_pick(self, ref: ModelRef) -> "Routing":
        """This routing with every chat step on the picked model. The step's fallback stays,
        unless it is the same model or the pick is simulated."""
        choice = self.choice(ref)
        if choice is None:
            raise ValueError(f"{ref} is not a model you can pick")
        if not choice.available:
            raise ValueError(f"{ref} can't be used: its provider has no credentials")
        served = choice.served_by
        routes: dict[Step, ResolvedRoute] = {}
        for step, route in self.routes.items():
            if route.kind is not StepKind.CHAT:
                routes[step] = route
                continue
            fallback = route.fallback
            if choice.simulated or fallback == served:
                fallback = None
            routes[step] = route.model_copy(
                update={"primary": served, "fallback": fallback, "substituted": []}
            )
        return self.model_copy(update={"routes": routes})

    @property
    def substitutions(self) -> list[str]:
        return [s for r in self.routes.values() for s in r.substituted]


def read_routing_file(path: Path) -> RoutingFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = RoutingFile.model_validate(raw)
    except FileNotFoundError as exc:
        raise ConfigError(f"model routing file not found: {path}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid model routing file {path}:\n{exc}") from exc
    missing = [s.value for s in Step if s not in parsed.steps]
    if missing:
        raise ConfigError(f"{path}: steps missing a route: {', '.join(missing)}")
    if FAKE_PROVIDER not in parsed.providers:
        raise ConfigError(f"{path}: the '{FAKE_PROVIDER}' provider must be declared")
    return parsed


def resolve_routing(
    file: RoutingFile,
    environ: Mapping[str, str],
    mode: Literal["auto", "live", "fake"],
) -> Routing:
    """Apply ``MODEL_<STEP>[_FALLBACK]`` overrides and the provider mode."""
    providers = _resolve_providers(file, environ)
    overrides = _read_overrides(environ, providers)
    routes: dict[Step, ResolvedRoute] = {}
    missing_creds: list[str] = []

    for step in Step:
        cfg = file.steps[step]
        kind = STEP_KINDS[step]
        primary = overrides.get((step, "primary")) or ModelRef(
            provider=cfg.provider, model=cfg.model
        )
        if (step, "fallback") in overrides:
            fallback = overrides[(step, "fallback")]
        else:
            fallback = ModelRef.parse(cfg.fallback) if cfg.fallback else None
        for ref in (primary, fallback):
            if ref is not None and ref.provider not in providers:
                raise ConfigError(
                    f"step {step.value!r} uses unknown provider {ref.provider!r} "
                    f"(declared: {', '.join(sorted(providers))})"
                )

        primary, fallback, substituted = _apply_mode(
            step,
            primary=primary,
            fallback=fallback,
            providers=providers,
            mode=mode,
            missing_creds=missing_creds,
        )

        routes[step] = ResolvedRoute(
            step=step,
            kind=kind,
            primary=primary,
            fallback=fallback,
            timeout_s=cfg.timeout_s,
            max_output_tokens=cfg.max_output_tokens,
            effort=cfg.effort,
            prompt=cfg.prompt,
            substituted=substituted,
        )

    if missing_creds:
        raise ConfigError(
            "MODEL_PROVIDER_MODE=live but provider credentials are missing:\n  "
            + "\n  ".join(sorted(set(missing_creds)))
        )
    choices, default = _resolve_picker(file.picker, providers, mode)
    return Routing(
        mode=mode, providers=providers, routes=routes, choices=choices, default_choice=default
    )


def _resolve_picker(
    picker: PickerConfig | None,
    providers: Mapping[str, ResolvedProvider],
    mode: Literal["auto", "live", "fake"],
) -> tuple[list[ModelChoice], ModelRef | None]:
    if picker is None:
        return [], None
    choices: list[ModelChoice] = []
    for raw, label in picker.models.items():
        ref = ModelRef.parse(raw)
        provider = providers.get(ref.provider)
        if provider is None:
            raise ConfigError(
                f"picker model {ref} uses unknown provider {ref.provider!r} "
                f"(declared: {', '.join(sorted(providers))})"
            )
        if ref.provider == FAKE_PROVIDER:
            raise ConfigError(f"picker model {ref}: the fake provider can't be picked")
        stand_in = ModelRef(provider=FAKE_PROVIDER, model=ref.model)
        if mode == "fake":
            served, available = stand_in, True
        elif provider.has_credentials:
            served, available = ref, True
        else:
            served, available = (stand_in, True) if mode == "auto" else (ref, False)
        choices.append(
            ModelChoice(
                ref=ref,
                label=label,
                provider_label=provider.label or provider.name,
                served_by=served,
                available=available,
            )
        )
    default = ModelRef.parse(picker.default)
    if not any(c.ref == default for c in choices):
        raise ConfigError(f"picker default {default} is not one of the picker's models")
    return choices, default


def _apply_mode(
    step: Step,
    *,
    primary: ModelRef,
    fallback: ModelRef | None,
    providers: Mapping[str, "ResolvedProvider"],
    mode: Literal["auto", "live", "fake"],
    missing_creds: list[str],
) -> tuple[ModelRef, ModelRef | None, list[str]]:
    """Settle a step's refs against credentials. Returns (primary, fallback, notes)."""
    fake_ref = ModelRef(provider=FAKE_PROVIDER, model=FAKE_MODELS[STEP_KINDS[step]])
    notes: list[str] = []

    def has_creds(ref: ModelRef) -> bool:
        return providers[ref.provider].has_credentials

    if mode == "fake":
        return fake_ref, None, notes
    if mode == "live":
        for role, ref in (("primary", primary), ("fallback", fallback)):
            if ref is not None and not has_creds(ref):
                env_name = providers[ref.provider].api_key_env
                missing_creds.append(f"{env_name} (step {step.value} {role}: {ref})")
        return primary, fallback, notes
    if not has_creds(primary):
        replacement = fallback if fallback is not None and has_creds(fallback) else fake_ref
        notes.append(f"{step.value}: {primary} has no credentials; using {replacement}")
        return replacement, None, notes
    if fallback is not None and not has_creds(fallback):
        notes.append(f"{step.value}: fallback {fallback} has no credentials; dropped")
        return primary, None, notes
    return primary, fallback, notes


def _resolve_providers(
    file: RoutingFile, environ: Mapping[str, str]
) -> dict[str, ResolvedProvider]:
    out: dict[str, ResolvedProvider] = {}
    for name, cfg in file.providers.items():
        base_url = cfg.base_url
        if cfg.base_url_env:
            base_url = environ.get(cfg.base_url_env, "").strip() or base_url
        if cfg.kind == "fake":
            has_creds = True
        elif cfg.api_key_env:
            has_creds = bool(environ.get(cfg.api_key_env, "").strip())
        else:
            # A keyless OpenAI-compatible endpoint (e.g. local vLLM) needs only a base URL.
            has_creds = cfg.kind == "openai" and base_url is not None
        out[name] = ResolvedProvider(
            name=name,
            kind=cfg.kind,
            label=cfg.label,
            base_url=base_url,
            has_credentials=has_creds,
            api_key_env=cfg.api_key_env,
        )
    return out


def _read_overrides(
    environ: Mapping[str, str], providers: Mapping[str, ResolvedProvider]
) -> dict[tuple[Step, str], ModelRef | None]:
    overrides: dict[tuple[Step, str], ModelRef | None] = {}
    steps = {s.value.upper(): s for s in Step}
    for key, raw in environ.items():
        upper = key.upper()
        if not upper.startswith("MODEL_") or upper == "MODEL_PROVIDER_MODE":
            continue
        value = raw.strip()
        if not value:
            continue
        name = upper.removeprefix("MODEL_")
        role = "primary"
        if name.endswith("_FALLBACK"):
            name, role = name.removesuffix("_FALLBACK"), "fallback"
        step = steps.get(name)
        if step is None:
            raise ConfigError(f"{upper}: unknown step {name.lower()!r}; steps: {', '.join(steps)}")
        if role == "fallback" and value.lower() == "none":
            overrides[(step, role)] = None
            continue
        try:
            ref = ModelRef.parse(value)
        except ValueError as exc:
            raise ConfigError(f"{upper}: {exc}") from exc
        if ref.provider not in providers:
            raise ConfigError(
                f"{upper}: unknown provider {ref.provider!r} "
                f"(declared: {', '.join(sorted(providers))})"
            )
        overrides[(step, role)] = ref
    return overrides
