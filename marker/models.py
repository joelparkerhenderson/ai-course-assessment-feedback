"""Discover installed Ollama models and select the ones to mark with.

Users can choose models three ways, in any combination:

* **Exact tags**: ``--model gpt-oss:20b --model muse-glimmer:30b-mlx``.
* **Families**: ``--family gpt-oss --family "Muse Glimmer"``. Family names are
  matched loosely (case, spaces, hyphens and underscores are ignored), and
  common aliases such as "ChatGPT-OSS" map to ``gpt-oss``.
* **Sizes**: ``--size 20b --size 120b`` narrows the family selection.

**Mac-optimised builds.** Ollama can serve MLX builds of models, which run
natively on Apple Silicon. They are tagged ``-mlx`` and stored as
safetensors. ``--mlx`` controls them when selecting by family or size:

====== ==============================================================
policy behaviour
====== ==============================================================
auto   ``prefer`` on Apple Silicon, ``never`` elsewhere (the default)
prefer for each family and size, use the MLX build if one is installed
only   MLX builds only
never  never MLX builds
any    every matching build
====== ==============================================================

Exact ``--model`` tags are always used exactly as given; the MLX policy
doesn't filter them.

**Reproducibility caveat.** In testing with Ollama 0.35.1, MLX builds did
*not* honour ``options.seed``: the same request gave different outputs. GGUF
builds were deterministic. MLX builds trade reproducibility for speed on
Apple Silicon, and ``marker grade`` warns when it uses one.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from typing import Any

#: Default model when nothing is selected.
DEFAULT_MODEL = "gpt-oss:20b"

#: ``--mlx`` policy names.
MLX_POLICIES = ("auto", "prefer", "only", "never", "any")

#: Loose family names that users may type, mapped to Ollama family names.
#: Keys and values are "keys" in the :func:`family_key` sense (lower case, alphanumerics only).
FAMILY_ALIASES: dict[str, str] = {
    "chatgptoss": "gptoss",
    "openaigptoss": "gptoss",
    "gptopensource": "gptoss",
}


@dataclass(frozen=True)
class ModelInfo:
    """One installed Ollama model, with the facts used for selection.

    Attributes:
        name: Full Ollama name, e.g. ``"muse-glimmer:30b-mlx"``.
        family: Name before the colon, without any namespace, e.g. ``"muse-glimmer"``.
        tag: Tag after the colon, e.g. ``"30b-mlx"``. ``"latest"`` if none.
        size: Parameter size such as ``"30b"``, from the tag or else from Ollama's
            reported parameter count. None if unknown.
        mlx: True for an MLX (Apple Silicon) build.
        format: Weights format reported by Ollama, e.g. ``"gguf"`` or ``"safetensors"``.
        quantization: Quantisation level, e.g. ``"MXFP4"``, ``"Q4_K_M"`` or ``"nvfp4"``.
        parameter_size: Ollama's reported parameter count, e.g. ``"32.3B"``.
        digest: Content digest of the weights.
    """

    name: str
    family: str
    tag: str
    size: str | None
    mlx: bool
    format: str
    quantization: str
    parameter_size: str
    digest: str


def family_key(name: str) -> str:
    """Return a loose key for comparing family names, with aliases applied.

    ``"ChatGPT-OSS"``, ``"gpt_oss"`` and ``"GPT OSS"`` all give ``"gptoss"``.
    """
    key = re.sub(r"[^a-z0-9.]", "", name.lower())
    return FAMILY_ALIASES.get(key, key)


def normalise_size(size: str) -> str:
    """Normalise a size string: ``"20B"``, ``"20"`` and ``" 20b "`` all give ``"20b"``."""
    s = size.strip().lower()
    return s if s.endswith("b") else f"{s}b"


def split_name(name: str) -> tuple[str, str]:
    """Split ``"[registry/][namespace/]family[:tag]"`` into ``(family, tag)``."""
    base, _, tag = name.partition(":")
    return base.rsplit("/", 1)[-1], tag or "latest"


def is_apple_silicon() -> bool:
    """True when running natively on an Apple Silicon Mac, where MLX builds run."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def model_info(raw: dict[str, Any]) -> ModelInfo:
    """Build a :class:`ModelInfo` from one entry of Ollama's ``/api/tags`` list."""
    name = str(raw.get("name") or raw.get("model") or "")
    family, tag = split_name(name)
    details = raw.get("details") or {}
    fmt = str(details.get("format", ""))
    parameter_size = str(details.get("parameter_size", ""))

    # Prefer the size in the tag ("27b-mlx" gives "27b"); fall back to the reported count ("23.6B" gives "23.6b").
    m = re.match(r"(\d+(?:\.\d+)?)b(?![a-z])", tag.lower())
    size = f"{m.group(1)}b" if m else (normalise_size(parameter_size) if parameter_size else None)

    return ModelInfo(
        name=name,
        family=family,
        tag=tag,
        size=size,
        mlx="mlx" in tag.lower().split("-") or fmt == "safetensors",
        format=fmt,
        quantization=str(details.get("quantization_level", "")),
        parameter_size=parameter_size,
        digest=str(raw.get("digest", "")),
    )


def _size_matches(info: ModelInfo, wanted: str) -> bool:
    """True if a model's size equals ``wanted``, allowing 10% tolerance for reported counts like 23.6B."""
    if info.size is None:
        return False
    if info.size == wanted:
        return True
    try:
        have, want = float(info.size[:-1]), float(wanted[:-1])
    except ValueError:
        return False
    return abs(have - want) <= 0.1 * want


def resolve_mlx_policy(policy: str, apple_silicon: bool | None = None) -> str:
    """Turn ``auto`` into ``prefer`` or ``never``, depending on the hardware."""
    if policy not in MLX_POLICIES:
        raise ValueError(f"unknown --mlx policy {policy!r}; choose from {', '.join(MLX_POLICIES)}")
    if policy != "auto":
        return policy
    if apple_silicon is None:
        apple_silicon = is_apple_silicon()
    return "prefer" if apple_silicon else "never"


def select_models(
    installed: list[ModelInfo],
    *,
    models: list[str] | None = None,
    families: list[str] | None = None,
    sizes: list[str] | None = None,
    mlx: str = "auto",
    apple_silicon: bool | None = None,
) -> list[str]:
    """Choose which installed models to mark with.

    Args:
        installed: Installed models (see :func:`model_info`).
        models: Exact model tags, used as given.
        families: Family names to match loosely. Each is filtered by ``sizes``
            and the MLX policy.
        sizes: Parameter sizes to keep, e.g. ``["20b", "120b"]``. If no
            families are given, sizes filter *all* installed models.
        mlx: MLX policy (see module docstring).
        apple_silicon: Override hardware detection (for tests).

    Returns:
        Model names in a stable order: exact tags first (in the order given),
        then family matches sorted by family and size. Duplicates are removed.
        With no selection at all, returns ``[DEFAULT_MODEL]``.

    Raises:
        ValueError: An exact tag isn't installed, or a family/size filter
            matches nothing. The message lists what *is* installed.
    """
    models = [m for m in (models or []) if m]
    families = [f for f in (families or []) if f]
    sizes = [normalise_size(s) for s in (sizes or []) if s]
    policy = resolve_mlx_policy(mlx, apple_silicon)
    installed_names = {i.name for i in installed}
    available = ", ".join(sorted(installed_names)) or "(none)"

    chosen: list[str] = []
    for m in models:
        name = m if ":" in m else f"{m}:latest"
        if name not in installed_names:
            raise ValueError(f"model {m!r} is not installed (installed: {available}); run: ollama pull {m}")
        chosen.append(name)

    if families or sizes:
        keys = {family_key(f) for f in families}
        pool = [i for i in installed if not keys or family_key(i.family) in keys]
        if sizes:
            pool = [i for i in pool if any(_size_matches(i, s) for s in sizes)]

        if policy == "only":
            pool = [i for i in pool if i.mlx]
        elif policy == "never":
            pool = [i for i in pool if not i.mlx]
        elif policy == "prefer":
            # For each (family, size) keep only MLX builds if any are installed.
            groups_with_mlx = {(family_key(i.family), i.size) for i in pool if i.mlx}
            pool = [i for i in pool if i.mlx or (family_key(i.family), i.size) not in groups_with_mlx]

        if not pool:
            raise ValueError(
                f"no installed model matches family={families or 'any'} size={sizes or 'any'} "
                f"mlx={policy} (installed: {available})"
            )

        def sort_key(i: ModelInfo) -> tuple[str, float, str]:
            """Sort by family, then numeric size (unknown last), then name."""
            try:
                n = float(i.size[:-1]) if i.size else float("inf")
            except ValueError:
                n = float("inf")
            return (i.family, n, i.name)

        chosen.extend(i.name for i in sorted(pool, key=sort_key))

    if not chosen:
        chosen = [DEFAULT_MODEL]
    return list(dict.fromkeys(chosen))  # dedupe, keep order


#: Reasoning effort levels that gpt-oss (and its fine-tunes) accept.
THINK_LEVELS = ("low", "medium", "high")

#: Every ``--think`` value: an effort level, or ``on``/``off`` for an explicit switch.
THINK_CHOICES = (*THINK_LEVELS, "on", "off")

#: The default ``--think`` value.
DEFAULT_THINK = "high"


def parse_think(values: list[str] | None, default: str = DEFAULT_THINK) -> tuple[str, dict[str, str]]:
    """Parse ``--think`` values into a default setting and per-model overrides.

    Each value is either a setting (``low``, ``medium``, ``high``, ``on`` or
    ``off``), which becomes the default for every model, or ``MODEL=SETTING``,
    which applies to one model. ``MODEL`` is an exact tag (``gpt-oss:120b``)
    or a family (``gpt-oss``, matched loosely like ``--family``). Values may be
    repeated or comma-separated: ``--think high,gpt-oss:120b=low``.

    Returns:
        ``(default, overrides)``, where ``overrides`` maps the model or family text to its setting.

    Raises:
        ValueError: An unknown setting, or an empty model name.
    """
    overrides: dict[str, str] = {}
    for raw in values or []:
        for item in (v.strip() for v in raw.split(",")):
            if not item:
                continue
            target, sep, setting = item.rpartition("=")
            setting = setting.strip().lower()
            if setting not in THINK_CHOICES:
                raise ValueError(f"unknown --think setting {setting!r}; choose from {', '.join(THINK_CHOICES)}")
            if not sep:
                default = setting
            elif not target.strip():
                raise ValueError(f"--think {item!r}: missing model name before '='")
            else:
                overrides[target.strip()] = setting
    return default, overrides


def think_setting(model: str, default: str, overrides: dict[str, str]) -> str:
    """Pick the ``--think`` setting for one model: an exact-tag override, then a family override, then the default."""
    if model in overrides:
        return overrides[model]
    for target, setting in overrides.items():
        if ":" not in target and matches_target(model, target):
            return setting
    return default


def matches_target(model: str, target: str) -> bool:
    """True if a ``--think`` target names this model: its exact tag, or (without a ``:``) its family."""
    if ":" in target:
        return model == target
    return family_key(split_name(model)[0]) == family_key(target)


def think_for(model: str, level: str, capabilities: list[str] | None) -> str | bool | None:
    """Work out the ``think`` request value for a model from its ``--think`` setting.

    * gpt-oss and its fine-tunes (e.g. ``gpt-oss-safeguard``) always reason and
      take an effort *level*. ``on`` means ``medium``. Their reasoning can't be
      switched off, so ``off`` means ``low``, the least reasoning available.
    * Other models that report the ``thinking`` capability take a boolean:
      ``off`` sends False (reasoning disabled); any other setting sends True.
      They have no effort levels, so ``low``, ``medium`` and ``high`` all mean on.
    * Models without that capability, or with unknown capabilities, get None
      (``think`` is not sent).

    Args:
        model: Model name.
        level: The ``--think`` setting for this model (one of :data:`THINK_CHOICES`).
        capabilities: Capabilities from ``/api/show``, or None if unknown.

    Raises:
        ValueError: ``level`` is not one of :data:`THINK_CHOICES`.
    """
    if level not in THINK_CHOICES:
        raise ValueError(f"unknown --think setting {level!r}; choose from {', '.join(THINK_CHOICES)}")
    if is_gpt_oss(model):
        return {"on": "medium", "off": "low"}.get(level, level)
    if capabilities and "thinking" in capabilities:
        return level != "off"
    return None


def is_gpt_oss(model: str) -> bool:
    """True for gpt-oss and its fine-tunes, which take a reasoning effort level and can't switch reasoning off."""
    return family_key(split_name(model)[0]).startswith("gptoss")


def slug(model: str) -> str:
    """Return a file-system-safe directory name for a model, e.g. ``"muse-glimmer_30b-mlx"``."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("_")
